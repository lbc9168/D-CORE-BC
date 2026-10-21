#!/usr/bin/env python3
"""Build the company registry v4 from V3 (ID authority) and the four-level tree.

Read-only with respect to databases. It writes only the three registry artifacts
and a review CSV. The tree's own entity_id values are never used as canonical
IDs; every match goes through a name/alias lookup against V3.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import unicodedata
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_V3 = ROOT / "supporting_docs" / "company_hierarchy_v3.json"
DEFAULT_TREE = ROOT / "supporting_docs" / "company_hierarchy_tree.json"
DEFAULT_ADDITIONS = ROOT / "supporting_docs" / "company_registry_v4_additions.json"
DEFAULT_OVERRIDES = ROOT / "supporting_docs" / "company_registry_v4_overrides.json"
DEFAULT_OUT = ROOT / "supporting_docs"
DEFAULT_REPORT = ROOT / "output" / "company-registry-v4" / "p0"


def name_key(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value or "").strip()).casefold()


def stable_entity_id(name: str) -> str:
    return "ent_" + uuid.uuid5(uuid.NAMESPACE_URL, "dcore:company:" + name.strip()).hex[:20]


def provisional_id(name: str) -> str:
    return "prov_" + uuid.uuid5(uuid.NAMESPACE_URL, "dcore:provisional:" + name.strip()).hex[:20]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = (value or "").strip()
        key = name_key(text)
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


@dataclass
class Entity:
    entity_id: str
    name_cn: str
    name_en: str = ""
    name_en_abbr: str = ""
    aliases: list[str] = field(default_factory=list)
    level: int | None = None
    parent_entity_id: str | None = None
    root_entity_id: str | None = None
    is_central_soe: bool | None = None
    ownership_type: str | None = None
    sources: list[dict] = field(default_factory=list)
    origin: str = "v3"
    status: str = "v3_only"
    tree_entity_id: str | None = None
    tree_parent_id: str | None = None
    v3_group_id: str | None = None
    v3_group_name: str | None = None
    v3_parent: str | None = None
    context_required_names: list[str] = field(default_factory=list)
    group_only_aliases: list[str] = field(default_factory=list)
    disputed_aliases: list[str] = field(default_factory=list)
    historical_only: bool = False
    candidate_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def merge_entity(target: Entity, incoming: Entity) -> None:
    """Merge duplicate V3 entries that share one ID without changing identity."""
    if name_key(target.name_cn) != name_key(incoming.name_cn):
        target.notes.append(f"同 ID 不同规范名: {incoming.name_cn}")
        target.aliases = dedupe([*target.aliases, incoming.name_cn])
    target.aliases = dedupe([*target.aliases, *incoming.aliases])
    target.context_required_names = dedupe(
        [*target.context_required_names, *incoming.context_required_names]
    )
    target.group_only_aliases = dedupe(
        [*target.group_only_aliases, *incoming.group_only_aliases]
    )
    target.disputed_aliases = dedupe(
        [*target.disputed_aliases, *incoming.disputed_aliases]
    )
    if target.parent_entity_id is None and incoming.parent_entity_id is not None:
        target.parent_entity_id = incoming.parent_entity_id
    elif (
        incoming.parent_entity_id is not None
        and target.parent_entity_id != incoming.parent_entity_id
    ):
        target.notes.append(
            f"同 ID 父指针冲突: {target.parent_entity_id} vs {incoming.parent_entity_id}"
        )
    if target.is_central_soe is None:
        target.is_central_soe = incoming.is_central_soe


def build_v3(doc: dict) -> tuple[dict[str, Entity], dict[str, set[str]], list[dict]]:
    entities: dict[str, Entity] = {}
    name_index: dict[str, set[str]] = defaultdict(set)
    unresolved: list[dict] = []

    def add_names(entity: Entity) -> None:
        excluded = {name_key(n) for n in [
            *entity.context_required_names,
            *entity.group_only_aliases,
            *entity.disputed_aliases,
        ]}
        for value in [entity.name_cn, *entity.aliases, entity.name_en]:
            key = name_key(value)
            if not key or key in excluded:
                continue
            name_index[key].add(entity.entity_id)

    for group in doc["groups"]:
        canonical = (group.get("canonical_name_cn") or "").strip()
        if not canonical:
            continue
        group_id = group.get("entity_id") or stable_entity_id(canonical)
        legal = (group.get("legal_full_name_cn") or canonical).strip()
        entity = Entity(
            entity_id=group_id,
            name_cn=legal,
            name_en=(group.get("canonical_en") or "").strip(),
            aliases=dedupe([
                canonical, legal,
                *(group.get("aliases") or []),
            ]),
            parent_entity_id=group.get("parent_entity_id"),
            v3_group_id=group_id,
            v3_group_name=canonical,
            v3_parent=group.get("parent_entity_id"),
            is_central_soe=group.get("is_central_soe"),
            context_required_names=dedupe(group.get("context_required_names") or []),
            group_only_aliases=dedupe(group.get("group_only_aliases") or []),
            disputed_aliases=dedupe(group.get("disputed_aliases") or []),
            origin="v3",
            status="v3_only",
        )
        # canonical itself is the display name when legal is absent
        entity.name_cn = legal
        if entity.entity_id in entities:
            merge_entity(entities[entity.entity_id], entity)
        else:
            entities[entity.entity_id] = entity
        add_names(entities[entity.entity_id])

        for sub in group.get("subsidiaries") or []:
            legal_sub = (sub.get("legal_full_name_cn") or sub.get("name_cn") or "").strip()
            if sub.get("legal_entity_status") == "needs_review" or not sub.get("entity_id"):
                # Preserve the V3 behaviour: such names are group-only hints, not
                # legal entities. They must never receive an identity ID.
                group_entity = entities.get(group_id)
                if group_entity is not None:
                    contextual = {name_key(n) for n in (sub.get("context_required_names") or [])}
                    group_only_names = [
                        sub.get("name_cn") or "", legal_sub,
                        *(sub.get("aliases") or []),
                    ]
                    group_entity.group_only_aliases = dedupe([
                        *group_entity.group_only_aliases,
                        *[n for n in group_only_names if n and name_key(n) not in contextual],
                    ])
                unresolved.append({
                    "group": canonical,
                    "name": sub.get("name_cn"),
                    "legal": legal_sub,
                    "reason": sub.get("legal_entity_status") or "missing_entity_id",
                })
                continue
            sub_id = sub["entity_id"]
            historical = sub.get("group_membership_status") == "historical_only"
            effective_parent = None if historical else (sub.get("parent_entity_id") or group_id)
            sub_entity = Entity(
                entity_id=sub_id,
                name_cn=legal_sub,
                name_en=(sub.get("canonical_en") or "").strip(),
                aliases=dedupe([
                    sub.get("name_cn") or "", legal_sub,
                    *(sub.get("aliases") or []),
                ]),
                parent_entity_id=effective_parent,
                v3_group_id=None if historical else group_id,
                v3_group_name=None if historical else canonical,
                v3_parent=effective_parent,
                is_central_soe=sub.get("is_central_soe"),
                context_required_names=dedupe(sub.get("context_required_names") or []),
                group_only_aliases=dedupe(sub.get("group_only_aliases") or []),
                disputed_aliases=dedupe(sub.get("disputed_aliases") or []),
                origin="v3",
                status="v3_only",
                historical_only=historical,
            )
            if historical:
                sub_entity.notes.append("V3 historical_only：不继承当前集团/父链")
            if sub_id in entities:
                merge_entity(entities[sub_id], sub_entity)
            else:
                entities[sub_id] = sub_entity
            add_names(entities[sub_id])

    return entities, name_index, unresolved


def load_additions(
    path: Path,
    v3: dict[str, Entity],
    v3_name_index: dict[str, set[str]],
) -> tuple[dict[str, Entity], dict[str, set[str]], list[dict]]:
    """Load human-approved new entities; validate IDs, names and parents."""
    if not path.exists():
        return {}, {}, []
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema_version") != "1.0" or not isinstance(doc.get("companies"), list):
        raise ValueError(f"新增主体文件结构不合法: {path}")

    approved: dict[str, Entity] = {}
    for item in doc["companies"]:
        name = (item.get("name_cn") or "").strip()
        if not name:
            raise ValueError("新增主体缺少 name_cn")
        entity_id = item.get("entity_id") or stable_entity_id(name)
        if not re.fullmatch(r"ent_[0-9a-f]{20}", entity_id):
            raise ValueError(f"新增主体 ID 格式不合法: {entity_id}")
        if entity_id in v3:
            raise ValueError(f"新增主体 ID 与 V3 冲突: {entity_id} ({name})")
        if entity_id in approved:
            raise ValueError(f"新增主体 ID 重复: {entity_id}")
        level = item.get("level")
        if level not in (1, 2, 3, 4):
            raise ValueError(f"新增主体 level 不合法: {name} -> {level}")
        if not (item.get("reason") or "").strip():
            raise ValueError(f"新增主体缺少 reason: {name}")
        approved[entity_id] = Entity(
            entity_id=entity_id,
            name_cn=name,
            name_en=(item.get("name_en") or "").strip(),
            name_en_abbr=(item.get("name_en_abbr") or "").strip(),
            aliases=dedupe(item.get("aliases") or []),
            level=level,
            parent_entity_id=item.get("parent_entity_id"),
            is_central_soe=item.get("is_central_soe"),
            ownership_type=item.get("ownership_type"),
            sources=item.get("sources") or [],
            origin="approved",
            status="approved",
            context_required_names=dedupe(item.get("context_required_names") or []),
        )

    for entity in approved.values():
        parent = entity.parent_entity_id
        if parent and parent not in v3 and parent not in approved:
            raise ValueError(f"新增主体父节点不存在: {entity.name_cn} -> {parent}")
        if parent and parent in approved and approved[parent].level >= entity.level:
            raise ValueError(f"新增主体层级不递增: {entity.name_cn}")

    index: dict[str, set[str]] = defaultdict(set)
    for entity in approved.values():
        for value in [entity.name_cn, *entity.aliases]:
            key = name_key(value)
            if not key:
                continue
            existing = v3_name_index.get(key, set())
            if existing and entity.entity_id not in existing:
                raise ValueError(
                    f"新增主体名称与 V3 冲突: {value} -> {sorted(existing)}"
                )
            index[key].add(entity.entity_id)
    return approved, index, []


def load_overrides(
    path: Path,
    v3: dict[str, Entity],
    approved: dict[str, Entity],
) -> dict[str, dict]:
    """Load audited V3-over-tree decisions (parent or historical-only)."""
    if not path.exists():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema_version") != "1.0" or not isinstance(doc.get("overrides"), list):
        raise ValueError(f"override 文件结构不合法: {path}")
    overrides: dict[str, dict] = {}
    for item in doc["overrides"]:
        entity_id = item.get("entity_id")
        if entity_id not in v3 and entity_id not in approved:
            raise ValueError(f"override 目标不存在: {entity_id}")
        if not (item.get("reason") or "").strip():
            raise ValueError(f"override 缺少 reason: {entity_id}")
        if "parent_entity_id" not in item and not item.get("historical_only"):
            raise ValueError(f"override 没有任何操作: {entity_id}")
        overrides[entity_id] = item
    return overrides


def collect_tree(doc: dict) -> dict[str, dict]:
    """Flatten hierarchy_trees, keeping the first occurrence of a duplicate ID."""
    nodes: dict[str, dict] = {}
    order = 0

    def walk(items: list[dict], parent_id: str | None) -> None:
        nonlocal order
        for node in items or []:
            tree_id = node.get("entity_id") or f"tree_missing_{order}"
            order += 1
            entry = {
                "tree_entity_id": tree_id,
                "name_cn": (node.get("name_cn") or "").strip(),
                "name_en": (node.get("name_en") or "").strip(),
                "name_en_abbr": (node.get("name_en_abbr") or "").strip(),
                "aliases": dedupe(node.get("aliases") or []),
                "level": node.get("level"),
                "parent_tree_id": node.get("parent_entity_id") or parent_id,
                "parent_name_cn": node.get("parent_name_cn") or (
                    nodes[parent_id]["name_cn"] if parent_id in nodes else ""
                ),
                "is_central_soe": node.get("is_central_soe"),
                "ownership_type": node.get("ownership_type"),
                "sources": node.get("sources") or [],
            }
            if tree_id in nodes:
                prior = nodes[tree_id]
                if name_key(prior["name_cn"]) != name_key(entry["name_cn"]):
                    prior.setdefault("notes", []).append(
                        f"tree 同 ID 不同名称: {entry['name_cn']}"
                    )
                prior["aliases"] = dedupe([*prior["aliases"], *entry["aliases"]])
            else:
                nodes[tree_id] = entry
            walk(node.get("subsidiaries") or [], tree_id)

    walk(doc.get("hierarchy_trees") or [], None)
    return nodes


def match_tree(
    tree_nodes: dict[str, dict],
    v3: dict[str, Entity],
    name_index: dict[str, set[str]],
    approved: dict[str, Entity],
    approved_index: dict[str, set[str]],
) -> tuple[dict[str, str], list[dict], list[dict], list[dict]]:
    """Return tree_id -> entity_id, matched, ambiguous, new nodes."""
    tree_to_entity: dict[str, str] = {}
    matched: list[dict] = []
    ambiguous: list[dict] = []
    new_nodes: list[dict] = []

    for tree_id, node in tree_nodes.items():
        approved_candidates = set(approved_index.get(name_key(node["name_cn"]), set()))
        if len(approved_candidates) == 1:
            entity_id = next(iter(approved_candidates))
            matched.append({
                "tree_entity_id": tree_id,
                "tree_name": node["name_cn"],
                "entity_id": entity_id,
                "v3_name": approved[entity_id].name_cn,
                "match_type": "approved",
                "hit_keys": [node["name_cn"]],
            })
            tree_to_entity[tree_id] = entity_id
            continue
        if len(approved_candidates) > 1:
            prov = provisional_id(node["name_cn"])
            tree_to_entity[tree_id] = prov
            ambiguous.append({
                "tree_entity_id": tree_id,
                "tree_name": node["name_cn"],
                "tree_level": node["level"],
                "tree_parent_id": node["parent_tree_id"],
                "candidate_ids": sorted(approved_candidates),
                "candidate_names": sorted(approved[c].name_cn for c in approved_candidates),
                "hit_keys": [node["name_cn"]],
                "provisional_id": prov,
            })
            continue

        name_candidates = set(name_index.get(name_key(node["name_cn"]), set()))
        candidates: set[str] = set(name_candidates)
        hit_keys: list[str] = []
        match_type = "name"
        if len(name_candidates) == 1:
            hit_keys = [node["name_cn"]]
        else:
            # Only fall back to aliases when the node's own name is not a unique
            # match. Tree aliases are noisier than node names and can point at a
            # parent (e.g. Canadian Solar lists 阿特斯阳光电力集团 as an alias).
            match_type = "alias"
            for key_value in node["aliases"]:
                key = name_key(key_value)
                if not key:
                    continue
                ids = name_index.get(key, set())
                if ids:
                    candidates |= ids
                    hit_keys.append(key_value)

        if len(candidates) == 1:
            entity_id = next(iter(candidates))
            matched.append({
                "tree_entity_id": tree_id,
                "tree_name": node["name_cn"],
                "entity_id": entity_id,
                "v3_name": v3[entity_id].name_cn,
                "match_type": match_type,
                "hit_keys": hit_keys,
            })
            tree_to_entity[tree_id] = entity_id
        elif len(candidates) > 1:
            prov = provisional_id(node["name_cn"])
            tree_to_entity[tree_id] = prov
            ambiguous.append({
                "tree_entity_id": tree_id,
                "tree_name": node["name_cn"],
                "tree_level": node["level"],
                "tree_parent_id": node["parent_tree_id"],
                "candidate_ids": sorted(candidates),
                "candidate_names": sorted(v3[c].name_cn for c in candidates),
                "hit_keys": hit_keys,
                "provisional_id": prov,
            })
        else:
            prov = provisional_id(node["name_cn"])
            tree_to_entity[tree_id] = prov
            new_nodes.append({
                "tree_entity_id": tree_id,
                "tree_name": node["name_cn"],
                "tree_level": node["level"],
                "parent_tree_id": node["parent_tree_id"],
                "provisional_id": prov,
            })

    return tree_to_entity, matched, ambiguous, new_nodes


def build_entities(
    v3: dict[str, Entity],
    approved: dict[str, Entity],
    overrides: dict[str, dict],
    tree_nodes: dict[str, dict],
    tree_to_entity: dict[str, str],
    matched: list[dict],
    ambiguous: list[dict],
    new_nodes: list[dict],
    name_index: dict[str, set[str]],
) -> tuple[dict[str, Entity], list[dict]]:
    entities: dict[str, Entity] = {key: Entity(**asdict(value)) for key, value in v3.items()}
    for key, value in approved.items():
        entities[key] = Entity(**asdict(value))
    issues: list[dict] = []

    # Attach tree hierarchy to matched entities.
    for row in matched:
        entity = entities[row["entity_id"]]
        node = tree_nodes[row["tree_entity_id"]]
        if entity.tree_entity_id and entity.tree_entity_id != row["tree_entity_id"]:
            entity.notes.append(f"多 tree 节点映射同一主体: {row['tree_entity_id']}")
            continue
        entity.tree_entity_id = row["tree_entity_id"]
        entity.tree_parent_id = node["parent_tree_id"]
        entity.level = node["level"]
        entity.ownership_type = node["ownership_type"]
        entity.sources = node["sources"]
        entity.name_en_abbr = entity.name_en_abbr or node["name_en_abbr"]
        if node["is_central_soe"] is not None:
            entity.is_central_soe = node["is_central_soe"]
        if entity.origin == "approved":
            entity.origin = "approved+tree"
        else:
            entity.origin = "v3+tree"
            entity.status = "matched"

    # Provisional/ambiguous tree nodes are isolated from the runtime view.
    for row in [*new_nodes, *ambiguous]:
        node = tree_nodes[row["tree_entity_id"]]
        row_status = "provisional_new" if "provisional_id" in row and row not in ambiguous else "ambiguous"
        if row in ambiguous:
            row_status = "ambiguous"
        prov_id = row["provisional_id"]
        if prov_id in entities:
            continue
        entities[prov_id] = Entity(
            entity_id=prov_id,
            name_cn=node["name_cn"],
            name_en=node["name_en"],
            name_en_abbr=node["name_en_abbr"],
            aliases=node["aliases"],
            level=node["level"],
            tree_entity_id=row["tree_entity_id"],
            tree_parent_id=node["parent_tree_id"],
            is_central_soe=node["is_central_soe"],
            ownership_type=node["ownership_type"],
            sources=node["sources"],
            origin="tree",
            status=row_status,
            candidate_ids=row.get("candidate_ids", []),
        )

    # Resolve parents. Matched entities use a mapped tree parent only when that
    # parent is already a canonical V3 entity; otherwise fall back to V3 parent.
    for entity in entities.values():
        if entity.status in {"matched", "parent_unresolved", "approved"} and entity.tree_parent_id:
            mapped = tree_to_entity.get(entity.tree_parent_id)
            if mapped and mapped.startswith("ent_") and mapped in entities:
                entity.parent_entity_id = mapped
            elif entity.status == "approved":
                # Approval already carries a validated parent; keep it.
                pass
            else:
                entity.parent_entity_id = entity.v3_parent or (
                    entity.v3_group_id
                    if entity.v3_group_id and entity.v3_group_id != entity.entity_id
                    else None
                )
                entity.status = "parent_unresolved"
                entity.notes.append(
                    f"tree 父节点未确认: {entity.tree_parent_id}"
                )
        elif entity.status == "matched" and entity.tree_parent_id is None:
            entity.parent_entity_id = None
        elif entity.status in {"provisional_new", "ambiguous"}:
            mapped = tree_to_entity.get(entity.tree_parent_id) if entity.tree_parent_id else None
            entity.parent_entity_id = mapped

    # Explicit audited V3-over-tree decisions. These are intentionally narrow:
    # they preserve a verified parent or historical-only status that the tree
    # cannot reconstruct from ownership alone.
    for entity_id, override in overrides.items():
        entity = entities.get(entity_id)
        if entity is None:
            continue
        if "parent_entity_id" in override:
            parent = override["parent_entity_id"]
            if parent and parent not in entities:
                raise ValueError(f"override 父节点不存在: {entity_id} -> {parent}")
            entity.parent_entity_id = parent
            entity.notes.append(f"override parent: {override['reason']}")
        if override.get("historical_only"):
            entity.historical_only = True
            entity.parent_entity_id = None
            entity.notes.append(f"override historical_only: {override['reason']}")

    # Root calculation + cycle detection.
    def root_of(start: str) -> str:
        seen: list[str] = []
        current = start
        while current in entities:
            parent = entities[current].parent_entity_id
            if not parent:
                break
            if parent in seen or parent == start:
                issues.append({"entity_id": start, "issue": "parent_cycle", "at": parent})
                break
            seen.append(current)
            current = parent
        return current

    for entity in entities.values():
        root = root_of(entity.entity_id)
        entity.root_entity_id = root

    # SOE inheritance from root.
    for entity in entities.values():
        if entity.is_central_soe is None and entity.root_entity_id in entities:
            entity.is_central_soe = entities[entity.root_entity_id].is_central_soe

    return entities, issues


def accepted(entity: Entity) -> bool:
    return entity.status in {"matched", "v3_only", "parent_unresolved", "approved"} and entity.entity_id.startswith("ent_")


def build_v4_view(entities: dict[str, Entity]) -> dict:
    included = {key: value for key, value in entities.items() if accepted(value)}
    # Ensure every included parent is included; otherwise fall back or root it.
    for entity in included.values():
        if entity.parent_entity_id and entity.parent_entity_id not in included:
            fallback = entity.v3_parent if entity.v3_parent in included else None
            if fallback is None and entity.v3_group_id in included:
                fallback = entity.v3_group_id
            entity.parent_entity_id = fallback
            entity.notes.append("V4 视图移除未确认父节点，回退 V3 父链")

    children: dict[str, list[str]] = defaultdict(list)
    roots: list[str] = []
    group_members_without_parent: list[str] = []
    for entity_id, entity in included.items():
        if entity.parent_entity_id:
            children[entity.parent_entity_id].append(entity_id)
        elif (
            entity.v3_group_id
            and entity.v3_group_id in included
            and entity.v3_group_id != entity_id
        ):
            # A group member can have no asserted direct parent (e.g. Recurrent
            # Energy, LLC). Keep it inside its group instead of promoting it to L1.
            group_members_without_parent.append(entity_id)
        else:
            roots.append(entity_id)

    def group_root(group_id: str) -> str:
        seen: set[str] = set()
        current = group_id
        while current in included and included[current].parent_entity_id and current not in seen:
            seen.add(current)
            current = included[current].parent_entity_id
        return current

    def descendants(root_id: str) -> list[str]:
        result: list[str] = []
        stack = list(children.get(root_id, []))
        while stack:
            current = stack.pop()
            result.append(current)
            stack.extend(children.get(current, []))
        for member_id in group_members_without_parent:
            if group_root(included[member_id].v3_group_id) == root_id:
                result.append(member_id)
        return result

    groups: list[dict] = []
    for root_id in sorted(roots, key=lambda key: included[key].name_cn):
        root = included[root_id]
        sub_rows: list[dict] = []
        for child_id in sorted(descendants(root_id), key=lambda key: (included[key].level or 99, included[key].name_cn)):
            child = included[child_id]
            sub_rows.append({
                "name_cn": child.name_cn,
                "legal_full_name_cn": child.name_cn,
                "name_en": child.name_en,
                "canonical_en": child.name_en,
                "name_en_abbr": child.name_en_abbr,
                "aliases": [a for a in child.aliases if name_key(a) != name_key(child.name_cn)],
                "context_required_names": child.context_required_names,
                "group_only_aliases": child.group_only_aliases,
                "disputed_aliases": child.disputed_aliases,
                "entity_id": child.entity_id,
                "parent_entity_id": child.parent_entity_id,
                "level": child.level,
                "is_central_soe": child.is_central_soe,
                "ownership_type": child.ownership_type,
                "sources": child.sources,
                "tree_entity_id": child.tree_entity_id,
            })
        groups.append({
            "canonical_name_cn": root.name_cn,
            "legal_full_name_cn": root.name_cn,
            "canonical_en": root.name_en,
            "aliases": [a for a in root.aliases if name_key(a) != name_key(root.name_cn)],
            "context_required_names": root.context_required_names,
            "group_only_aliases": root.group_only_aliases,
            "disputed_aliases": root.disputed_aliases,
            "entity_id": root.entity_id,
            "parent_entity_id": None,
            "level": 1,
            "historical_only": root.historical_only,
            "is_central_soe": root.is_central_soe,
            "ownership_type": root.ownership_type,
            "sources": root.sources,
            "subsidiaries": sub_rows,
        })

    return {
        "schema_version": 2,
        "status": "p0_candidate",
        "generated_on": date.today().isoformat(),
        "notes": [
            "Generated from company_hierarchy_v3.json (ID authority) + company_hierarchy_tree.json (hierarchy authority).",
            "Tree IDs are audit labels only; canonical IDs always come from V3.",
            "Provisional/ambiguous tree nodes are excluded from this runtime view.",
        ],
        "groups": groups,
    }


def validate(
    v3: dict[str, Entity],
    approved: dict[str, Entity],
    entities: dict[str, Entity],
    view: dict,
    issues: list[dict],
) -> list[str]:
    errors: list[str] = []
    v3_ids = set(v3)
    missing = v3_ids - set(entities)
    if missing:
        errors.append(f"V3 ID 丢失 {len(missing)} 个: {sorted(missing)[:5]}")
    missing_approved = set(approved) - set(entities)
    if missing_approved:
        errors.append(f"审批新增 ID 丢失 {len(missing_approved)} 个: {sorted(missing_approved)[:5]}")
    if issues:
        errors.append(f"图谱问题 {len(issues)} 条: {issues[:3]}")

    ids = set(entities)
    for entity in entities.values():
        if entity.parent_entity_id and entity.parent_entity_id not in ids:
            errors.append(f"父节点不存在: {entity.entity_id} -> {entity.parent_entity_id}")

    seen: set[str] = set()
    for group in view.get("groups", []):
        if group["entity_id"] in seen:
            errors.append(f"V4 视图重复根节点: {group['entity_id']}")
        seen.add(group["entity_id"])
        for sub in group.get("subsidiaries", []):
            if sub["entity_id"] in seen:
                errors.append(f"V4 视图重复实体: {sub['entity_id']}")
            seen.add(sub["entity_id"])
    included_ids = {e.entity_id for e in entities.values() if accepted(e)}
    if seen != included_ids:
        errors.append(
            f"V4 视图覆盖不一致: 视图 {len(seen)} / 接受 {len(included_ids)}"
        )
    return errors


def build_changes(
    v3: dict[str, Entity],
    approved: dict[str, Entity],
    overrides: dict[str, dict],
    entities: dict[str, Entity],
    matched: list[dict],
    ambiguous: list[dict],
    new_nodes: list[dict],
    unresolved_v3: list[dict],
    issues: list[dict],
    files: dict[str, str],
) -> dict:
    crosswalk = [
        row for row in matched
        if row["match_type"] != "approved" and row["tree_entity_id"] != row["entity_id"]
    ]
    reparented = []
    for entity_id, before in v3.items():
        after = entities[entity_id]
        if (before.v3_parent or None) != (after.parent_entity_id or None):
            reparented.append({
                "entity_id": entity_id,
                "name": after.name_cn,
                "v3_parent": before.v3_parent,
                "v3_parent_name": v3.get(before.v3_parent).name_cn if before.v3_parent in v3 else None,
                "v4_parent": after.parent_entity_id,
                "v4_parent_name": entities.get(after.parent_entity_id).name_cn if after.parent_entity_id in entities else None,
                "v4_root": after.root_entity_id,
                "v4_root_name": entities.get(after.root_entity_id).name_cn if after.root_entity_id in entities else None,
            })

    alias_index: dict[str, set[str]] = defaultdict(set)
    for entity in entities.values():
        if not accepted(entity):
            continue
        excluded = {name_key(n) for n in [
            *entity.context_required_names,
            *entity.group_only_aliases,
            *entity.disputed_aliases,
        ]}
        for value in [entity.name_cn, *entity.aliases]:
            key = name_key(value)
            if key and key not in excluded:
                alias_index[key].add(entity.entity_id)
    alias_conflicts = [
        {"alias": key, "entity_ids": sorted(ids),
         "names": sorted(entities[i].name_cn for i in ids)}
        for key, ids in alias_index.items() if len(ids) > 1
    ]

    status_counts = Counter(entity.status for entity in entities.values())
    level_counts = Counter(entity.level for entity in entities.values() if entity.level)
    accepted_roots = {
        entity.entity_id for entity in entities.values()
        if accepted(entity)
        and not entity.parent_entity_id
        and not (
            entity.v3_group_id
            and entity.v3_group_id in entities
            and entity.v3_group_id != entity.entity_id
        )
    }
    root_counts = Counter(
        entities[entity.root_entity_id].name_cn
        for entity in entities.values()
        if accepted(entity) and entity.root_entity_id in entities
    )
    return {
        "schema_version": "4.0",
        "generated_on": date.today().isoformat(),
        "sources": files,
        "metrics": {
            "v3_entity_ids": len(v3),
            "approved_additions": len(approved),
            "overrides_applied": len(overrides),
            "registry_entities": len(entities),
            "accepted_entities": sum(1 for e in entities.values() if accepted(e)),
            "accepted_roots": len(accepted_roots),
            "status": dict(status_counts),
            "levels": {str(k): v for k, v in sorted(level_counts.items())},
            "tree_nodes": len(matched) + len(ambiguous) + len(new_nodes),
            "matched": len(matched),
            "crosswalk_id_changes": len(crosswalk),
            "ambiguous": len(ambiguous),
            "provisional_new": len(new_nodes),
            "reparented": len(reparented),
            "alias_conflicts": len(alias_conflicts),
            "v3_unresolved_entries": len(unresolved_v3),
            "issues": len(issues),
        },
        "id_crosswalk": crosswalk,
        "approved_additions": [
            {
                "entity_id": entity.entity_id,
                "name_cn": entity.name_cn,
                "parent_entity_id": entity.parent_entity_id,
                "parent_name_cn": entities.get(entity.parent_entity_id).name_cn
                if entity.parent_entity_id in entities else None,
                "level": entity.level,
                "reason": "approved in company_registry_v4_additions.json",
            }
            for entity in approved.values()
        ],
        "reparented": reparented,
        "overrides_applied": [
            {
                "entity_id": entity_id,
                "name_cn": entities[entity_id].name_cn if entity_id in entities else None,
                "parent_entity_id": override.get("parent_entity_id"),
                "historical_only": bool(override.get("historical_only")),
                "reason": override.get("reason"),
            }
            for entity_id, override in overrides.items()
        ],
        "ambiguous": ambiguous,
        "new_nodes": new_nodes,
        "alias_conflicts": alias_conflicts,
        "v3_unresolved_entries": unresolved_v3,
        "issues": issues,
        "root_counts": dict(root_counts.most_common(30)),
    }


def write_review_csv(path: Path, tree_nodes: dict[str, dict], matched: list[dict],
                     ambiguous: list[dict], new_nodes: list[dict],
                     entities: dict[str, Entity]) -> int:
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(row: dict) -> None:
        parent_id = row.get("tree_parent")
        parent = tree_nodes.get(parent_id) if parent_id else None
        row["tree_parent_name"] = parent["name_cn"] if parent else ""
        key = (row["tree_id"], row["decision_needed"])
        if key in seen:
            return
        seen.add(key)
        rows.append(row)

    for row in matched:
        if row["match_type"] == "approved":
            continue
        node = tree_nodes[row["tree_entity_id"]]
        if row["tree_entity_id"] != row["entity_id"]:
            add({
                "tree_id": row["tree_entity_id"],
                "tree_name": row["tree_name"],
                "level": node["level"],
                "tree_parent": node["parent_tree_id"],
                "match_type": row["match_type"],
                "candidate_ids": row["entity_id"],
                "candidate_names": row["v3_name"],
                "decision_needed": "confirm_id_crosswalk",
                "note": "同名但 tree 使用不同 ID；运行时应使用 candidate_ids",
            })
    for row in ambiguous:
        add({
            "tree_id": row["tree_entity_id"],
            "tree_name": row["tree_name"],
            "level": row["tree_level"],
            "tree_parent": row.get("tree_parent_id"),
            "match_type": "ambiguous",
            "candidate_ids": ";".join(row["candidate_ids"]),
            "candidate_names": ";".join(row["candidate_names"]),
            "decision_needed": "choose_existing_or_new",
            "note": "同名命中多个 V3 主体",
        })
    for row in new_nodes:
        add({
            "tree_id": row["tree_entity_id"],
            "tree_name": row["tree_name"],
            "level": row["tree_level"],
            "tree_parent": row["parent_tree_id"],
            "match_type": "none",
            "candidate_ids": "",
            "candidate_names": "",
            "decision_needed": "confirm_new_or_alias",
            "note": f"provisional_id={row['provisional_id']}",
        })
    for entity in entities.values():
        if entity.status == "parent_unresolved" and entity.tree_entity_id:
            node = tree_nodes[entity.tree_entity_id]
            add({
                "tree_id": entity.tree_entity_id,
                "tree_name": node["name_cn"],
                "level": node["level"],
                "tree_parent": node["parent_tree_id"],
                "match_type": "matched_parent_unresolved",
                "candidate_ids": entity.entity_id,
                "candidate_names": entity.name_cn,
                "decision_needed": "confirm_tree_parent",
                "note": f"tree 父节点未确认；当前回退 V3 父链: {entity.parent_entity_id}",
            })
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "tree_id", "tree_name", "level", "tree_parent", "tree_parent_name",
            "match_type", "candidate_ids", "candidate_names",
            "decision_needed", "note",
        ])
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def dump(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v3", type=Path, default=DEFAULT_V3)
    parser.add_argument("--tree", type=Path, default=DEFAULT_TREE)
    parser.add_argument("--additions", type=Path, default=DEFAULT_ADDITIONS)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--check", action="store_true", help="validate only; write nothing")
    args = parser.parse_args()

    v3_doc = json.loads(args.v3.read_text(encoding="utf-8"))
    tree_doc = json.loads(args.tree.read_text(encoding="utf-8"))
    v3, name_index, unresolved_v3 = build_v3(v3_doc)
    approved, approved_index, _ = load_additions(args.additions, v3, name_index)
    overrides = load_overrides(args.overrides, v3, approved)
    combined_index = {key: set(value) for key, value in name_index.items()}
    for key, ids in approved_index.items():
        combined_index.setdefault(key, set()).update(ids)
    tree_nodes = collect_tree(tree_doc)
    tree_to_entity, matched, ambiguous, new_nodes = match_tree(
        tree_nodes, v3, name_index, approved, approved_index
    )
    entities, issues = build_entities(
        v3, approved, overrides, tree_nodes, tree_to_entity, matched, ambiguous, new_nodes, combined_index
    )
    view = build_v4_view(entities)
    errors = validate(v3, approved, entities, view, issues)

    files = {
        "v3": {"path": str(args.v3), "sha256": sha256(args.v3)},
        "tree": {"path": str(args.tree), "sha256": sha256(args.tree)},
    }
    if args.additions.exists():
        files["additions"] = {
            "path": str(args.additions),
            "sha256": sha256(args.additions),
        }
    if args.overrides.exists():
        files["overrides"] = {
            "path": str(args.overrides),
            "sha256": sha256(args.overrides),
        }
    changes = build_changes(
        v3, approved, overrides, entities, matched, ambiguous, new_nodes, unresolved_v3, issues, files
    )

    print("== P0 registry v4 ==")
    for key, value in changes["metrics"].items():
        print(f"{key}: {value}")
    print(f"validation_errors: {len(errors)}")
    for item in errors[:10]:
        print("  -", item)

    if errors:
        print("P0 validation failed; no files written", file=sys.stderr)
        return 1
    if args.check:
        print("check only; no files written")
        return 0

    dump(args.out_dir / "company_registry_v4.json", {
        "schema_version": "4.0",
        "status": "p0_candidate",
        "generated_on": date.today().isoformat(),
        "sources": files,
        "entities": [asdict(entity) for entity in entities.values()],
    })
    dump(args.out_dir / "company_hierarchy_v4.json", view)
    dump(args.out_dir / "company_registry_v4_changes.json", changes)
    review_rows = write_review_csv(
        args.report_dir / "review.csv", tree_nodes, matched, ambiguous, new_nodes, entities
    )
    print(f"review_rows: {review_rows}")
    print(f"written: {args.out_dir / 'company_registry_v4.json'}")
    print(f"written: {args.out_dir / 'company_hierarchy_v4.json'}")
    print(f"written: {args.out_dir / 'company_registry_v4_changes.json'}")
    print(f"written: {args.report_dir / 'review.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
