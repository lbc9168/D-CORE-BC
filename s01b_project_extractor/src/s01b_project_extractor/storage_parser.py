"""储能/光伏子项确定性解析器（纯函数，无 IO）：从 capacity_details 提取储能功率/能量与混合项目光伏子项装机。

extract_storage 的功率正则迁自 scripts/migrate_v2.py（一次性迁移退役，此处为流水线常设单一可信源）；
能量在 MWh 基础上扩展 GWh/兆瓦时/吉瓦时（纯单位换算，2026-W30 副本演练 misses 实证 ~28/101 属此类）。
extract_pv_capacity 供综合能源(HB)项目提取光伏子项装机——prompt 已强制混合项目子项明细写入
capacity_details（如 "PV: 100MW, BESS: 50MW"），故无需改抽取 prompt。
"""
from __future__ import annotations

import re
from typing import Optional

# ── 储能正则：功率需储能关键词在数值前（迁自 migrate_v2），避免把发电容量误当储能；
#    能量单位本身即储能语义，直读并换算 MWh（吉瓦时/GWh ×1000）──
_PWR = re.compile(r"(?:储能|bess|储电)[^0-9]{0,8}(\d+(?:\.\d+)?)\s*mw(?!h)", re.I)
_ENG = re.compile(r"(\d+(?:\.\d+)?)\s*(mwh|gwh|兆瓦时|吉瓦时)", re.I)
_ENG_FACTOR = {"mwh": 1.0, "兆瓦时": 1.0, "gwh": 1000.0, "吉瓦时": 1000.0}

# ── 光伏子项正则：需光伏关键词在数值前（间隔容忍"装机容量约"等短语）；
#    单位 MW/兆瓦(非兆瓦时) → GW/吉瓦(非吉瓦时)×1000 → 万千瓦×10 → kW/kWp/千瓦(非千瓦时)÷1000，按序独占匹配 ──
_PV_KW = r"(?:光伏|photovoltaic|\bpv\b|\bsolar\b)"
_PV_NUM = r"[^0-9]{0,10}(\d+(?:\.\d+)?)\s*"
_PV_UNITS: list[tuple[re.Pattern, float]] = [
    (re.compile(_PV_KW + _PV_NUM + r"(?:mwp?(?!h)|兆瓦(?!时))", re.I), 1.0),
    (re.compile(_PV_KW + _PV_NUM + r"(?:gwp?(?!h)|吉瓦(?!时))", re.I), 1000.0),
    (re.compile(_PV_KW + _PV_NUM + r"万千瓦(?!时)", re.I), 10.0),
    (re.compile(_PV_KW + _PV_NUM + r"(?:kwp?(?!h)|千瓦(?!时))", re.I), 0.001),
]
_PV_MENTION = re.compile(_PV_KW, re.I)


def extract_storage(details: Optional[str]) -> tuple[Optional[float], Optional[float]]:
    """从 capacity_details 尽力提取 (储能功率MW, 储能容量MWh)，提不出留 None。"""
    if not details:
        return (None, None)
    p = _PWR.search(details)
    e = _ENG.search(details)
    energy = float(e.group(1)) * _ENG_FACTOR[e.group(2).lower()] if e else None
    return (float(p.group(1)) if p else None, energy)


def extract_pv_capacity(details: Optional[str]) -> Optional[float]:
    """从 capacity_details 尽力提取光伏子项装机（MW，含 GW/万千瓦/kW 换算），提不出留 None。"""
    if not details:
        return None
    for pattern, factor in _PV_UNITS:
        m = pattern.search(details)
        if m:
            return float(m.group(1)) * factor
    return None


def mentions_pv(text: Optional[str]) -> bool:
    """文本是否提及光伏（misses 判定用：提了光伏却解析不出子项装机）。"""
    return bool(text and _PV_MENTION.search(text))
