"""S2 阶段：项目识别+基本信息；in-memory 校验 category/stage 枚举与名称含中文。"""
import logging
import re
import bri_enums as enums

logger = logging.getLogger(__name__)

# 主 CJK 区块（U+4E00–U+9FFF），用于「名称是否含中文」判断已足够
_CHINESE_RE = re.compile(r"[一-鿿]")


def run_s2(title: str, content: str, date: str, *, llm) -> list[dict]:
    """跑 S2，返回 projects 列表（每项含 common_info）。"""
    from s01b_project_extractor.prompts.s2_basic import build_s2_prompt
    out = llm.complete(build_s2_prompt(title, content, date), stage="s2", json_mode=True)
    if not isinstance(out, dict):
        logger.error("S2 返回非 dict（按空项目列表处理）: %s", type(out))
        return []
    projects = out.get("projects", [])
    # S2 不评估融资（融资由 S3 负责）；此处把缺省 Financing Model 置为「Unknown」（即「未确定」枚举），
    # 与多为 Unknown 的 gold 字段对齐；Phase 4 接入 S3 后会用真实融资值覆盖。
    for p in projects:
        ci = p.setdefault("common_info", {})
        if not str(ci.get("Financing Model", "")).strip():
            ci["Financing Model"] = "Unknown"
        # in_scope 出口：S2 未给时默认在范围内（不误排除）；给了则原样透传
        p.setdefault("in_scope", True)
        p.setdefault("out_of_scope_reason", "")
    return projects


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
