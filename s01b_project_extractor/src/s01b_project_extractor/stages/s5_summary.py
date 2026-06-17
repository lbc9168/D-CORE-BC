"""S5 阶段：生成摘要并按高置信度纠偏 common_info 的 stage/category。"""
import logging
import bri_enums as enums

logger = logging.getLogger(__name__)


def run_s5(structured_project: dict, *, llm) -> dict:
    """跑 S5，返回 {project_summary, corrections}。"""
    from s01b_project_extractor.prompts.s5_summary import build_s5_prompt
    out = llm.complete(build_s5_prompt(structured_project), stage="s5", json_mode=True)
    if not isinstance(out, dict):
        logger.error("S5 返回非 dict（按空结果处理）: %s", type(out))
        return {}
    return out


def apply_s5(project: dict, s5_out: dict) -> dict:
    """把 S5 摘要写入 project；高置信度(≥0.8)且枚举合法的纠偏应用到 common_info。"""
    summary = (s5_out.get("project_summary") or "").strip()
    if summary:
        project["project_summary"] = summary
    ci = project.setdefault("common_info", {})
    valid = {"project_stage": set(enums.valid_stages()), "category": set(enums.valid_categories())}
    field_map = {"project_stage": "Project Stage", "category": "Category"}
    for c in (s5_out.get("corrections") or []):
        f = c.get("field")
        sv = str(c.get("suggested_value", "")).strip()
        try:
            conf = float(c.get("confidence", 0))
        except (ValueError, TypeError):
            logger.debug("S5 纠偏项 confidence 非数值（跳过）: field=%s confidence=%r", f, c.get("confidence"))
            continue
        if f in field_map and conf >= 0.8 and sv in valid.get(f, set()):
            ci[field_map[f]] = sv
    return project
