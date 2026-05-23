"""数值字段解析单一可信源：把脏文本（约50 / 50-60MW / 1,200）解析为 float。"""
import re
from typing import Any, Optional

_NA = {"", "nan", "none", "na", "n/a", "<na>", "待定", "未知", "未披露", "—", "-"}
_NUM = re.compile(r"-?\d+(?:\.\d+)?")
# 形如 50-60 / 50~60 的范围：连接符在两数之间，此处的 - 是分隔符而非负号
_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*[-~～]\s*(\d+(?:\.\d+)?)")


def parse_number(val: Any) -> Optional[float]:
    """解析数值文本为 float；范围取中点；失败返回 None。"""
    if val is None:
        return None
    s = str(val).strip().lower().replace(",", "").replace("，", "")
    if s in _NA:
        return None
    # 范围优先：两数间的连接符不应被当作负号吃掉，先取中点
    m = _RANGE.search(s)
    if m:
        return round((float(m.group(1)) + float(m.group(2))) / 2, 6)
    nums = _NUM.findall(s)
    if not nums:
        return None
    try:
        return float(nums[0])
    except ValueError:
        return None
