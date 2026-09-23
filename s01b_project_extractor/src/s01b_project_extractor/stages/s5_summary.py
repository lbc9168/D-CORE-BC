"""S5 阶段：只生成摘要；结构化事实由其来源阶段负责，S5 没有修改权。"""
import logging
import re
import bri_enums as enums

logger = logging.getLogger(__name__)


def _guard_summary_roles(project: dict, summary: str) -> str:
    """A prose summary cannot assign a contract role rejected by entity evidence."""
    entities = project.get("chinese_entities", []) or []
    roles = {e.get("Type of involvement of Chinese Entities") for e in entities}
    names = [e.get("_entity_raw_name") or e.get("Implementing Chinese Entity", "") for e in entities]
    role_patterns = {"设备供应商": r"设备供应商|供应方|承担.{0,12}设备供应|提供风机",
                     "EPC总承包": r"EPC总承包|EPC承包商", "股权投资方": r"股权投资方|股权投资者"}
    unsupported = [role for role, pattern in role_patterns.items()
                   if role not in roles and re.search(pattern, summary)]
    reasons = ([f"摘要声称未获实体证据确认的角色: {role}" for role in unsupported]
               if any(name and name in summary for name in names) else [])
    ci = project.get("common_info", {})
    if ci.get("capacity_evidence_errors") or ci.get("_txmf_contract_errors"):
        reasons.append("容量证据存在校验问题，摘要只保留已解析字段")
    stage = ci.get("Project Stage")
    asserted_stages = re.findall(r"(?:阶段为|阶段[:：]|处于)(" + "|".join(map(re.escape, enums.valid_stages())) + ")", summary)
    if stage and any(assertion != stage for assertion in asserted_stages):
        reasons.append("摘要阶段与结构化阶段不一致")
    if not reasons:
        return summary
    project["model_project_summary"] = summary
    project["summary_guard_reasons"] = reasons
    logger.warning("S5 摘要与已核实字段冲突，改用结构化事实: %s", reasons)
    return _structured_summary(project)


def _structured_summary(project: dict) -> str:
    """Deterministic fallback consumes only normalized fields and publishable entities."""
    ci = project.get("common_info", {})
    entities = project.get("chinese_entities", []) or []
    names = [e.get("_entity_raw_name") or e.get("Implementing Chinese Entity", "") for e in entities]
    facts = [str(ci.get("Project Name", "项目")), f"阶段：{ci.get('Project Stage', 'Unknown')}"]
    for field, label, unit in (("capacity_mw_parsed", "发电装机", "MW"),
                               ("storage_power_mw_parsed", "储能功率", "MW"),
                               ("storage_energy_mwh_parsed", "储能电量", "MWh")):
        if ci.get(field) is not None:
            facts.append(f"{label}{ci[field]:g}{unit}")
    participants = []
    for name, entity in zip(names, entities):
        role = entity.get("Type of involvement of Chinese Entities")
        participants.append(f"{name}（{role if role and role != 'Unknown' else '参与已确认，具体角色未明确'}）")
    facts.append("中企参与：" + "、".join(participants) if participants else "企业候选待复核")
    return "；".join(facts) + "。"


def run_s5(structured_project: dict, *, llm) -> dict:
    """Optional summary failure never discards already verified extraction facts."""
    from s01b_project_extractor.prompts.s5_summary import build_s5_prompt
    from s01b_project_extractor.llm.client import LLMResponseParseError
    # Build outside the boundary: programming/schema bugs must not be swallowed.
    prompt = build_s5_prompt(structured_project)
    try:
        out = llm.complete(prompt, stage="s5", json_mode=True)
    except (RuntimeError, TimeoutError, ConnectionError) as exc:
        # The configured client wraps exhausted network/SDK retries in RuntimeError;
        # LLMResponseParseError is its typed RuntimeError subclass.
        logger.warning("S5 可选摘要调用失败，采用结构化摘要: %s", exc)
        warning = {"kind": "llm_failure", "error_type": type(exc).__name__, "message": str(exc)}
        if isinstance(exc, LLMResponseParseError):
            warning["response"] = exc.payload
        return {"summary_warning": warning}
    return out


def apply_s5(project: dict, s5_out: dict) -> dict:
    """只写摘要；保留旧格式 corrections 供审计，不采用任何事实修改建议。"""
    from copy import deepcopy
    if isinstance(s5_out, dict) and s5_out.get("corrections"):
        project["s5_ignored_corrections"] = deepcopy(s5_out["corrections"])
    summary = s5_out.get("project_summary") if isinstance(s5_out, dict) else None
    warning = s5_out.get("summary_warning") if isinstance(s5_out, dict) else None
    if not (isinstance(warning, dict) and isinstance(warning.get("message"), str)):
        warning = None
    if warning or not isinstance(summary, str) or not summary.strip():
        project["summary_warning"] = deepcopy(warning) if warning else {
            "kind": "invalid_summary", "message": "S5 project_summary must be a nonempty string",
            "response": deepcopy(s5_out),
        }
        project["summary_validation_error"] = project["summary_warning"]["message"]
        project["project_summary"] = _structured_summary(project)
        logger.warning("S5 降级为结构化摘要: %s", project["summary_validation_error"])
        return project
    project["project_summary"] = _guard_summary_roles(project, summary.strip())
    return project
