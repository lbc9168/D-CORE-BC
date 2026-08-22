"""S3 阶段：抽取金融字段，数值经 bri_parse 规范后回填 common_info。"""
import logging
import re
from bri_parse import parse_number
from s01b_project_extractor.stages.evidence_binding import validate_bound_evidence

logger = logging.getLogger(__name__)

# 三类金额：(数值键, Raw 键, 原文短语内部键)。三者概念不互斥（见 prompt B.5 归属规则）。
# raw 不变量：非空 ⇔ 逐字原文短语，空 = 未采集到原文（不得回填换算数字字符串）。
AMOUNT_FIELDS = (
    ("Total Project Investment (Million USD)", "Total Project Investment Raw", "total_investment_usd_raw_phrase"),
    ("Contract Amount (Million USD)", "Contract Amount Raw", "contract_amount_usd_raw_phrase"),
    ("Financing Amount (Million USD)", "Financing Amount Raw", "financing_amount_usd_raw_phrase"),
)

_FINANCING_TOOL_EVIDENCE = {
    "credit": re.compile(
        r"贷款|信贷|授信|银团|银行财团|买方信贷|出口信贷|优惠贷款|"
        r"\bloan\b|credit facility|debt financing|bank consortium",
        re.IGNORECASE,
    ),
    "bond": re.compile(r"债券|公司债|点心债|绿色债|\bbond(?:s)?\b", re.IGNORECASE),
    "equity": re.compile(
        r"股权融资|发行股份|认购股份|增资(?:扩股)?|资本金出资|"
        r"equity financing|share issuance|capital contribution",
        re.IGNORECASE,
    ),
    "other": re.compile(
        r"赠款|无偿援助|资金援助|资金和技术援助|资助|"
        r"\bgrant(?:s)?\b|financial aid|financial assistance|funding assistance",
        re.IGNORECASE,
    ),
}

_FINANCING_LABEL_BY_TOOL = {
    "credit": "信贷融资",
    "bond": "债券融资",
    "equity": "股权融资",
    "other": "其他",
}


def _tool_is_bound_in_clause(pattern: re.Pattern, quote: str, project_anchor: str) -> bool:
    """要求项目锚点与融资工具出现在同一自然句或分号分句。"""
    clauses = re.split(r"[。；;\n]", quote)
    return any(project_anchor in clause and pattern.search(clause) for clause in clauses)


def enforce_financing_model_evidence(
    finance: dict,
    title: str,
    content: str,
    project_name: str = "",
) -> None:
    """仅依据绑定到目标项目的逐字证据确定融资模式，并丢弃临时证据字段。"""
    requested_model = str(finance.get("Financing Model", "") or "").strip()
    raw_evidence = finance.pop("Financing Evidence", []) or []
    if not isinstance(raw_evidence, list):
        logger.warning("Financing Evidence 不是列表，按无证据处理")
        raw_evidence = []

    supported_tools: set[str] = set()
    for item in raw_evidence:
        valid, reason = validate_bound_evidence(
            item,
            title,
            content,
            target_project=project_name,
        )
        if not valid:
            logger.warning("融资证据未通过项目绑定校验: %s", reason)
            continue
        tool = str(item.get("tool", "") or "").strip().lower()
        quote = str(item.get("quote", "") or "").strip()
        anchor = str(item.get("project_anchor", "") or "").strip()
        pattern = _FINANCING_TOOL_EVIDENCE.get(tool)
        if pattern is None or not _tool_is_bound_in_clause(pattern, quote, anchor):
            logger.warning("融资证据与工具不匹配: tool=%s quote=%s", tool, quote[:120])
            continue
        supported_tools.add(tool)

    if not supported_tools:
        final_model = "Unknown"
    elif len(supported_tools) == 1:
        final_model = _FINANCING_LABEL_BY_TOOL[next(iter(supported_tools))]
    else:
        final_model = "混合融资"

    if final_model != requested_model:
        logger.warning(
            "融资模式按项目级证据纠偏: requested=%s final=%s tools=%s",
            requested_model or "空",
            final_model,
            sorted(supported_tools),
        )
    finance["Financing Model"] = final_model
    if final_model == "Unknown":
        finance["Financing Model Details"] = ""


def run_s3(title: str, content: str, project_name: str, *, llm) -> dict:
    """跑 S3，返回金融字段 dict（键与 common_info 对齐）。"""
    from s01b_project_extractor.prompts.s3_finance import build_s3_prompt
    out = llm.complete(build_s3_prompt(title, content, project_name), stage="s3", json_mode=True)
    if not isinstance(out, dict):
        logger.error("S3 返回非 dict（按空字典处理）: %s", type(out))
        return {}
    for num_key, raw_key, phrase_key in AMOUNT_FIELDS:
        raw_field = out.pop(raw_key, None)
        out[phrase_key] = str(raw_field).strip() if raw_field is not None else ""
        parsed = parse_number(out.get(num_key, ""))
        if parsed is not None:
            out[num_key] = parsed
    enforce_financing_model_evidence(out, title, content, project_name)
    return out
