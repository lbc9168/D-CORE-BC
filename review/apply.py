"""apply：按 verdict 分流——字段值高置信纠正自动改库；status/存在性挑战与低置信入 review_queue（不自动删）。"""
import logging
from review import queue

logger = logging.getLogger(__name__)


def route(*, canonical_id, source_link, verdicts, review_db, update_field, conf_threshold: float = 0.8) -> dict:
    """处理一个项目的 verdict 列表；update_field(canonical_id, field, new_value) 由调用方注入（事务改库）。
    返回 {auto_corrected, queued}。"""
    auto_corrected = queued = 0
    for v in verdicts:
        if v.verdict == "ok":
            continue
        # status / 存在性挑战：高风险，绝不自动改，一律入清单
        if v.field == "status":
            queue.add_item(review_db, canonical_id=canonical_id, source_link=source_link,
                           reason_type="status_challenge", field="status",
                           current_value=None, suggested_value=v.suggested_value,
                           critic_confidence=v.confidence, evidence=v.evidence)
            queued += 1
            continue
        # 字段值纠正：高置信 + 有建议值 → 自动改；否则入清单
        if v.verdict == "wrong" and v.suggested_value and v.confidence >= conf_threshold:
            update_field(canonical_id, v.field, v.suggested_value)
            auto_corrected += 1
        else:
            queue.add_item(review_db, canonical_id=canonical_id, source_link=source_link,
                           reason_type="low_confidence_field", field=v.field,
                           current_value=None, suggested_value=v.suggested_value,
                           critic_confidence=v.confidence, evidence=v.evidence)
            queued += 1
    return {"auto_corrected": auto_corrected, "queued": queued}
