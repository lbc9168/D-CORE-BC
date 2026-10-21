"""company_hierarchy_v3.json 结构契约测试。

V3 的目标是“零 ID 变更、只补关系与属性”，因此这里断言：
- 与 V2 的 563 个 entity_id 完全一致；
- 所有 parent_entity_id 都能解析、无环；
- 关键修订（农行央企、能建/国机直接母链）已落盘。

运行：pytest supporting_docs/test_hierarchy_v3.py -v
"""
import json
from pathlib import Path

SUPPORTING_DOCS = Path(__file__).parent
V2_FILE = SUPPORTING_DOCS / "company_hierarchy_v2.json"
V3_FILE = SUPPORTING_DOCS / "company_hierarchy_v3.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def entries(doc: dict) -> list[dict]:
    """Return one row per registered entity entry, including groups."""
    rows: list[dict] = []
    for group in doc["groups"]:
        rows.append({
            "kind": "group",
            "id": group.get("entity_id"),
            "name": group.get("legal_full_name_cn") or group["canonical_name_cn"],
            "canonical": group["canonical_name_cn"],
            "parent": group.get("parent_entity_id"),
            "soe": group.get("is_central_soe"),
        })
        for sub in group.get("subsidiaries") or []:
            rows.append({
                "kind": "sub",
                "id": sub.get("entity_id"),
                "name": sub.get("legal_full_name_cn") or sub.get("name_cn"),
                "canonical": group["canonical_name_cn"],
                "parent": sub.get("parent_entity_id"),
                "soe": sub.get("is_central_soe"),
            })
    return rows


def ids(doc: dict) -> set[str]:
    return {row["id"] for row in entries(doc) if row["id"]}


def test_v3_file_is_schema_v2_with_expected_counts():
    doc = load(V3_FILE)
    assert doc["schema_version"] == 2
    assert len(doc["groups"]) == 152
    assert len(ids(doc)) == 563


def test_v3_preserves_every_v2_entity_id():
    assert ids(load(V3_FILE)) == ids(load(V2_FILE))


def test_every_parent_pointer_resolves():
    doc = load(V3_FILE)
    known = ids(doc)
    missing = [
        (row["name"], row["id"], row["parent"])
        for row in entries(doc)
        if row["parent"] and row["parent"] not in known
    ]
    assert not missing, f"父指针指向不存在的实体: {missing[:10]}"


def test_parent_graph_has_no_cycle():
    doc = load(V3_FILE)
    parents = {row["id"]: row["parent"] for row in entries(doc) if row["id"]}
    for start in parents:
        seen: set[str] = set()
        current = start
        while current:
            assert current not in seen, f"企业母子关系存在环: {start}"
            seen.add(current)
            current = parents.get(current)


def test_abc_is_central_soe_in_v3():
    doc = load(V3_FILE)
    abc = next(g for g in doc["groups"] if g["canonical_name_cn"] == "中国农业银行股份有限公司")
    assert abc["is_central_soe"] is True


def test_key_direct_parent_links():
    doc = load(V3_FILE)
    by_name = {g["canonical_name_cn"]: g for g in doc["groups"]}
    ceec = by_name["中国能源建设集团有限公司"]
    sinomach = by_name["中国机械工业集团有限公司"]
    cpecc = by_name["中国电力工程顾问集团有限公司"]
    cmec = by_name["中国机械设备工程股份有限公司"]

    assert cpecc["parent_entity_id"] == ceec["entity_id"]
    assert cmec["parent_entity_id"] == sinomach["entity_id"]
