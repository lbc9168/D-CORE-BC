"""S3+S4 合并阶段：一次 LLM 调用产出中方实体(母子公司归一)+金融字段；返回 {chinese_entities, finance}。"""
import logging
import bri_enums as enums
from bri_parse import parse_number
from s01b_project_extractor.stages.entity_normalize import normalize_entity

logger = logging.getLogger(__name__)

# 金融字段键（与 common_info 对齐），用于从合并输出里切出 finance 部分。
_FINANCE_KEYS = (
    "Total Project Investment (Million USD)",
    "Investment (or Financial Close) Date",
    "Financing Model",
    "Financing Model Details",
    "Additional Remarks",
)


def run_s34(title: str, content: str, project_name: str, category: str, *, llm) -> dict:
    """跑合并 S3+S4（一次调用）：实体做母子公司归一、投资额经 bri_parse；返回 {chinese_entities, finance}。"""
    from s01b_project_extractor.prompts.s34_entity_finance import build_s34_prompt
    from s01b_project_extractor.tools.project_extractor_tool import _get_entity_lookup_text
    from s01b_project_extractor.knowledge import main_equipment
    prompt = build_s34_prompt(title, content, project_name, category,
                              _get_entity_lookup_text(), main_equipment(category))
    out = llm.complete(prompt, stage="s34", json_mode=True)
    if not isinstance(out, dict):
        logger.error("S34 返回非 dict（按空处理）: %s", type(out))
        return {"chinese_entities": [], "finance": {}}
    # ── 实体部分：母子公司归一（与 run_s4 一致）──
    ents = out.get("chinese_entities", []) or []
    for e in ents:
        raw = e.get("Implementing Chinese Entity", "")
        parent, sub = normalize_entity(raw)
        e["Implementing Chinese Entity"] = parent
        if not (e.get("Subsidiary Name") or "").strip() and sub:
            e["Subsidiary Name"] = sub
    # ── 金融部分：切出 + 投资额经 bri_parse（与 run_s3 一致）──
    finance = {k: out[k] for k in _FINANCE_KEYS if k in out}
    parsed = parse_number(finance.get("Total Project Investment (Million USD)", ""))
    if parsed is not None:
        finance["Total Project Investment (Million USD)"] = parsed
    return {"chinese_entities": ents, "finance": finance}


def validate_s34(out: dict) -> list[str]:
    """校验合并输出的实体部分（involvement 合法 + 实体名非空）；金融部分无强校验（与 S3 一致）。"""
    errors = []
    valid_inv = set(enums.valid_involvement())
    for i, e in enumerate((out or {}).get("chinese_entities", []) or []):
        inv = str(e.get("Type of involvement of Chinese Entities", "")).strip()
        if inv and inv not in valid_inv:
            errors.append(f"实体#{i} 参与方式不合法: {inv}")
        if not str(e.get("Implementing Chinese Entity", "")).strip():
            errors.append(f"实体#{i} Implementing Chinese Entity 为空")
    return errors
