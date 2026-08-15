"""子类（subcategory）确定性判定规则（纯函数，无 IO）：只表达光伏项目形态。

设计约定：只返回有证据的形态子类，无证据返回 None；能源组合由
energy_taxonomy_v2 写入 technology_tags_json，不再占用 subcategory。
"""
from __future__ import annotations

import re
from typing import Optional

# ── 光伏：分布式证据关键词（无证据=None，导出层默认集中式） ──
_DISTRIBUTED = re.compile(
    r"分布式|屋顶|屋面|楼顶|户用|工商业|车棚|rooftop|\bdistributed\b|behind[- ]the[- ]meter",
    re.I,
)

def classify_pv(texts: list[str]) -> Optional[str]:
    """光伏项目子类判定：任一文本含分布式证据→"分布式"；否则 None（导出层默认集中式）。"""
    joined = " ".join(t for t in texts if t)
    return "分布式" if _DISTRIBUTED.search(joined) else None
