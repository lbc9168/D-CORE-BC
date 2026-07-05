"""
确定性容量解析器：把 LLM 逐字抄录的原文数字短语（如"600万千瓦"）换算为 MW float。

换算规则：
  万千瓦   × 10    → MW
  千瓦/kW  ÷ 1000  → MW
  GW/吉瓦  × 1000  → MW
  兆瓦/MW/MWp       直读为 MW

产能类（GWh、MWh、GW/年、万吨/年、吨/年）→ (None, details)  留 details 原文
输送容量（MVA、kVA、kV）              → (None, details)
TX/MF category                        → (None, None)  强制置空
解析失败                              → (None, None)  + logging.error
"""
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ── TX/MF：无装机概念的 category，与主库口径一致 ──────────────────────────
_NO_CAPACITY_CATEGORIES = {"输变电建设", "新能源制造业"}

# ── 预处理：去千位分隔符，统一全角为半角 ──────────────────────────────────
_PREP_RE = re.compile(r"[,，](?=\d)")

# ── 数字匹配（含小数）──────────────────────────────────────────────────────
_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)")

# ── 产能类模式（有这些后缀 → 不是装机容量）──────────────────────────────────
# 注：MWh/GWh/kWh 是电量/储能电量不是装机；GW/年/MW/年 是年产能不是装机；吨/年同理
# kWh/KWh 必须先于 kW/KW 检测，否则 "500kWh" 会被 _QIANWA_RE 误读为 0.5MW
# 兆瓦时/吉瓦时 = MWh/GWh 的中文写法，同为电量（终审 I1：曾漏拦被误读为装机）
_PRODUCTION_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*"
    r"(MWh|GWh|kWh|KWh|MW/年|GW/年|万吨/年|吨/年|千瓦时|千瓦时/年|兆瓦时|吉瓦时)",
    re.IGNORECASE,
)

# ── 输送容量模式（电压/视在功率，非装机）──────────────────────────────────
_TRANSMISSION_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(MVA|kVA|KVA|kV|KV)",
    re.IGNORECASE,
)

# ── 装机容量模式，按优先级排列 ────────────────────────────────────────────
# 万千瓦：数字 + "万千瓦"（不含"千瓦时"）
_WAN_QIANWA_RE = re.compile(r"(\d+(?:\.\d+)?)\s*万千瓦(?!时)")
# 千瓦/kW（不带"万"前缀，不带"时"/"h"后缀——kWh 由 _PRODUCTION_RE 先行拦截，双保险）
_QIANWA_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:千瓦|kW|KW)(?!时|h|p(?:k|h)?)", re.IGNORECASE)
# GW / 吉瓦（不带"/年"后缀、不带"时"后缀——吉瓦时由 _PRODUCTION_RE 先行拦截，双保险）
_GW_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:GW|吉瓦)(?!/年|时)", re.IGNORECASE)
# MW / 兆瓦 / MWp（不带h后缀、不带/年后缀、不带"时"后缀——兆瓦时由 _PRODUCTION_RE 先行拦截，双保险）
_MW_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:MW|兆瓦|MWp)(?!h|/年|时)", re.IGNORECASE)


def parse_capacity_mw(
    raw: Optional[str],
    category: Optional[str] = None,
) -> tuple[Optional[float], Optional[str]]:
    """
    解析原文容量短语 → (capacity_mw, details_note)。

    Args:
        raw:      LLM 逐字抄录的原文短语，如 "600万千瓦" 或 "200MW"。
        category: 项目 category 中文键（如"输变电建设"、"新能源制造业"）。

    Returns:
        (capacity_mw, details_note)
        - 成功换算：(float, None)
        - 产能/输送容量：(None, 含原始短语的说明字符串)
        - TX/MF category / 空输入 / 解析失败：(None, None)
    """
    # ── TX/MF 强制置空（与主库口径一致）──
    if category in _NO_CAPACITY_CATEGORIES:
        return None, None

    if not raw:
        return None, None

    s = str(raw).strip()
    if not s:
        return None, None

    # 去千位分隔符（如 "1,200MW"）
    s = _PREP_RE.sub("", s)

    # ── 优先检查产能类（GWh/MWh/GW/年/万吨/年等）→ null + details ──
    m = _PRODUCTION_RE.search(s)
    if m:
        unit = m.group(2)
        logger.info("容量短语「%s」含产能单位「%s」→ null（非装机容量）", raw, unit)
        return None, f"原文：{raw}（产能单位 {unit}，非装机容量）"

    # ── 检查输送容量（MVA/kVA/kV）→ null + details ──
    m = _TRANSMISSION_RE.search(s)
    if m:
        unit = m.group(2)
        logger.info("容量短语「%s」含输送单位「%s」→ null", raw, unit)
        return None, f"原文：{raw}（输送容量单位 {unit}，非装机容量）"

    # ── 尝试装机单位换算 ──

    # 万千瓦 × 10
    m = _WAN_QIANWA_RE.search(s)
    if m:
        val = float(m.group(1)) * 10
        return val, None

    # GW / 吉瓦 × 1000
    m = _GW_RE.search(s)
    if m:
        val = float(m.group(1)) * 1000
        return val, None

    # MW / 兆瓦 / MWp 直读
    m = _MW_RE.search(s)
    if m:
        val = float(m.group(1))
        return val, None

    # 千瓦 / kW ÷ 1000（放最后，避免误匹配"万千瓦"已处理的）
    m = _QIANWA_RE.search(s)
    if m:
        val = float(m.group(1)) / 1000
        return val, None

    # ── 无法识别单位 → null + 日志 ──
    has_number = bool(_NUM_RE.search(s))
    if has_number:
        logger.error("容量短语「%s」含数字但单位无法识别 → null（需人工核查）", raw)
    else:
        logger.info("容量短语「%s」无数字 → null", raw)
    return None, None
