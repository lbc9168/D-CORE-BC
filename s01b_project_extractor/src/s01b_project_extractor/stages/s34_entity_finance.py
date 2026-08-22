"""S3+S4 合并阶段：一次 LLM 调用产出中方实体(母子公司归一)+金融字段；返回 {chinese_entities, finance}。"""
import logging
import bri_enums as enums
from bri_parse import parse_number
from s01b_project_extractor.stages.entity_normalize import resolve_entity
from s01b_project_extractor.stages.s3_finance import AMOUNT_FIELDS, enforce_financing_model_evidence
from s01b_project_extractor.stages.s4_entity import enforce_entity_role_evidence

logger = logging.getLogger(__name__)

# 金融字段键（与 common_info 对齐），用于从合并输出里切出 finance 部分。
_FINANCE_KEYS = (
    "Total Project Investment (Million USD)",
    "Contract Amount (Million USD)",
    "Financing Amount (Million USD)",
    "Investment (or Financial Close) Date",
    "Financing Model",
    "Financing Evidence",
    "Financing Model Details",
    "Additional Remarks",
)


def run_s34(title: str, content: str, project_name: str, category: str, *, llm,
            technology_tags: tuple[str, ...] = ()) -> dict:
    """跑合并 S3+S4（一次调用）：实体做母子公司归一、投资额经 bri_parse；返回 {chinese_entities, finance}。"""
    from s01b_project_extractor.prompts.s34_entity_finance import build_s34_prompt
    from s01b_project_extractor.knowledge import main_equipment
    prompt = build_s34_prompt(title, content, project_name, category, main_equipment(category, technology_tags))
    out = llm.complete(prompt, stage="s34", json_mode=True)
    if not isinstance(out, dict):
        logger.error("S34 返回非 dict（按空处理）: %s", type(out))
        return {"chinese_entities": [], "finance": {}}
    # ── 实体部分：母子公司归一（与 run_s4 一致）──
    ents = out.get("chinese_entities", []) or []
    enforce_entity_role_evidence(ents, title, content, project_name)
    for e in ents:
        raw = e.get("Implementing Chinese Entity", "")
        resolution = resolve_entity(raw)
        e["Implementing Chinese Entity"] = resolution.implementing
        # 无条件用 parser 输出覆盖 LLM 的 Subsidiary Name：
        # LLM 不可信（易幻觉），不变量"subsidiary 非空⇔原文名"只能由 parser 保证。
        e["Subsidiary Name"] = resolution.subsidiary
        e["_entity_verification_status"] = resolution.verification_status
        e["_entity_matched_source"] = resolution.matched_source
    # ── 金融部分：切出 + 三类金额原文短语保留 + 数值经 bri_parse（与 run_s3 一致，见 AMOUNT_FIELDS 注释）──
    finance = {k: out[k] for k in _FINANCE_KEYS if k in out}
    for num_key, raw_key, phrase_key in AMOUNT_FIELDS:
        raw_field = out.pop(raw_key, None)
        finance[phrase_key] = str(raw_field).strip() if raw_field is not None else ""
        parsed = parse_number(finance.get(num_key, ""))
        if parsed is not None:
            finance[num_key] = parsed
    enforce_financing_model_evidence(finance, title, content, project_name)
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
        if e.get("_participation_evidence_status") != "verified":
            errors.append(f"实体#{i} 缺少目标项目—企业—角色绑定证据")
    return errors
