"""S02A 实体身份键必须保留同集团、同角色下的不同具体子公司。"""

import pandas as pd

from s02a_duplicate_merger.tools.duplicate_merger_tool import _group_into_logical_projects


def test_grouping_uses_parent_subsidiary_and_role_identity():
    """两个子公司都保留，完全相同的第三行仍被去重。"""
    base = {
        "Source Link": "https://example.test/project",
        "Project Name": "测试项目",
        "Country": "测试国",
        "Category": "光伏",
        "Capacity (MW)": "100",
        "Project Stage": "在建",
        "Implementing Chinese Entity": "中国能源建设集团有限公司",
        "Type of involvement of Chinese Entities": "EPC总承包",
    }
    frame = pd.DataFrame(
        [
            {**base, "Subsidiary Name": "中国能建A院"},
            {**base, "Subsidiary Name": "中国能建B院"},
            {**base, "Subsidiary Name": "中国能建A院"},
        ]
    )

    projects = _group_into_logical_projects(frame)

    assert len(projects) == 1
    assert [entity["subsidiary_name"] for entity in projects[0]["entities"]] == [
        "中国能建A院",
        "中国能建B院",
    ]


def _project_with_entities(entities):
    project = _group_into_logical_projects(pd.DataFrame([{
        "Source Link": "https://example.test/project", "Project Name": "测试项目",
        "Country": "肯尼亚", "Country ISO": "KEN", "Category": "光伏",
        "Capacity (MW)": "100", "Project Stage": "在建",
    }]))[0]
    project["entities"] = entities
    return project


def _entity(name, entity_id=None):
    return {
        "implementing_entity": "中国能源建设集团有限公司",
        "subsidiary_name": name, "involvement_type": "EPC总承包",
        "subcontract_amount": "", "entity_id": entity_id,
        "entity_name": name if entity_id else None,
        "group_entity_id": "cn-group" if entity_id else None,
        "raw_entity_name": name, "identity_status": "verified_chinese",
        "entity_evidence_json": '{"source":"https://example.test/project"}',
    }


def test_merge_group_retains_siblings_and_deduplicates_alias_ids():
    from s02a_duplicate_merger.tools.duplicate_merger_tool import _merge_group
    a = _entity("A院", "cn-a")
    alias_a = _entity("A院全称", "cn-a")
    b = _entity("B院", "cn-b")
    merged = _merge_group(None, None, [_project_with_entities([a]), _project_with_entities([alias_a, b])])
    assert [e["entity_id"] for e in merged["entities"]] == ["cn-a", "cn-b"]
    legacy = _merge_group(None, None, [_project_with_entities([_entity("A院")]), _project_with_entities([_entity("B院")])])
    assert len(legacy["entities"]) == 2


def test_entity_identity_roundtrip_and_legacy_enrichment_without_duplicates(tmp_path):
    from s02a_duplicate_merger.tools.duplicate_merger_tool import _init_db, _write_to_db, _fetch_event_as_dict
    conn = _init_db(str(tmp_path / "main.db"))
    old = _project_with_entities([_entity("A院")])
    _write_to_db(conn, [old])
    event_id = conn.execute("SELECT id FROM project_events").fetchone()[0]
    entity_row_id = conn.execute("SELECT id FROM chinese_entities").fetchone()[0]
    new = _project_with_entities([_entity("A院", "cn-a"), _entity("B院", "cn-b")])
    result = _write_to_db(conn, [new])
    assert result["entity_new"] == 1
    rows = conn.execute("SELECT id,entity_id FROM chinese_entities ORDER BY id").fetchall()
    assert rows[0] == (entity_row_id, "cn-a")
    assert len(rows) == 2
    alias = _project_with_entities([_entity("A院全称", "cn-a")])
    assert _write_to_db(conn, [alias])["entity_new"] == 0
    saved = _fetch_event_as_dict(conn, event_id)["entities"]
    assert saved[0]["entity_id"] == "cn-a"
    assert saved[0]["group_entity_id"] == "cn-group"
    assert saved[0]["entity_evidence_json"] == new["entities"][0]["entity_evidence_json"]
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_grouping_carries_id_and_matches_aliases():
    base = {
        "Source Link": "https://example.test/project", "Project Name": "测试项目",
        "Country": "肯尼亚", "Category": "光伏", "Capacity (MW)": "100", "Project Stage": "在建",
        "Implementing Chinese Entity": "中国能源建设集团有限公司",
        "Type of involvement of Chinese Entities": "EPC总承包",
        "entity_id": "cn-a", "entity_name": "A院", "group_entity_id": "cn-group",
        "raw_entity_name": "A院简称", "identity_status": "verified_chinese", "entity_evidence_json": "{}",
    }
    result = _group_into_logical_projects(pd.DataFrame([
        {**base, "Subsidiary Name": "A院简称"}, {**base, "Subsidiary Name": "A院全称"},
    ]))
    assert len(result[0]["entities"]) == 1
    assert result[0]["entities"][0]["raw_entity_name"] == "A院简称"


def test_raw_name_bridge_is_unique_and_never_merges_distinct_known_ids(tmp_path):
    import pytest
    from s02a_duplicate_merger.tools.duplicate_merger_tool import _find_existing_entity, _init_db
    conn = _init_db(str(tmp_path / "main.db"))
    entity = {**_entity("新辰能源", "cn-new"), "raw_entity_name": "新辰能源"}
    conn.executemany(
        "INSERT INTO chinese_entities (event_id,implementing_entity,subsidiary_name,involvement_type,entity_id,raw_entity_name) "
        "VALUES ('event',?,'','EPC总承包',?,?)",
        [("旧名称一", "cn-other", "新辰能源"), ("历史无原文", None, None)],
    )
    # Two nonempty IDs never collapse, and missing historic raw_name is not guessed.
    assert _find_existing_entity(conn, "event", entity) is None
    conn.execute(
        "INSERT INTO chinese_entities (event_id,implementing_entity,involvement_type,raw_entity_name) "
        "VALUES ('event','待补录旧名','EPC总承包','新辰能源')"
    )
    original_id = conn.execute("SELECT MAX(id) FROM chinese_entities").fetchone()[0]
    assert _find_existing_entity(conn, "event", entity)[0] == original_id
    conn.execute(
        "INSERT INTO chinese_entities (event_id,implementing_entity,involvement_type,raw_entity_name) "
        "VALUES ('event','另一未决主体','EPC总承包','新辰能源')"
    )
    with pytest.raises(ValueError, match="Ambiguous entity replay"):
        _find_existing_entity(conn, "event", entity)
    conn.close()
