"""company_lookup —— developer 名称三层查表引擎：registry → hierarchy(只读) → 透传+miss。

数据文件：根目录 developer_registry.json（本引擎唯一可写建议来源，但引擎本身零写盘）；
supporting_docs/company_hierarchy_v2.json 只读复用（别名→该实体自身规范名，绝不上卷母公司，spec D2）。

真实 hierarchy 文件的 subsidiary 挂法：条目形如 {"name_cn": <出现过的名字，可能是讹变/简称>,
"legal_full_name_cn": <该子公司法定全称，重复出现即为规范名>}；当两者不同时，
legal_full_name_cn 才是该子公司自身的规范名，name_cn 只是指向它的一个变体键。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REGISTRY = _ROOT / "developer_registry.json"
DEFAULT_HIERARCHY = _ROOT / "supporting_docs" / "company_hierarchy_v2.json"
_CJK_RE = re.compile(r"[一-鿿]")


class _Company(BaseModel):
    """登记册单条：canonical_cn/en 必填非空，aliases 列表。"""
    canonical_cn: str
    canonical_en: str
    aliases: List[str] = []
    is_chinese: bool
    parent_cn: Optional[str] = None
    note: Optional[str] = None
    source: str = ""

    @field_validator("canonical_cn", "canonical_en")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("canonical 名不得为空")
        return v.strip()


@dataclass(frozen=True)
class LookupResult:
    canonical_cn: str
    canonical_en: Optional[str]
    matched: bool
    source: str  # registry | hierarchy | passthrough


@dataclass
class LookupIndex:
    registry_map: Dict[str, tuple]        # 名/别名 → (canonical_cn, canonical_en)
    hierarchy_map: Dict[str, str]         # 名/别名 → 该实体自身规范名（不上卷）
    misses: List[str] = field(default_factory=list)
    _seen_miss: set = field(default_factory=set)


def _load_registry(path: Path) -> List[_Company]:
    """载入并 Pydantic 校验登记册（编程规则#6：外部 JSON 进业务前必须校验）。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [_Company(**c) for c in data["companies"]]


def build_index(registry_path: Optional[str] = None,
                hierarchy_path: Optional[str] = None) -> LookupIndex:
    """构建两层索引；registry 别名全册冲突抛 ValueError；hierarchy 缺失时降级只用 registry。"""
    reg_map: Dict[str, tuple] = {}
    for c in _load_registry(Path(registry_path) if registry_path else DEFAULT_REGISTRY):
        for name in [c.canonical_cn, *c.aliases]:
            key = name.strip()
            if key in reg_map and reg_map[key][0] != c.canonical_cn:
                raise ValueError(f"registry 别名冲突: {key!r} → {reg_map[key][0]} / {c.canonical_cn}")
            reg_map[key] = (c.canonical_cn, c.canonical_en)

    hier_map: Dict[str, str] = {}
    hpath = Path(hierarchy_path) if hierarchy_path else DEFAULT_HIERARCHY
    if hierarchy_path is None and not hpath.exists():
        logger.warning("hierarchy 文件缺失 %s，降级仅 registry 查表", hpath)
    elif hierarchy_path is not None or hpath.exists():
        groups = json.loads(hpath.read_text(encoding="utf-8"))["groups"]
        for g in groups:
            canon = g["canonical_name_cn"].strip()
            for a in g.get("aliases", []) or []:
                hier_map.setdefault(a.strip(), canon)  # 集团别名→集团自身
            for sub in g.get("subsidiaries", []) or []:
                name_cn = (sub.get("name_cn") or "").strip()
                legal = (sub.get("legal_full_name_cn") or "").strip()
                # 该子公司自身的规范名：优先取 legal_full_name_cn（真实文件里 name_cn
                # 本身可能就是讹变/简称键，legal_full_name_cn 才是重复出现的规范全称）；
                # legal 缺失时退回 name_cn 自身。
                sub_canon = legal or name_cn
                if not sub_canon:
                    continue
                variants = [name_cn, legal, *(sub.get("aliases", []) or [])]
                for v in variants:
                    v = (v or "").strip()
                    if v and v != sub_canon:
                        hier_map.setdefault(v, sub_canon)  # 变体→子公司自身，绝不上卷
                hier_map.setdefault(sub_canon, sub_canon)  # 规范名本身也可直接命中
    return LookupIndex(registry_map=reg_map, hierarchy_map=hier_map)


def lookup(part: str, index: LookupIndex) -> LookupResult:
    """单部件三层查找。未命中记 miss（进程内去重保序）。"""
    key = (part or "").strip()
    if not key:
        return LookupResult(part, None, False, "passthrough")
    if key in index.registry_map:
        cn, en = index.registry_map[key]
        return LookupResult(cn, en, True, "registry")
    if _CJK_RE.search(key) and key in index.hierarchy_map:
        return LookupResult(index.hierarchy_map[key], None, True, "hierarchy")
    if key not in index._seen_miss:
        index._seen_miss.add(key)
        index.misses.append(key)
    return LookupResult(key, None, False, "passthrough")


def collect_misses(index: LookupIndex) -> List[str]:
    """返回本进程内透传过的部件（去重保序），供 miss CSV。"""
    return list(index.misses)
