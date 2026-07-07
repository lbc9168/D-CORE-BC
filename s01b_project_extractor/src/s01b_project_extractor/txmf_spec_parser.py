"""确定性解析 TX（输变电建设）/ MF（新能源制造业）专用指标，供回填 CLI 调用。

纯函数、无 LLM、无 IO。数据源优先级固定为 capacity_details → project_summary →
capacity_mw_raw（后者仅在含单位文本时参与，纯裸数字禁止单独作为来源——历史误
解析污染的教训，见任务简报）。

TX：从事件文本中独立解析 voltage_kv / line_length_km / transmission_capacity_mw，
    三列互不依赖，各自按同一优先级链在 details→summary 中查找。
MF：从事件文本中解析 production_capacity_value + production_capacity_unit 一对
    值（单位归一见 _find_standard_phrases），保守处理多短语/无信号/件数类三种歧义。

复用同包 capacity_parser.py 的模块级私有正则（_PRODUCTION_RE/_TRANSMISSION_RE）
做能量单位/输送单位的双保险拦截；capacity_parser.py 本身不做任何改动。
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from s01b_project_extractor.capacity_parser import _PRODUCTION_RE, _TRANSMISSION_RE

logger = logging.getLogger(__name__)

# ── 通用：去千位分隔符 ──────────────────────────────────────────────────────
_COMMA_RE = re.compile(r"[,，](?=\d)")


def _clean(s: Optional[str]) -> str:
    """去 None、去首尾空白、去千位分隔符，统一为可正则匹配的字符串。"""
    if not s:
        return ""
    return _COMMA_RE.sub("", str(s).strip())


# ══════════════════════════════════════════════════════════════════
# TX：voltage_kv / line_length_km / transmission_capacity_mw
# ══════════════════════════════════════════════════════════════════

# 电压等级：±可选，千伏/kV，负向排除千伏安/kVA（那是视在功率不是电压）
_VOLTAGE_RE = re.compile(r"[±]?(\d+(?:\.\d+)?)\s*(?:千伏(?!安)|kV(?![aA]))")

# 线路长度：公里/千米/km
_LENGTH_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:公里|千米|km)", re.IGNORECASE)
_LENGTH_CONTEXT_WORDS = ("总长", "全长")

# 注：兆乏/MVAr（无功功率）无需专门正则拦截——_TCAP_* 系列本就不匹配"兆乏"，
# 天然不入 transmission_capacity_mw 列（见对拍用例 BRA-TX-0001-01）。

# 输送容量：语境模式（总/额定 输送/输电/送电 容量/能力 约? N 万千瓦/兆瓦/MW/GW）
_TCAP_CONTEXT_RE = re.compile(
    r"(?:总|额定)?(?:输送|输电|送电)(?:容量|能力)约?"
    r"(\d+(?:\.\d+)?)\s*(万千瓦|兆瓦|MW|GW)",
    re.IGNORECASE,
)
# 变电容量：N万千伏安 ×10
_TCAP_WAN_QIANWEIAN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*万千伏安")
# 变电容量：NxM MVA（乘积；负向排除 MVAr，同 _TCAP_MVA_RE 的 F1 修法）
_TCAP_NXM_MVA_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+(?:\.\d+)?)\s*MVA(?![rR])", re.IGNORECASE)
# 变电容量：N MVA 直读（负向排除 MVAr/兆乏；需配合 IGNORECASE 确保 mvar/MVAR/Mvar 全排除）
_TCAP_MVA_RE = re.compile(r"(\d+(?:\.\d+)?)\s*MVA(?![rR])", re.IGNORECASE)


def _tcap_context_to_mw(value: float, unit: str) -> float:
    """按语境模式单位换算到 MW：万千瓦×10、GW×1000、兆瓦/MW 直读。"""
    unit_low = unit.lower()
    if unit == "万千瓦":
        return value * 10
    if unit_low == "gw":
        return value * 1000
    return value  # 兆瓦 / MW 直读


def _parse_voltage(text: str) -> Optional[float]:
    """多个不同千伏值取最大（线路电压等级）；无匹配返回 None。"""
    vals = {float(v) for v in _VOLTAGE_RE.findall(text)}
    if not vals:
        return None
    return max(vals)


def _parse_length(text: str) -> Optional[float]:
    """唯一长度值直接填；多个不同值优先带"总长/全长"语境的；多个不同且无语境则空。"""
    matches = list(_LENGTH_RE.finditer(text))
    if not matches:
        return None
    vals = {float(m.group(1)) for m in matches}
    if len(vals) == 1:
        return vals.pop()
    # 多个不同值：找紧邻数字前方带"总长/全长"语境的，优先取之（前置词，不看后文，
    # 避免短文本中后面出现的"总长"被前一个无关数字的宽窗口误捕获）
    for m in matches:
        before = text[max(0, m.start() - 10): m.start()]
        if any(w in before for w in _LENGTH_CONTEXT_WORDS):
            return float(m.group(1))
    return None


def _parse_transmission_capacity(text: str) -> Optional[float]:
    """输送容量：语境模式 > 变电容量(NxM MVA / 万千伏安 / MVA 直读)；能量单位先行拦截。"""
    # 能量单位拦截（复用 capacity_parser 的产能类正则做双保险）：MWh/GWh/千瓦时等命中绝不入此列
    if _PRODUCTION_RE.search(text):
        return None

    m = _TCAP_CONTEXT_RE.search(text)
    if m:
        return _tcap_context_to_mw(float(m.group(1)), m.group(2))

    m = _TCAP_WAN_QIANWEIAN_RE.search(text)
    if m:
        return float(m.group(1)) * 10

    m = _TCAP_NXM_MVA_RE.search(text)
    if m:
        return float(m.group(1)) * float(m.group(2))

    m = _TCAP_MVA_RE.search(text)
    if m:
        return float(m.group(1))

    return None


def parse_tx_event(
    capacity_details: Optional[str],
    project_summary: Optional[str],
    capacity_mw_raw: Optional[str],
) -> dict:
    """解析单个 TX 事件文本 → {voltage_kv, line_length_km, transmission_capacity_mw, miss}。

    三列各自独立，按 capacity_details → project_summary 的优先级链查找（details 命中则不看 summary）；
    capacity_mw_raw 纯裸数字不参与解析（历史误解析源）。
    """
    details = _clean(capacity_details)
    summary = _clean(project_summary)

    voltage = _parse_voltage(details)
    if voltage is None:
        voltage = _parse_voltage(summary)

    length = _parse_length(details)
    if length is None:
        length = _parse_length(summary)

    tcap = _parse_transmission_capacity(details)
    if tcap is None:
        tcap = _parse_transmission_capacity(summary)

    miss = voltage is None and length is None and tcap is None
    if miss:
        logger.info("TX 事件三列全空：details=%r，summary 节选=%r", details[:50], summary[:50])

    return {
        "voltage_kv": voltage,
        "line_length_km": length,
        "transmission_capacity_mw": tcap,
        "miss": miss,
    }


# ══════════════════════════════════════════════════════════════════
# MF：production_capacity_value + production_capacity_unit
# ══════════════════════════════════════════════════════════════════

# 标准单位产能短语正则（含负向断言防止吞掉更长单位，如 GW 不吞 GWh）
_GWH_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:GWh|吉瓦时)", re.IGNORECASE)
_MWH_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:MWh|兆瓦时)", re.IGNORECASE)
_GW_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:GW|吉瓦)(?!h|时)", re.IGNORECASE)
_WANTON_RE = re.compile(r"(\d+(?:\.\d+)?)\s*万吨")
_TON_YEAR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:tons?/year|吨/年)", re.IGNORECASE)
_WAN_QIANWA_RE = re.compile(r"(\d+(?:\.\d+)?)\s*万千瓦(?!时)")
_MW_RE = re.compile(r"(\d+(?:\.\d+)?)\s*MW(?!h)", re.IGNORECASE)

# 产能语境守卫关键词（仅 MW/万千瓦 需要，见简报）
_POS_CONTEXT_WORDS = ("年产能", "产能", "年产", "组件", "电池", "production")
_NEG_CONTEXT_WORDS = ("电厂", "电站", "发电")
_CONTEXT_WINDOW = 15

# 件数类（无标准单位时的产能短语兜底信号）
_JIANSHU_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:支|只|辆|台|片|组)(?!MVA)")


def _has_context(text: str, m: "re.Match[str]", words: tuple) -> bool:
    """匹配位置前后 _CONTEXT_WINDOW 字符内是否含 words 中任一关键词。"""
    start = max(0, m.start() - _CONTEXT_WINDOW)
    end = min(len(text), m.end() + _CONTEXT_WINDOW)
    seg = text[start:end]
    return any(w in seg for w in words)


def _find_standard_phrases(text: str) -> list[tuple[float, str]]:
    """在 text 中查找全部标准单位产能短语，返回 [(归一后 value, unit), ...]（未去重）。

    检测顺序：能量单位（GWh/MWh）优先于不带时的 GW，避免 GW 正则吞掉 GWh 数字；
    MW/万千瓦 需产能语境守卫（正向命中记入结果、负向命中直接跳过该匹配）。
    """
    found: list[tuple[float, str]] = []

    for m in _GWH_RE.finditer(text):
        found.append((float(m.group(1)), "GWh/年"))

    for m in _MWH_RE.finditer(text):
        found.append((float(m.group(1)) / 1000, "GWh/年"))

    for m in _GW_RE.finditer(text):
        found.append((float(m.group(1)), "GW/年"))

    for m in _WANTON_RE.finditer(text):
        found.append((float(m.group(1)), "万吨/年"))

    for m in _TON_YEAR_RE.finditer(text):
        found.append((float(m.group(1)) / 10000, "万吨/年"))

    for m in _WAN_QIANWA_RE.finditer(text):
        if _has_context(text, m, _NEG_CONTEXT_WORDS):
            continue
        if _has_context(text, m, _POS_CONTEXT_WORDS):
            found.append((float(m.group(1)) / 100, "GW/年"))

    for m in _MW_RE.finditer(text):
        if _has_context(text, m, _NEG_CONTEXT_WORDS):
            continue
        if _has_context(text, m, _POS_CONTEXT_WORDS):
            found.append((float(m.group(1)) / 1000, "GW/年"))

    return found


def parse_mf_event(
    capacity_details: Optional[str],
    project_summary: Optional[str],
    capacity_mw_raw: Optional[str],
) -> dict:
    """解析单个 MF 事件文本 → {production_capacity_value, production_capacity_unit, miss, miss_reason}。

    数据源优先级：capacity_details 有确定性信号（标准短语或件数类）即采用，不下探 project_summary；
    details 无信号才看 summary；capacity_mw_raw 纯裸数字不参与解析。
    """
    details = _clean(capacity_details)
    summary = _clean(project_summary)

    for text in (details, summary):
        if not text:
            continue

        phrases = _find_standard_phrases(text)
        distinct = {(round(v, 6), u) for v, u in phrases}
        if len(distinct) > 1:
            logger.info("MF 多短语歧义，保守置空：text=%r，distinct=%r", text[:80], distinct)
            return {
                "production_capacity_value": None,
                "production_capacity_unit": None,
                "miss": True,
                "miss_reason": "multi_value",
            }
        if len(distinct) == 1:
            value, unit = distinct.pop()
            return {
                "production_capacity_value": value,
                "production_capacity_unit": unit,
                "miss": False,
                "miss_reason": None,
            }

        # 无标准单位短语：件数类兜底 → (NULL, Unknown)，不算 miss
        if _JIANSHU_RE.search(text):
            return {
                "production_capacity_value": None,
                "production_capacity_unit": "Unknown",
                "miss": False,
                "miss_reason": None,
            }

        # 含 MW/万千瓦 但只命中负向语境（电厂/电站/发电），无其他信号 → miss(powerplant_only)
        if _MW_RE.search(text) or _WAN_QIANWA_RE.search(text):
            logger.info("MF 仅命中发电语境，视为无产能信号：text=%r", text[:80])
            return {
                "production_capacity_value": None,
                "production_capacity_unit": None,
                "miss": True,
                "miss_reason": "powerplant_only",
            }

    return {
        "production_capacity_value": None,
        "production_capacity_unit": None,
        "miss": True,
        "miss_reason": "no_signal",
    }
