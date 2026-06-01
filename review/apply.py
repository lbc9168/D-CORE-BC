"""apply：按 verdict 分流——字段值高置信纠正自动改库；status/存在性挑战与低置信入 review_queue；exclude 判决高置信自动置 excluded（可逆），低置信入 exclude_suggested 清单。"""
import logging
from typing import Callable, Optional

from review import queue

logger = logging.getLogger(__name__)


def route(*, canonical_id, source_link, verdicts, review_db, update_field,
          conf_threshold: float = 0.8, value_validator: Optional[Callable] = None,
          exclude_fn: Optional[Callable] = None, exclude_auto_threshold: float = 1.0) -> dict:
    """处理一个项目的 verdict 列表。

    - update_field(canonical_id, field, new_value)：字段纠正闭包（事务改库），调用方注入。
    - value_validator(field, suggested_value)->bool：自动改前校验建议值合法性（None=不校验）。
    - exclude_fn(canonical_id, reason)：exclude 自动置位闭包（事务改库 excluded=1），调用方注入；None 时退化为入队。
    - exclude_auto_threshold：exclude 自动置位的最低 confidence（默认 1.0，即仅"确定"才自动排除）。
    返回 {auto_corrected, auto_excluded, queued, reasons}。"""
    auto_corrected = auto_excluded = queued = 0
    reasons: list[str] = []
    for v in verdicts:
        if v.verdict == "ok":
            continue
        # exclude 判决：高置信 + 注入了 exclude_fn → 自动置 excluded（可逆、记日志）；否则入人工清单
        if v.verdict == "exclude":
            if exclude_fn is not None and v.confidence >= exclude_auto_threshold:
                exclude_fn(canonical_id, v.evidence)
                auto_excluded += 1
            else:
                queue.add_item(review_db, canonical_id=canonical_id, source_link=source_link,
                               reason_type="exclude_suggested", field="status",
                               current_value=None, suggested_value="exclude",
                               critic_confidence=v.confidence, evidence=v.evidence)
                queued += 1
                reasons.append("exclude_suggested")
            continue
        # status / 存在性挑战：高风险，绝不自动改，一律入清单
        if v.field == "status":
            queue.add_item(review_db, canonical_id=canonical_id, source_link=source_link,
                           reason_type="status_challenge", field="status",
                           current_value=None, suggested_value=v.suggested_value,
                           critic_confidence=v.confidence, evidence=v.evidence)
            queued += 1
            reasons.append("status_challenge")
            continue
        # 字段值纠正：高置信 + 有建议值 + 建议值通过合法性校验 → 自动改；否则入清单
        value_ok = value_validator is None or value_validator(v.field, v.suggested_value)
        if v.verdict == "wrong" and v.suggested_value and v.confidence >= conf_threshold and value_ok:
            update_field(canonical_id, v.field, v.suggested_value)
            auto_corrected += 1
        else:
            queue.add_item(review_db, canonical_id=canonical_id, source_link=source_link,
                           reason_type="low_confidence_field", field=v.field,
                           current_value=None, suggested_value=v.suggested_value,
                           critic_confidence=v.confidence, evidence=v.evidence)
            queued += 1
            reasons.append("low_confidence_field")
    return {"auto_corrected": auto_corrected, "auto_excluded": auto_excluded,
            "queued": queued, "reasons": reasons}
