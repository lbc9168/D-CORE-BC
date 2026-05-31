"""S2 阶段：项目识别+基本信息；in-memory 校验 category/stage 枚举与名称含中文。"""
import re
import bri_enums as enums

_CHINESE_RE = re.compile(r"[一-鿿]")


def run_s2(title: str, content: str, date: str, *, llm) -> list[dict]:
    """跑 S2，返回 projects 列表（每项含 common_info）。"""
    from s01b_project_extractor.prompts.s2_basic import build_s2_prompt
    out = llm.complete(build_s2_prompt(title, content, date), stage="s2", json_mode=True)
    return out.get("projects", []) if isinstance(out, dict) else []


def validate_s2(projects: list[dict]) -> list[str]:
    """校验每个项目：category/stage 合法、Project Name 含中文。"""
    errors = []
    valid_cat = set(enums.valid_categories())
    valid_stage = set(enums.valid_stages())
    for i, p in enumerate(projects):
        ci = p.get("common_info", {}) or {}
        cat = str(ci.get("Category", "")).strip()
        if cat and cat not in valid_cat:
            errors.append(f"项目#{i} 分类不合法: {cat}")
        stage = str(ci.get("Project Stage", "")).strip()
        if stage and stage not in valid_stage:
            errors.append(f"项目#{i} 阶段不合法: {stage}")
        pname = str(ci.get("Project Name", "")).strip()
        if pname and not _CHINESE_RE.search(pname):
            errors.append(f"项目#{i} 名称未含中文: {pname[:30]}")
    return errors
