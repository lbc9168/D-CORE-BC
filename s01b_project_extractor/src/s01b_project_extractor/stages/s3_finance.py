"""S3 阶段：抽取金融字段，数值经 bri_parse 规范后回填 common_info。"""
import logging
from bri_parse import parse_number

logger = logging.getLogger(__name__)

# 三类金额：(数值键, Raw 键, 原文短语内部键)。三者概念不互斥（见 prompt B.5 归属规则）。
# raw 不变量：非空 ⇔ 逐字原文短语，空 = 未采集到原文（不得回填换算数字字符串）。
AMOUNT_FIELDS = (
    ("Total Project Investment (Million USD)", "Total Project Investment Raw", "total_investment_usd_raw_phrase"),
    ("Contract Amount (Million USD)", "Contract Amount Raw", "contract_amount_usd_raw_phrase"),
    ("Financing Amount (Million USD)", "Financing Amount Raw", "financing_amount_usd_raw_phrase"),
)


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
    return out
