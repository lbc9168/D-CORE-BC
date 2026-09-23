"""S2 阶段：项目识别+基本信息；in-memory 校验 category/stage 枚举与名称含中文。"""
import json
import logging
import re
import bri_enums as enums
from s01b_project_extractor.capacity_parser import parse_capacity_mw
from s01b_project_extractor.stages.entity_response import EntityResponseError
from s01b_project_extractor.stages.capacity_evidence import apply_capacity_evidence
from s01b_project_extractor.energy_taxonomy_v2 import (
    GENERATION_SOURCES,
    STORAGE_STATUSES,
    classify_energy,
    extract_energy_facts,
    normalize_generation_sources,
    parse_storage_measurements,
)
from s01b_project_extractor.txmf_spec_parser import parse_mf_atomic, parse_tx_atomic

logger = logging.getLogger(__name__)

# 主 CJK 区块（U+4E00–U+9FFF），用于「名称是否含中文」判断已足够
_CHINESE_RE = re.compile(r"[一-鿿]")

_PROJECT_NAME_EN_CONFUSABLES = {
    "海四达": (("Hithium",), "Haisida"),
    "天鹏": (("Tianneng",), "Tianpeng"),
}


def _repair_confusable_project_name_en(ci: dict) -> None:
    """按已核实的中英文易混淆表修复项目英文名中的企业词元。"""
    name_cn = str(ci.get("Project Name", "") or "")
    name_en = str(ci.get("Project Name (EN)", "") or "")
    repaired = name_en
    for cn_token, (forbidden_tokens, canonical_en) in _PROJECT_NAME_EN_CONFUSABLES.items():
        if cn_token not in name_cn:
            continue
        for forbidden in forbidden_tokens:
            repaired = re.sub(re.escape(forbidden), canonical_en, repaired, flags=re.IGNORECASE)
    if repaired != name_en:
        logger.warning("项目英文名易混淆词元已修正: %s → %s", name_en, repaired)
        ci["Project Name (EN)"] = repaired
        ci["name_en_quality"] = "deterministic_corrected"


def _apply_capacity_post_process(ci: dict) -> None:
    """S2 输出后处理：解析 Capacity (MW) 原文短语 → 数值，并将解析结果写入 common_info。

    写入三个键：
      capacity_mw_parsed     (float|None)  确定性解析结果，供 eval/写库层直接消费
      capacity_mw_raw_phrase (str)         LLM 逐字原文，供 capacity_mw_raw 列落库
      capacity_details_note  (str|None)    产能/输送类说明，供 capacity_details 列合并
    """
    raw_phrase = str(ci.get("Capacity (MW)", "") or "").strip()
    category = str(ci.get("Category", "") or "").strip()
    mw_val, details_note = parse_capacity_mw(raw_phrase or None, category=category or None)
    ci["capacity_mw_parsed"] = mw_val
    ci["capacity_mw_raw_phrase"] = raw_phrase
    ci["capacity_details_note"] = details_note


def _apply_energy_facts_post_process(ci: dict) -> None:
    """规范化 S2 原子事实，并用 v2 规则确定 category、标签和 confirmed 储能容量。"""
    if str(ci.get("Category", "") or "").strip() in {"输变电建设", "新能源制造业"}:
        ci["Generation Sources"] = []
        ci["Storage Status"] = "not_mentioned"
        ci["Storage Power"] = ""
        ci["Storage Energy"] = ""
        ci["generation_sources_json"] = "[]"
        ci["storage_status_normalized"] = "not_mentioned"
        ci["storage_power_mw_raw_phrase"] = ""
        ci["storage_energy_mwh_raw_phrase"] = ""
        ci["storage_power_mw_parsed"] = None
        ci["storage_energy_mwh_parsed"] = None
        ci["energy_composition_evidence_normalized"] = ""
        ci["technology_tags_json"] = "[]"
        ci["energy_taxonomy_version"] = "energy_taxonomy_v2.1"
        return
    raw_sources = ci.get("Generation Sources", []) or []
    if isinstance(raw_sources, str):
        raw_sources = [part.strip() for part in re.split(r"[,，;；]", raw_sources) if part.strip()]
    try:
        sources = normalize_generation_sources(raw_sources)
    except ValueError:
        sources = tuple(
            source for source in GENERATION_SOURCES
            if source in {str(value).strip() for value in raw_sources}
        )
    status = str(ci.get("Storage Status", "not_mentioned") or "not_mentioned").strip()
    power_raw = str(ci.get("Storage Power", "") or "").strip()
    energy_raw = str(ci.get("Storage Energy", "") or "").strip()
    evidence = str(ci.get("Energy Composition Evidence", "") or "").strip()
    evidence_facts = extract_energy_facts(evidence)
    if status not in STORAGE_STATUSES:
        status = "unclear"
    if status == "confirmed" and evidence_facts.storage_status != "confirmed":
        status = evidence_facts.storage_status
    if status == "confirmed":
        storage_power_mw, storage_energy_mwh = parse_storage_measurements(power_raw, energy_raw)
    else:
        power_raw = ""
        energy_raw = ""
        storage_power_mw = None
        storage_energy_mwh = None
    classification = classify_energy(sources, status)
    if classification.proposed_category is not None:
        ci["Category"] = classification.proposed_category
    ci["generation_sources_json"] = json.dumps(sources, ensure_ascii=False)
    ci["storage_status_normalized"] = status
    ci["storage_power_mw_raw_phrase"] = power_raw
    ci["storage_energy_mwh_raw_phrase"] = energy_raw
    ci["storage_power_mw_parsed"] = storage_power_mw
    ci["storage_energy_mwh_parsed"] = storage_energy_mwh
    ci["energy_composition_evidence_normalized"] = evidence
    ci["technology_tags_json"] = json.dumps(classification.technology_tags, ensure_ascii=False)
    ci["energy_taxonomy_version"] = classification.policy_version


_TXMF_FIELDS = {
    "TX Voltage Evidence": "voltage_kv_raw_phrase",
    "TX Line Length Evidence": "line_length_km_raw_phrase",
    "TX Transmission Capacity Evidence": "transmission_capacity_mw_raw_phrase",
    "MF Production Capacity Evidence": "production_capacity_raw_phrase",
}


def _is_exact_surface_quote(value: str, title: str, content: str) -> bool:
    """判断证据是否完整连续地出现于标题或正文任一 surface。"""
    return not value or value in title or value in content


def _apply_txmf_post_process(ci: dict, title: str, content: str) -> None:
    """校验 TX/MF 证据并解析专用指标；非对应领域字段强制为空。"""
    category = str(ci.get("Category", "") or "").strip()
    errors: list[str] = []
    values: dict[str, str] = {}
    for source_key, target_key in _TXMF_FIELDS.items():
        value = str(ci.get(source_key, "") or "").strip()
        allowed = (category == "输变电建设" and source_key.startswith("TX ")) or (
            category == "新能源制造业" and source_key.startswith("MF ")
        )
        if value and not allowed:
            errors.append(f"{source_key} 不适用于 {category or '空分类'}")
            value = ""
        if value and not _is_exact_surface_quote(value, title, content):
            errors.append(f"{source_key} 不是标题或正文连续逐字证据")
            value = ""
        ci[source_key] = value
        ci[target_key] = value
        values[source_key] = value
    ci["_txmf_contract_errors"] = errors
    if category == "输变电建设":
        parsed = parse_tx_atomic(
            values["TX Voltage Evidence"],
            values["TX Line Length Evidence"],
            values["TX Transmission Capacity Evidence"],
        )
        ci["voltage_kv_parsed"] = parsed["voltage_kv"]
        ci["line_length_km_parsed"] = parsed["line_length_km"]
        ci["transmission_capacity_mw_parsed"] = parsed["transmission_capacity_mw"]
        ci["production_capacity_value_parsed"] = None
        ci["production_capacity_unit_normalized"] = None
        ci["Capacity (MW)"] = ""
    elif category == "新能源制造业":
        parsed = parse_mf_atomic(values["MF Production Capacity Evidence"])
        ci["production_capacity_value_parsed"] = parsed["production_capacity_value"]
        ci["production_capacity_unit_normalized"] = parsed["production_capacity_unit"]
        ci["voltage_kv_parsed"] = None
        ci["line_length_km_parsed"] = None
        ci["transmission_capacity_mw_parsed"] = None
        ci["Capacity (MW)"] = ""
    else:
        ci["voltage_kv_parsed"] = None
        ci["line_length_km_parsed"] = None
        ci["transmission_capacity_mw_parsed"] = None
        ci["production_capacity_value_parsed"] = None
        ci["production_capacity_unit_normalized"] = None


def run_s2(title: str, content: str, date: str, *, llm, retry_feedback: list[str] | None = None) -> list[dict]:
    """跑 S2，返回 projects 列表（每项含 common_info，容量字段已经过确定性后处理）。"""
    from s01b_project_extractor.prompts.s2_basic import build_s2_prompt
    prompt = build_s2_prompt(title, content, date)
    if retry_feedback:
        prompt += "\n上次输出未通过原文校验，请重新核对原文修正，勿编造证据或删除有效项目：\n" + "\n".join(retry_feedback)
    out = llm.complete(prompt, stage="s2", json_mode=True)
    if not isinstance(out, dict) or not isinstance(out.get("projects"), list):
        raise EntityResponseError("s2: projects must be an explicit list", out)
    projects = out["projects"]
    # Validate model-owned fields before normalization or SQLite binding. Evidence
    # objects are supported only by the explicitly declared Capacity Evidence field.
    for index, project in enumerate(projects):
        if not isinstance(project, dict) or not isinstance(project.get("common_info"), dict):
            raise EntityResponseError(f"s2: project {index} needs common_info object", out)
        if project.get("project_summary") is not None and not isinstance(project["project_summary"], str):
            raise EntityResponseError(f"s2: project {index} summary must be a string", out)
        for key, value in project["common_info"].items():
            if key == "Project Context":
                if not isinstance(value, dict) or not isinstance(value.get("source_name"), str) or not isinstance(value.get("context"), str) or type(value.get("is_current_event")) is not bool:
                    raise EntityResponseError("s2: invalid Project Context", out)
                if not isinstance(value.get("aliases"), list) or any(not isinstance(v, str) for v in value["aliases"]):
                    raise EntityResponseError("s2: Project Context aliases must be strings", out)
                continue
            if key == "Capacity Evidence" or key.startswith("_"):
                continue
            if key == "Generation Sources" and isinstance(value, list) and all(isinstance(s, str) for s in value):
                continue
            if isinstance(value, (dict, list)):
                raise EntityResponseError(f"s2: {key} must be a scalar, not {type(value).__name__}", out)
    # S2 不评估融资（融资由 S3 负责）；此处把缺省 Financing Model 置为「Unknown」（即「未确定」枚举），
    # 与多为 Unknown 的 gold 字段对齐；Phase 4 接入 S3 后会用真实融资值覆盖。
    for p in projects:
        ci = p.setdefault("common_info", {})
        context = ci.get("Project Context", {})
        if context.get("is_current_event") is False:
            p["in_scope"] = False
            p["out_of_scope_reason"] = "仅历史背景或未来组合，无本篇独立项目事件"
        if not str(ci.get("Financing Model", "")).strip():
            ci["Financing Model"] = "Unknown"
        # in_scope 出口：S2 未给时默认在范围内（不误排除）；给了则原样透传
        p.setdefault("in_scope", True)
        p.setdefault("out_of_scope_reason", "")
        _repair_confusable_project_name_en(ci)
        # 先确定能源分类，再按最终类别解析发电装机，确保储能容量永不混入 capacity_mw。
        _apply_energy_facts_post_process(ci)
        _apply_txmf_post_process(ci, title, content)
        _apply_capacity_post_process(ci)
        apply_capacity_evidence(ci, title, content)
    return projects


def classify_s2_validation(projects: list[dict]) -> tuple[list[str], list[str]]:
    """核心项目/分类歧义阻塞；已清空的不确定数值及名称格式只产生字段复核警告。"""
    errors, warnings = [], []
    valid_cat, valid_stage = set(enums.valid_categories()), set(enums.valid_stages())
    for i, p in enumerate(projects):
        ci = p.get("common_info", {}) or {}
        cat = str(ci.get("Category", "")).strip()
        if cat and cat not in valid_cat:
            errors.append(f"项目#{i} 分类不合法: {cat}")
        stage = str(ci.get("Project Stage", "")).strip()
        if stage and stage not in valid_stage:
            errors.append(f"项目#{i} 阶段不合法: {stage}")
        pname = str(ci.get("Project Name", "")).strip()
        if not pname:
            errors.append(f"项目#{i} 项目名称缺失，无法确认项目身份")
        elif not _CHINESE_RE.search(pname):
            warnings.append(f"项目#{i} 名称未含中文: {pname[:30]}")
        sources = ci.get("Generation Sources", []) or []
        if isinstance(sources, str):
            sources = [part.strip() for part in re.split(r"[,，;；]", sources) if part.strip()]
        invalid_sources = sorted(set(sources).difference(GENERATION_SOURCES))
        if invalid_sources:
            errors.append(f"项目#{i} 发电源不合法: {invalid_sources}")
        storage_status = str(ci.get("Storage Status", "not_mentioned") or "not_mentioned").strip()
        if storage_status not in STORAGE_STATUSES:
            errors.append(f"项目#{i} 储能状态不合法: {storage_status}")
        # Optional quantity evidence does not determine project identity. A cleared
        # value is a field warning even when its quote/anchor could not be located.
        nullable_generation = {
            "conflicting project totals", "conflicting turbine counts or unit capacities",
            "single-unit capacity lacks unambiguous complete project count",
            "capacity value absent from supplied source quotation",
            "capacity quotation has no bounded target-project context",
            "capacity belongs to an unbound project context",
        }
        for message in dict.fromkeys((ci.get("_txmf_contract_errors", []) or []) +
                                     (ci.get("capacity_evidence_errors", []) or [])):
            field = ("storage_power_mw_parsed" if message.startswith("Storage Power:") else
                     "storage_energy_mwh_parsed" if message.startswith("Storage Energy:") else
                     "capacity_mw_parsed" if message in nullable_generation or message.startswith("Capacity Evidence:") else
                     "voltage_kv_parsed" if message.startswith("TX Voltage Evidence") else
                     "line_length_km_parsed" if message.startswith("TX Line Length Evidence") else
                     "transmission_capacity_mw_parsed" if message.startswith("TX Transmission Capacity Evidence") else
                     "production_capacity_value_parsed" if message.startswith("MF Production Capacity") else None)
            safe_null = field is not None and field in ci and ci[field] is None
            (warnings if safe_null else errors).append(f"项目#{i} {pname}: {message}")
    return errors, warnings


def validate_s2(projects: list[dict]) -> list[str]:
    """兼容调用方的完整诊断；自动重试与发布门控使用阻塞项分类。"""
    errors, warnings = classify_s2_validation(projects)
    return errors + warnings


def validate_s2_blocking(projects: list[dict]) -> list[str]:
    return classify_s2_validation(projects)[0]
