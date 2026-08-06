"""flat_prediction：把 s01b 原始提取结果归一成含 legacy 与能源事实字段的文章级结果。"""

import json
import pathlib
import sys

# Root eval tests run without installing the nested src-layout package.
_ROOT = next(path for path in pathlib.Path(__file__).resolve().parents if (path / "bri_enums.py").exists())
_S01B_SRC = _ROOT / "s01b_project_extractor" / "src"
if str(_S01B_SRC) not in sys.path:
    sys.path.insert(0, str(_S01B_SRC))

from s01b_project_extractor.energy_taxonomy import classify_energy, normalize_generation_sources

_STATUS_MAP = {"valid_project": "valid", "no_chinese_participation": "no_chinese",
               "irrelevant": "irrelevant", "chinese_unnamed_participation": "chinese_unnamed"}

def _flat_project(proj: dict) -> dict:
    """单个项目 → 扁平 dict（字段 + entities 名字/角色对）；capacity_mw 取 stage 层已解析的数值。"""
    common = proj.get("common_info", {}) or {}
    entities = []
    for e in proj.get("chinese_entities", []) or []:
        # 来源名按⑨-2不变量重建：subsidiary 非空⇔原文名，空⇔implementing 即原文名。
        # name_f1 度量"是否抽对文章中的实体"，解析归一质量由 s01b 单测保证。
        sub = e.get("Subsidiary Name", "")
        name = sub or e.get("Implementing Chinese Entity", "")
        if name:
            entities.append({"name": name, "involvement_type": e.get("Type of involvement of Chinese Entities", "")})
    # capacity_mw_parsed 由 s2_basic._apply_capacity_post_process() 在 stage 层注入（确定性解析结果）；
    # 若键不存在（旧路径/测试桩）则回退到原文短语，metrics.cmp_numeric 内部会再解析一次。
    cap = common.get("capacity_mw_parsed", common.get("Capacity (MW)", ""))
    sources_raw = common.get("generation_sources_json", common.get("Generation Sources", []))
    if isinstance(sources_raw, str):
        try:
            sources = json.loads(sources_raw)
        except json.JSONDecodeError:
            sources = []
    else:
        sources = sources_raw or []
    storage_status = common.get("storage_status_normalized", common.get("Storage Status", "not_mentioned"))
    normalized_sources = normalize_generation_sources(sources)
    classification = classify_energy(normalized_sources, storage_status)
    return {
        "project_name": common.get("Project Name", ""),
        "country": common.get("Country", ""),
        "category": common.get("Category", ""),
        "capacity_mw": cap,
        "project_stage": common.get("Project Stage", ""),
        "location": common.get("Location(State)", ""),
        "total_investment_usd": common.get("Total Project Investment (Million USD)", ""),
        "contract_amount_usd": common.get("Contract Amount (Million USD)", ""),
        "financing_amount_usd": common.get("Financing Amount (Million USD)", ""),
        "financing_model": common.get("Financing Model", ""),
        "project_developer": common.get("Project Developer", ""),
        "planned_start_date": common.get("Planned Start Date", ""),
        "planned_complete_date": common.get("Planned Complete Date", common.get("Planned Completion Date", "")),
        "investment_date": common.get("Investment Date", ""),
        "project_summary": proj.get("project_summary", common.get("Project Summary", "")),
        "generation_sources": list(normalized_sources),
        "storage_status": storage_status,
        "storage_power_mw": common.get("storage_power_mw_parsed", common.get("Storage Power")),
        "storage_energy_mwh": common.get("storage_energy_mwh_parsed", common.get("Storage Energy")),
        "proposed_category": classification.proposed_category,
        "entities": entities,
    }

def flat_prediction(result: dict) -> dict:
    """原始结果 → {status(归一), projects:[扁平项目,...]}；非 valid 时 projects=[]。"""
    raw_status = result.get("status")
    status = _STATUS_MAP.get(raw_status, raw_status if raw_status is not None else "")
    if status != "valid":
        return {"status": status, "projects": []}
    projects = [_flat_project(p) for p in (result.get("projects") or [])]
    return {"status": status, "projects": projects}
