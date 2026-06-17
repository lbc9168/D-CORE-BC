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
    # 数值规范化（保留原文进 *_raw 由写库层处理；此处仅清洗主值）
    inv = out.get("Total Project Investment (Million USD)", "")
    parsed = parse_number(inv)
    if parsed is not None:
        out["Total Project Investment (Million USD)"] = parsed
    return out
