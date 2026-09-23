"""S3 阶段：抽取金融字段，数值经 bri_parse 规范后回填 common_info。"""
import logging
import re
import math
from bri_parse import parse_number
from s01b_project_extractor.stages.entity_response import EntityResponseError
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
    raw_evidence = finance.get("Financing Evidence", []) or []
    if not isinstance(raw_evidence, list):
        logger.warning("Financing Evidence 不是列表，按无证据处理")
        raw_evidence = []

    supported_tools: set[str] = set()
    for item in raw_evidence:
        if isinstance(item, dict) and "scope" in item:
            if item.get("scope") == "project" and item.get("rationale") and item.get("quote") and item.get("tool") in _FINANCING_LABEL_BY_TOOL:
                supported_tools.add(item["tool"])
            continue
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


def enforce_amount_evidence(finance: dict, title: str, content: str, *, entity: bool = False) -> None:
    """Keep portfolio/unit amounts and unsupported currency conversions out of project totals."""
    evidence = finance.get("Amount Evidence")
    fields = ["Subcontract Amount (Million USD)"] if entity else [f[0] for f in AMOUNT_FIELDS]
    warnings = []
    if evidence is None:
        # Historical payloads remain readable, but a guessed FX conversion is not
        # made reproducible by a new default. Preserve the raw amount for review.
        for field in fields:
            raw_key = next((f[2] for f in AMOUNT_FIELDS if f[0] == field), "Subcontract Amount Raw")
            raw = str(finance.get(raw_key, "") or "")
            if raw and not re.search(r"美元|USD|US\$", raw, re.I):
                finance[field] = None
                warnings.append(field + ": 原币保留；旧响应没有可复现汇率依据")
    else:
        if not isinstance(evidence, list) or any(not isinstance(item, dict) for item in evidence):
            raise EntityResponseError("Amount Evidence must be a list of objects", finance)
        for item in evidence:
            if item.get("amount_scope") not in {"project", "portfolio", "equipment_unit"}:
                raise EntityResponseError("Amount Evidence invalid amount_scope", finance)
            for key in ("field", "currency", "conversion_basis", "rationale"):
                if not isinstance(item.get(key), str):
                    raise EntityResponseError(f"Amount Evidence {key} must be string", finance)
            if not isinstance(item.get("evidence_quotes"), list) or any(not isinstance(q, str) for q in item["evidence_quotes"]):
                raise EntityResponseError("Amount Evidence quotes must be strings", finance)
            for key in ("original_amount", "usd_per_currency_unit"):
                value = item.get(key)
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                    raise EntityResponseError(f"Amount Evidence {key} must be nonnegative finite number or null", finance)
        for field in fields:
            items = [item for item in evidence if item["field"] == field]
            had_value = parse_number(finance.get(field)) is not None
            # Never distribute a portfolio value over its members. Multiple
            # competing claims are local ambiguity; retain evidence, clear value.
            valid = [item for item in items if item["amount_scope"] == "project"
                     and item["rationale"].strip() and item["evidence_quotes"]
                     and item.get("original_amount") is not None
                     and item.get("usd_per_currency_unit") is not None
                     and item["conversion_basis"].strip()]
            amounts = {round(item["original_amount"] * item["usd_per_currency_unit"] / 1_000_000, 8) for item in valid}
            # A claimed USD rate must be exactly one; other currencies need a
            # recorded source, never a model's untraceable current-rate estimate.
            valid_conversion = all(_source_supports_conversion(item, title, content) for item in valid)
            if len(amounts) == 1 and valid_conversion and len(valid) == len(items):
                finance[field] = amounts.pop()
            else:
                finance[field] = None
                if had_value or items:
                    warnings.append(field + ": 金额归属或换算依据不足；原始金额留审计")
    if warnings:
        finance["amount_review_warnings"] = warnings


def _source_supports_conversion(item: dict, title: str, content: str) -> bool:
    """A model-written source label is not itself an exchange-rate source."""
    if item["currency"].upper() == "USD":
        amounts = _dollar_amounts(title + "\n" + content)
        return item["usd_per_currency_unit"] == 1 and any(math.isclose(item["original_amount"], amount, rel_tol=0.001) for amount in amounts)
    basis = re.sub(r"^(?:原文|article|source)\s*[:：]\s*", "", item["conversion_basis"].strip(), flags=re.I)
    if not basis or not (basis in title or basis in content):
        return False
    # This pipeline receives article text, not an external FX table. Accept an
    # explicit source dollar equivalent only; otherwise preserve original currency.
    converted = item["original_amount"] * item["usd_per_currency_unit"]
    return any(math.isclose(converted, amount, rel_tol=0.001) for amount in _dollar_amounts(basis))


def _dollar_amounts(text: str) -> list[float]:
    values = []
    patterns = (r"(\d+(?:\.\d+)?)\s*(亿|百万|万|billion|million)?\s*(?:美元|USD|US\$)",
                r"(?:USD|US\$|\$)\s*(\d+(?:\.\d+)?)\s*(billion|million)?")
    for match in (m for pattern in patterns for m in re.finditer(pattern, text.replace(",", ""), re.I)):
        multiplier = {"亿": 1e8, "万": 1e4, "百万": 1e6, "million": 1e6, "billion": 1e9}.get((match.group(2) or "").lower(), 1)
        values.append(float(match.group(1)) * multiplier)
    return values


def bind_subcontract_evidence(out: dict) -> None:
    """Project-level evidence cannot silently allocate one sum to several companies."""
    if "Amount Evidence" not in out:
        return
    entities = out.get("chinese_entities", [])
    evidence = [item for item in out["Amount Evidence"] if isinstance(item, dict)
                and item.get("field") == "Subcontract Amount (Million USD)"]
    for entity in entities:
        if "Amount Evidence" not in entity:
            entity["Amount Evidence"] = evidence if len(entities) == 1 else []
            if evidence and len(entities) > 1:
                entity["_unassigned_subcontract_evidence"] = evidence


DEVELOPER_REVIEW_FIELDS = ("Project Developer", "Project Developer Evidence")


def validate_developer_review(out: dict) -> None:
    """Optional new fields stay absent for old responses; explicit null means unknown."""
    for key in DEVELOPER_REVIEW_FIELDS:
        if key in out and out[key] is not None and not isinstance(out[key], str):
            raise EntityResponseError(f"{key} must be string or null", out)


def apply_finance_review(common_info: dict, finance: dict, *, stage: str) -> None:
    """Apply explicit owner corrections, including nulls, without upgrading old payloads."""
    supplied = [key for key in DEVELOPER_REVIEW_FIELDS if key in finance]
    if supplied:
        common_info.setdefault("project_developer_review_original", {
            key: common_info.get(key) for key in DEVELOPER_REVIEW_FIELDS})
        common_info["project_developer_review_source"] = stage
        for key in supplied:
            common_info[key] = finance[key] or ""
        if "Project Developer" in finance and "Project Developer Evidence" not in finance:
            # Old evidence supported the replaced candidate, not this new decision.
            common_info["Project Developer Evidence"] = ""
    common_info.update({key: value for key, value in finance.items()
                        if key not in DEVELOPER_REVIEW_FIELDS and value not in ("", None)})


def run_s3(title: str, content: str, project_name: str, *, llm, project_context: dict | None = None) -> dict:
    """跑 S3，返回金融字段 dict（键与 common_info 对齐）。"""
    from s01b_project_extractor.prompts.s3_finance import build_s3_prompt
    out = llm.complete(build_s3_prompt(title, content, project_name, project_context=project_context), stage="s3", json_mode=True)
    if not isinstance(out, dict):
        raise EntityResponseError("s3: finance response must be an object", out)
    validate_developer_review(out)
    for num_key, raw_key, phrase_key in AMOUNT_FIELDS:
        raw_field = out.pop(raw_key, None)
        out[phrase_key] = str(raw_field).strip() if raw_field is not None else ""
        parsed = parse_number(out.get(num_key, ""))
        if parsed is not None:
            out[num_key] = parsed
    enforce_financing_model_evidence(out, title, content, project_name)
    enforce_amount_evidence(out, title, content)
    return out
