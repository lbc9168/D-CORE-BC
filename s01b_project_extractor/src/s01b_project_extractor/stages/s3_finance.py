"""S3 阶段：抽取金融字段，数值经 bri_parse 规范后回填 common_info。"""
import logging
from bri_parse import parse_number

logger = logging.getLogger(__name__)


def run_s3(title: str, content: str, project_name: str, *, llm) -> dict:
    """跑 S3，返回金融字段 dict（键与 common_info 对齐）。"""
    from s01b_project_extractor.prompts.s3_finance import build_s3_prompt
    out = llm.complete(build_s3_prompt(title, content, project_name), stage="s3", json_mode=True)
    if not isinstance(out, dict):
        logger.error("S3 返回非 dict（按空字典处理）: %s", type(out))
        return {}
    # 原文短语：优先取 LLM 新字段 "Total Project Investment Raw"（prompt 已新增）。
    # 降级：LLM 未返回该字段时写空——不变量：非空 ⇔ 逐字原文短语，空 = 未采集到原文。
    # 不再把换算字段值填入 raw，避免审计人员无法区分"原文"与"降级数字字符串"。
    inv_raw_field = out.pop("Total Project Investment Raw", None)
    inv = out.get("Total Project Investment (Million USD)", "")
    if inv_raw_field is not None:
        out["total_investment_usd_raw_phrase"] = str(inv_raw_field).strip()
    else:
        out["total_investment_usd_raw_phrase"] = ""  # 未采集到原文短语，写空
    parsed = parse_number(inv)
    if parsed is not None:
        out["Total Project Investment (Million USD)"] = parsed
    return out
