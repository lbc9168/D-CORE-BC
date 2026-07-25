"""子类（subcategory）确定性判定规则（纯函数，无 IO）：光伏→分布式证据；综合能源→光储证据。

设计约定（与 enums_mapping.json subcategory 段一致）：
- 只返回"有证据"的子类中文键，无证据返回 None；DB 存 NULL，导出层按父类 _default 渲染
  （光伏默认集中式、综合能源默认其他，用户拍板）。
- 判定关键词属分类逻辑（与 txmf 正则同理）留在 .py；子类取值本身在 enums JSON。
"""
from __future__ import annotations

import re
from typing import Optional

# ── 光伏：分布式证据关键词（无证据=None，导出层默认集中式） ──
_DISTRIBUTED = re.compile(
    r"分布式|屋顶|屋面|楼顶|户用|工商业|车棚|rooftop|\bdistributed\b|behind[- ]the[- ]meter",
    re.I,
)

# ── 综合能源：光储 = 光伏 + 储能 且无其他能源/产出形式 ──
_HAS_PV = re.compile(r"光伏|光储|photovoltaic|\bpv\b|\bsolar\b", re.I)
_HAS_STORAGE = re.compile(r"储能|光储|储电|\bbess\b|battery|energy storage", re.I)
# 其他能源/产出：风/水/地热/生物质/燃气/柴油/光热/氢/氨 任一出现即非纯光储
# （"hydro" 同时覆盖 hydropower 与 hydrogen——制氢项目保守归"其他"）
_HAS_OTHER = re.compile(
    r"风电|风光|风机|\bwind\b|水电|hydro|地热|geothermal|生物质|biomass"
    r"|燃气|\bgas\b|柴油|diesel|光热|\bcsp\b|氢|氨|ammonia",
    re.I,
)


def classify_pv(texts: list[str]) -> Optional[str]:
    """光伏项目子类判定：任一文本含分布式证据→"分布式"；否则 None（导出层默认集中式）。"""
    joined = " ".join(t for t in texts if t)
    return "分布式" if _DISTRIBUTED.search(joined) else None


def classify_hb(texts: list[str], has_storage_value: bool) -> Optional[str]:
    """综合能源项目子类判定：含光伏 且 含储能（结构化列或文本）且 无其他能源→"光储"；否则 None（=其他）。"""
    joined = " ".join(t for t in texts if t)
    has_pv = bool(_HAS_PV.search(joined))
    has_storage = has_storage_value or bool(_HAS_STORAGE.search(joined))
    has_other = bool(_HAS_OTHER.search(joined))
    return "光储" if (has_pv and has_storage and not has_other) else None
