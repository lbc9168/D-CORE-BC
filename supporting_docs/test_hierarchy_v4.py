"""company_hierarchy_v4.json 结构契约测试。

V4 = V3 的 563 个主体 ID + 6 个审批新增 + tree 层级 + 4 条人工 override。
运行：pytest supporting_docs/test_hierarchy_v4.py -v
"""
import json
from pathlib import Path

SUPPORTING_DOCS = Path(__file__).parent
V3_FILE = SUPPORTING_DOCS / "company_hierarchy_v3.json"
V4_FILE = SUPPORTING_DOCS / "company_hierarchy_v4.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def rows(doc: dict) -> list[dict]:
    result = []
    for group in doc["groups"]:
        result.append({**group, "_kind": "group"})
        for sub in group.get("subsidiaries") or []:
            result.append({**sub, "_kind": "sub", "_group": group["canonical_name_cn"]})
    return result


def by_id(doc: dict) -> dict[str, dict]:
    return {row["entity_id"]: row for row in rows(doc) if row.get("entity_id")}


def test_v4_shape_and_v3_ids_are_preserved():
    v4 = load(V4_FILE)
    v3 = load(V3_FILE)
    assert v4["schema_version"] == 2
    assert len(v4["groups"]) == 150
    assert len(rows(v4)) == 569
    v3_ids = {
        row.get("entity_id")
        for group in v3["groups"]
        for row in [group, *(group.get("subsidiaries") or [])]
        if row.get("entity_id")
    }
    assert v3_ids <= set(by_id(v4))


def test_key_listed_company_hierarchy():
    index = by_id(load(V4_FILE))
    assert index["ent_3d8eb7a5fea850b4bb28"]["parent_entity_id"] == "ent_405cdb1433d45008b11d"  # CPECC -> CEEC
    assert index["ent_28a6343c256b59dca63d"]["parent_entity_id"] == "ent_e825d214717852b2b67d"  # CMEC -> Sinomach
    assert index["ent_38cfb8bfb59c509f9985"]["parent_entity_id"] == "ent_405cdb1433d45008b11d"  # CEEC listed -> CEEC
    assert index["ent_181f4c7a5bf45c1c8b96"]["parent_entity_id"] == "ent_c2e4a0dbc10c5a5ead87"  # PowerChina listed
    assert index["ent_d3b1bba2d9c05cf7969f"]["parent_entity_id"] == "ent_36ac391bc81b5d438be2"  # CCCC listed
    assert index["ent_35437a0d0e025dfc8b67"]["parent_entity_id"] is None  # China Merchants Group L1
    assert index["ent_f32888e2f6c75a6d9ed1"]["parent_entity_id"] == "ent_35437a0d0e025dfc8b67"
    assert index["ent_9e3c182615665371b37e"]["parent_entity_id"] == "ent_35437a0d0e025dfc8b67"


def test_v3_overrides_are_preserved():
    index = by_id(load(V4_FILE))
    assert index["ent_9d2682cd130457bf8ef2"]["parent_entity_id"] == "ent_36ac391bc81b5d438be2"  # CWE -> CCCC
    assert index["ent_29d46875263751799bac"]["parent_entity_id"] == "ent_6810a59fc6bb5e30b51a"  # 上海三菱 -> 上海机电
    assert index["ent_dfe3fb0d67265683a271"]["parent_entity_id"] is None  # Recurrent LLC no direct parent

    v4 = load(V4_FILE)
    canadian = next(g for g in v4["groups"] if g["entity_id"] == "ent_373f225e11cd58deaeb0")
    assert any(s["entity_id"] == "ent_dfe3fb0d67265683a271" for s in canadian["subsidiaries"])

    hunan = next(g for g in v4["groups"] if g["entity_id"] == "ent_6ad0740cacbc591d8b7d")
    assert hunan["historical_only"] is True


def test_group_only_alias_is_preserved():
    v4 = load(V4_FILE)
    shanghai = next(g for g in v4["groups"] if g["entity_id"] == "ent_2d85682b9f585f1b9dd7")
    assert "上海电气中央研究院" in shanghai["group_only_aliases"]


def test_v4_has_no_parent_cycle():
    index = by_id(load(V4_FILE))
    for start in index:
        seen: set[str] = set()
        current = start
        while current and current in index:
            assert current not in seen, f"parent cycle at {start}"
            seen.add(current)
            current = index[current]["parent_entity_id"]
