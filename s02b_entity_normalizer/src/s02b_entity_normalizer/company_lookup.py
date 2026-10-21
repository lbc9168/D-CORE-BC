"""company_lookup —— developer 名称三层查表引擎：registry → hierarchy(只读) → 透传+miss。

数据文件：根目录 developer_registry.json（本引擎唯一可写建议来源，但引擎本身零写盘）；
supporting_docs/company_hierarchy_v3.json 只读复用（别名→该实体自身规范名，绝不上卷母公司，spec D2）。

真实 hierarchy 文件的 subsidiary 挂法：条目形如 {"name_cn": <出现过的名字，可能是讹变/简称>,
"legal_full_name_cn": <该子公司法定全称，重复出现即为规范名>}；当两者不同时，
legal_full_name_cn 才是该子公司自身的规范名，name_cn 只是指向它的一个变体键。
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REGISTRY = _ROOT / "developer_registry.json"
DEFAULT_HIERARCHY = _ROOT / "supporting_docs" / "company_hierarchy_v3.json"
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
    identity_status: str = "confirmed"

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
    ambiguous: set = field(default_factory=set)


def _load_registry(path: Path) -> List[_Company]:
    """载入并 Pydantic 校验登记册（编程规则#6：外部 JSON 进业务前必须校验）。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [_Company(**c) for c in data["companies"]]


def _name_key(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).strip()).casefold()


def build_index(registry_path: Optional[str] = None,
                hierarchy_path: Optional[str] = None) -> LookupIndex:
    """构建两层索引；registry 别名全册冲突抛 ValueError；hierarchy 缺失时降级只用 registry。"""
    reg_map: Dict[str, tuple] = {}
    for c in _load_registry(Path(registry_path) if registry_path else DEFAULT_REGISTRY):
        if c.identity_status == "needs_review":
            continue
        for name in [c.canonical_cn, c.canonical_en, *c.aliases]:
            key = _name_key(name)
            if key in reg_map and reg_map[key][0] != c.canonical_cn:
                raise ValueError(f"registry 别名冲突: {key!r} → {reg_map[key][0]} / {c.canonical_cn}")
            reg_map[key] = (c.canonical_cn, c.canonical_en)

    hier_map: Dict[str, str] = {}
    ambiguous: set[str] = set()
    hpath = Path(hierarchy_path) if hierarchy_path else DEFAULT_HIERARCHY
    if hierarchy_path is None and not hpath.exists():
        logger.warning("hierarchy 文件缺失 %s，降级仅 registry 查表", hpath)
    elif hierarchy_path is not None or hpath.exists():
        groups = json.loads(hpath.read_text(encoding="utf-8"))["groups"]
        candidates: Dict[str, set[str]] = {}
        for g in groups:
            canon = (g.get("legal_full_name_cn") or g["canonical_name_cn"]).strip()
            excluded = set(g.get("group_only_aliases", [])) | set(g.get("disputed_aliases", []))
            contextual = {_name_key(n) for n in g.get("context_required_names", [])}
            for a in [g["canonical_name_cn"], canon, g.get("canonical_en", ""), *(g.get("aliases", []) or [])]:
                if a and a not in excluded and _name_key(a) not in contextual:
                    candidates.setdefault(_name_key(a), set()).add(canon)
            for sub in g.get("subsidiaries", []) or []:
                if sub.get("legal_entity_status") == "needs_review":
                    continue
                name_cn = (sub.get("name_cn") or "").strip()
                legal = (sub.get("legal_full_name_cn") or "").strip()
                sub_canon = legal or name_cn  # old fixtures remain supported
                if not sub_canon:
                    continue
                variants = [name_cn, legal, sub.get("canonical_en", ""), *(sub.get("aliases", []) or [])]
                contextual = {_name_key(n) for n in sub.get("context_required_names", [])}
                for v in variants:
                    if v and _name_key(v) not in contextual:
                        candidates.setdefault(_name_key(v), set()).add(sub_canon)
        ambiguous = {key for key, names in candidates.items() if len(names) > 1}
        hier_map = {key: next(iter(names)) for key, names in candidates.items() if key not in ambiguous}

    return LookupIndex(registry_map=reg_map, hierarchy_map=hier_map, ambiguous=ambiguous)


def lookup(part: str, index: LookupIndex) -> LookupResult:
    """单部件三层查找。未命中记 miss（进程内去重保序）。"""
    raw = (part or "").strip()
    key = _name_key(raw)
    if not key:
        return LookupResult(part, None, False, "passthrough")
    if key in index.registry_map:
        cn, en = index.registry_map[key]
        return LookupResult(cn, en, True, "registry")
    if key in index.hierarchy_map:
        return LookupResult(index.hierarchy_map[key], None, True, "hierarchy")
    if key not in index._seen_miss:
        index._seen_miss.add(key)
        index.misses.append(raw)
    return LookupResult(raw, None, False, "ambiguous" if key in index.ambiguous else "passthrough")


def collect_misses(index: LookupIndex) -> List[str]:
    """返回本进程内透传过的部件（去重保序），供 miss CSV。"""
    return list(index.misses)
