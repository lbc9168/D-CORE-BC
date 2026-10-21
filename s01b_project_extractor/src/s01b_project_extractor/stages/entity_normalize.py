"""Resolve trusted company names without conflating legal entities and group membership.

The legacy (implementing, subsidiary) projection is retained for existing consumers.
New consumers use entity_id/entity_name; an unverified group-only mention deliberately
has no legal-entity ID. Source membership does not establish a direct parent.
"""
import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from typing import Literal

from pydantic import BaseModel, Field, StrictBool

logger = logging.getLogger(__name__)
VERIFIED_CHINESE = "verified_chinese"
VERIFIED_NON_CHINESE = "verified_non_chinese"
UNMATCHED_NEEDS_REVIEW = "unmatched_needs_review"


class _RegistryCompany(BaseModel):
    canonical_cn: str
    canonical_en: str = ""
    aliases: list[str] = Field(default_factory=list)
    review_names: list[str] = Field(default_factory=list)
    is_chinese: StrictBool
    parent_cn: str | None = None
    identity_status: Literal["confirmed", "needs_review"] = "confirmed"
    entity_id: str | None = None


class _SubsidiaryRecord(BaseModel):
    name_cn: str = Field(min_length=1)
    legal_full_name_cn: str | None = None
    aliases: list[str] = Field(default_factory=list)
    context_required_names: list[str] = Field(default_factory=list)
    entity_id: str | None = Field(default=None, pattern=r"^ent_[0-9a-f]{20}$")
    parent_entity_id: str | None = Field(default=None, pattern=r"^ent_[0-9a-f]{20}$")
    is_chinese: StrictBool | None = None
    legal_entity_status: Literal["confirmed", "needs_review"] = "confirmed"
    group_membership_status: Literal["confirmed", "historical_only"] = "confirmed"


class _GroupRecord(BaseModel):
    canonical_name_cn: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    group_only_aliases: list[str] = Field(default_factory=list)
    disputed_aliases: list[str] = Field(default_factory=list)
    context_required_names: list[str] = Field(default_factory=list)
    subsidiaries: list[_SubsidiaryRecord] = Field(default_factory=list)
    entity_id: str | None = Field(default=None, pattern=r"^ent_[0-9a-f]{20}$")
    parent_entity_id: str | None = Field(default=None, pattern=r"^ent_[0-9a-f]{20}$")
    is_chinese: StrictBool | None = True
    historical_only: StrictBool = False


@dataclass(frozen=True)
class EntityResolution:
    implementing: str
    subsidiary: str
    verification_status: str
    matched_source: str
    entity_id: str | None = None
    entity_name: str = ""
    group_id: str | None = None
    parent_entity_id: str | None = None
    raw_name: str = ""


@dataclass(frozen=True)
class _Identity:
    name: str
    entity_id: str | None
    group: str
    group_id: str | None
    parent_entity_id: str | None = None
    is_chinese: bool | None = True


def stable_entity_id(name: str) -> str:
    """Bootstrap an ID once; persisted IDs must be retained when a name changes."""
    return "ent_" + uuid5(NAMESPACE_URL, "dcore:company:" + name.strip()).hex[:20]


def _name_key(name: str) -> str:
    # Parentheses width and English case are typography, not corporate identity.
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", name).strip()).casefold()


_CORP_SUFFIXES = (
    "集团股份有限公司", "集团有限责任公司", "集团有限公司", "股份有限公司",
    "有限责任公司", "有限公司", "集团公司", "集团",
)


def _strip_suffix(name: str) -> str:
    value = _name_key(name)
    for suffix in _CORP_SUFFIXES:
        if value.endswith(suffix) and len(value) > len(suffix) + 1:
            return value[:-len(suffix)]
    return re.sub(r"(?:,?\s+(?:co\.?[,]?\s+)?(?:ltd\.?|limited|inc\.?|corporation))$", "", value).strip()


def hierarchy_path() -> Path:
    """Return the active registry file; COMPANY_HIERARCHY can pin V3 for rollback."""
    override = os.environ.get("COMPANY_HIERARCHY", "").strip()
    root = Path(__file__).resolve().parents[4] / "supporting_docs"
    if override:
        candidate = Path(override)
        return candidate if candidate.is_absolute() else root / candidate
    return root / "company_hierarchy_v4.json"


def _load_hierarchy_v2() -> dict:
    path = hierarchy_path()
    if not path.exists():
        raise FileNotFoundError(f"中企层级表不存在: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 2 or not isinstance(payload.get("groups"), list):
        raise ValueError(f"中企层级表结构不合法: {path}")
    for group in payload["groups"]:
        _GroupRecord.model_validate(group)
    return payload


def _identity_candidates(groups: list[dict]) -> tuple[dict[str, list[_Identity]], dict[str, _Identity]]:
    candidates: dict[str, list[_Identity]] = {}
    by_id: dict[str, _Identity] = {}
    conflicting_ids: set[str] = set()
    for group in groups:
        canonical = group["canonical_name_cn"].strip()
        if not canonical:
            raise ValueError("企业集团规范名不能为空")
        group_id = group.get("entity_id") or stable_entity_id(canonical)
        root = _Identity(group.get("legal_full_name_cn") or canonical, group_id, canonical,
                         group_id, group.get("parent_entity_id"), group.get("is_chinese", True))
        if group.get("historical_only"):
            # Historical filing location, not a current group assertion.
            root = _Identity(root.name, group_id, root.name, None, None, root.is_chinese)
        entries = [(group, root, [canonical, root.name])]
        for sub in group.get("subsidiaries", []):
            legal = (sub.get("legal_full_name_cn") or sub.get("name_cn") or "").strip()
            if not legal:
                raise ValueError(f"{canonical}: 子主体规范名不能为空")
            identity_id = None if sub.get("legal_entity_status") == "needs_review" else (sub.get("entity_id") or stable_entity_id(legal))
            identity = _Identity(legal, identity_id, canonical,
                                 group_id, sub.get("parent_entity_id"), sub.get("is_chinese"))
            if sub.get("group_membership_status") == "historical_only":
                # Retain the historical filing location without asserting a current
                # group after a reorganization. Nationality needs its own ruling.
                identity = _Identity(legal, identity_id, legal, None, None, sub.get("is_chinese"))
            entries.append((sub, identity, [sub.get("name_cn", ""), legal]))
        for item, identity, names in entries:
            prior = by_id.get(identity.entity_id) if identity.entity_id else None
            if prior and prior != identity:
                if item.get("entity_id"):
                    raise ValueError(f"企业 ID 的规范名/归组/母公司冲突: {identity.entity_id}")
                conflicting_ids.add(identity.entity_id)
                by_id.pop(identity.entity_id, None)
            if identity.entity_id and identity.entity_id not in conflicting_ids:
                by_id[identity.entity_id] = identity
            group_only = set(item.get("group_only_aliases", []))
            disputed = set(item.get("disputed_aliases", []))
            # A generic unit name (e.g. 中央研究院) is not a global company alias.
            contextual = {_name_key(n) for n in item.get("context_required_names", [])}
            for name in [
                *names, item.get("canonical_en", ""), *item.get("aliases", []),
                *item.get("group_only_aliases", []),
            ]:
                if not isinstance(name, str) or not name.strip() or name in disputed:
                    continue
                if _name_key(name) in contextual:
                    continue
                resolved = identity
                if name in group_only:
                    resolved = _Identity(name, None, canonical, group_id, None, root.is_chinese)
                candidates.setdefault(_name_key(name), []).append(resolved)
    # Direct links are separate from the flat group membership; validate, never invent.
    for identity in by_id.values():
        seen: set[str] = set()
        current = identity
        while current.parent_entity_id:
            if current.entity_id in seen:
                raise ValueError(f"企业母子关系存在环: {identity.entity_id}")
            seen.add(current.entity_id)
            if current.parent_entity_id not in by_id:
                raise ValueError(f"企业直接母公司不存在: {current.parent_entity_id}")
            current = by_id[current.parent_entity_id]
    return candidates, by_id


def _unique_candidates(candidates: dict[str, list[_Identity]], *, label: str) -> tuple[dict[str, _Identity], set[str]]:
    result: dict[str, _Identity] = {}
    ambiguous: set[str] = set()
    for key, entries in candidates.items():
        # Multiple spellings of the same legal entity are equivalent; group-only
        # aliases remain unresolved legal subjects even when the group is known.
        identities = {(e.entity_id, e.group_id) for e in entries}
        if len(identities) != 1:
            groups = {(e.group_id, e.group) for e in entries}
            logger.warning("entity_normalize %s歧义键: %r → 待核实；集团=%s", label, key,
                           sorted({e.group for e in entries}))
            if len(groups) == 1:
                group_id, group = next(iter(groups))
                result[key] = _Identity(key, None, group, group_id, None, None)
            else:
                ambiguous.add(key)
        else:
            result[key] = min(entries, key=lambda e: (e.name, e.group))
    return result, ambiguous


def _build_identity_index(groups: list[dict]):
    candidates, by_id = _identity_candidates(groups)
    exact, ambiguous = _unique_candidates(candidates, label="名称")
    stripped_candidates: dict[str, list[_Identity]] = {}
    for key, entries in candidates.items():
        stripped_candidates.setdefault(_strip_suffix(key), []).extend(entries)
    stripped, stripped_ambiguous = _unique_candidates(stripped_candidates, label="去尾缀碰撞")
    return exact, stripped, ambiguous, stripped_ambiguous, by_id


def _build_index_v2(groups: list[dict]) -> tuple[dict, dict]:
    """Legacy index view; ambiguous identities are omitted, never chosen lexically."""
    exact, stripped, _, _, _ = _build_identity_index(groups)
    def projection(entry):
        return (entry.group, "" if entry.name == entry.group else entry.name)
    return ({key: projection(value) for key, value in exact.items()},
            {key: projection(value) for key, value in stripped.items()})


_stripped_index_cache: dict[str, tuple[str, str]] = {}
_identity_index_cache: dict[str, _Identity] = {}
_identity_stripped_cache: dict[str, _Identity] = {}
_ambiguous_keys: set[str] = set()
_ambiguous_stripped: set[str] = set()
_identities_by_id: dict[str, _Identity] = {}


@lru_cache(maxsize=1)
def _hierarchy_index_v2() -> dict[str, tuple[str, str]]:
    global _identity_index_cache, _identity_stripped_cache, _ambiguous_keys
    global _ambiguous_stripped, _identities_by_id, _stripped_index_cache
    groups = _load_hierarchy_v2()["groups"]
    (_identity_index_cache, _identity_stripped_cache, _ambiguous_keys,
     _ambiguous_stripped, _identities_by_id) = _build_identity_index(groups)
    def projection(entry):
        return (entry.group, "" if entry.name == entry.group else entry.name)
    _stripped_index_cache = {key: projection(value) for key, value in _identity_stripped_cache.items()}
    return {key: projection(value) for key, value in _identity_index_cache.items()}


@lru_cache(maxsize=1)
def _nationality_index() -> dict[str, _RegistryCompany]:
    path = Path(__file__).resolve().parents[4] / "developer_registry.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    index: dict[str, _RegistryCompany] = {}
    for item in payload["companies"]:
        company = _RegistryCompany(**item)
        review_names = company.review_names if company.identity_status == "needs_review" else []
        for name in (company.canonical_cn, company.canonical_en, *company.aliases, *review_names):
            key = _name_key(name)
            if not key:
                continue
            prior = index.get(key)
            if prior and prior.canonical_cn != company.canonical_cn:
                raise ValueError(f"中企国别登记表别名冲突: {key}: {prior.canonical_cn}/{company.canonical_cn}")
            index[key] = company
    return index


def _find_hierarchy(name: str) -> tuple[_Identity | None, str]:
    _hierarchy_index_v2()
    key = _name_key(name)
    if key in _ambiguous_keys:
        return None, "ambiguous"
    if key in _identity_index_cache:
        return _identity_index_cache[key], "hierarchy"
    stripped = _strip_suffix(key)
    if stripped in _ambiguous_stripped:
        return None, "ambiguous"
    return _identity_stripped_cache.get(stripped), "hierarchy_suffix"


def _identity_is_chinese(identity: _Identity) -> bool | None:
    current = identity
    seen: set[str | None] = set()
    while current.entity_id not in seen:
        if current.is_chinese is not None:
            return current.is_chinese
        seen.add(current.entity_id)
        # Prefer explicit direct parents; a confirmed group membership also
        # permits inheritance, without asserting that group is the direct parent.
        next_id = current.parent_entity_id or current.group_id
        if next_id is None or next_id in seen:
            break
        next_identity = _identities_by_id.get(next_id)
        if next_identity is None:
            break
        current = next_identity
    return None


def _resolved(identity: _Identity, raw: str, source: str) -> EntityResolution:
    chinese = _identity_is_chinese(identity)
    status = VERIFIED_CHINESE if chinese is True else (VERIFIED_NON_CHINESE if chinese is False else UNMATCHED_NEEDS_REVIEW)
    return EntityResolution(identity.group, "" if raw == identity.group else raw, status, source,
                            identity.entity_id, identity.name if identity.entity_id else raw,
                            identity.group_id, identity.parent_entity_id, raw)


def resolve_entity(verbatim: str) -> EntityResolution:
    """Return identity, group and nationality separately; no fuzzy or translation matching."""
    name = (verbatim or "").strip()
    if not name or name == "Unknown":
        return EntityResolution("Unknown", "", UNMATCHED_NEEDS_REVIEW, "unmatched", raw_name=name)
    registry = _nationality_index().get(_name_key(name))
    if registry is not None:
        if registry.identity_status == "needs_review":
            return EntityResolution(name, "", UNMATCHED_NEEDS_REVIEW, "registry_needs_review",
                                    entity_name=name, raw_name=name)
        # Foreign records retain their explicit ruling. A suspected wrong ruling
        # must be amended with evidence, never overridden by a similar name.
        identity, source = _find_hierarchy(registry.canonical_cn)
        if registry.is_chinese and identity:
            return _resolved(identity, name, "registry")
        status = VERIFIED_CHINESE if registry.is_chinese else VERIFIED_NON_CHINESE
        parent = (registry.parent_cn or "").strip()
        parent_identity, _ = _find_hierarchy(parent) if parent else (None, "")
        group_id = parent_identity.group_id if parent_identity else None
        entity_id = registry.entity_id or stable_entity_id(registry.canonical_cn)
        return EntityResolution(parent if registry.is_chinese and parent else registry.canonical_cn,
                                name if registry.is_chinese and parent else "", status, "registry",
                                entity_id, registry.canonical_cn, group_id,
                                parent_identity.entity_id if parent_identity else None, name)
    identity, source = _find_hierarchy(name)
    if identity:
        return _resolved(identity, name, source)
    logger.warning("entity_normalize 未命中或歧义（待名称复核）: %s", name)
    return EntityResolution(name, "", UNMATCHED_NEEDS_REVIEW,
                            "ambiguous" if source == "ambiguous" else "unmatched",
                            entity_name=name, raw_name=name)


def _normalize_from_hierarchy(name: str) -> tuple[str, str, bool]:
    identity, _ = _find_hierarchy(name)
    if identity:
        return identity.group, "" if name == identity.group else name, True
    return name, "", False


def normalize_entity(verbatim: str) -> tuple[str, str]:
    """Compatibility projection; new storage must use the structured resolver."""
    resolution = resolve_entity(verbatim)
    return resolution.implementing, resolution.subsidiary


def verified_chinese_mentions(text: str) -> list[dict]:
    """Find registered names for candidate recall, never project participation.

    Longest registered spellings take precedence (including foreign/ambiguous
    names); ASCII names require word boundaries. No fuzzy or invented aliases.
    """
    _hierarchy_index_v2()
    names = set(_identity_index_cache) | _ambiguous_keys | set(_nationality_index())
    surface = _name_key(text)
    occupied: list[tuple[int, int]] = []
    found = []
    for name in sorted(names, key=lambda value: (-len(value), value)):
        # Two-character Chinese abbreviations occur inside ordinary prose
        # (外国投资者 contains 国投). Keep these for explicit entity resolution,
        # but never use them for scanning an unsegmented article.
        if len(name) < 2 or (len(name) < 3 and not name.isascii()):
            continue
        pattern = re.escape(name)
        if name[0].isascii() and name[0].isalnum():
            pattern = r"(?<![a-z0-9_])" + pattern
        if name[-1].isascii() and name[-1].isalnum():
            pattern += r"(?![a-z0-9_])"
        hits = [match.span() for match in re.finditer(pattern, surface)
                if not any(start < match.end() and match.start() < end
                           for start, end in occupied)]
        if not hits:
            continue
        occupied.extend(hits)
        resolution = resolve_entity(name)
        if resolution.verification_status == VERIFIED_CHINESE:
            found.append({"name": name, "entity_name": resolution.entity_name,
                          "entity_id": resolution.entity_id, "group": resolution.implementing})
    return found
