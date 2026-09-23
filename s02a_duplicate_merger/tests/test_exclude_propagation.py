"""Publication review does not mutate existing canonical scope or business facts."""
import hashlib
import sqlite3

import pandas as pd

from s02a_duplicate_merger.tools.duplicate_merger_tool import (
    _group_into_logical_projects, _init_db, _write_to_db, _load_from_db,
    _mark_imported, _merge_group, _write_non_project_records,
)


def _row(**changes):
    return {
        "Source Link": "https://example.test/a", "Project Name": "X项目",
        "Country": "肯尼亚", "Country ISO": "KEN", "Category": "光伏",
        "Capacity (MW)": "50", "Project Stage": "在建",
        "Implementing Chinese Entity": "中国电建",
        "Type of involvement of Chinese Entities": "EPC总承包",
        "excluded": 0, **changes,
    }


def _project():
    return _group_into_logical_projects(pd.DataFrame([_row()]))[0]


def test_group_does_not_merge_excluded_facts():
    projects = _group_into_logical_projects(pd.DataFrame([
        _row(), _row(excluded=1, exclude_reason="油气", **{"Implementing Chinese Entity": "不应入库"}),
    ]))
    assert len(projects) == 1
    assert projects[0]["excluded"] == 0
    assert [e["implementing_entity"] for e in projects[0]["entities"]] == ["中国电建"]


def test_pending_or_excluded_never_creates_or_poison_canonical(tmp_path):
    conn = _init_db(str(tmp_path / "main.db"))
    valid = _project()
    excluded = {**valid, "excluded": 1, "exclude_reason": "油气"}
    pending = {**valid, "publication_status": "pending_review"}
    assert _write_to_db(conn, [excluded, pending])["canonical_new"] == 0
    _write_to_db(conn, [valid])
    before = conn.execute("SELECT * FROM canonical_projects").fetchall()
    event_before = conn.execute("SELECT * FROM project_events").fetchall()
    _write_to_db(conn, [excluded, pending])
    assert conn.execute("SELECT * FROM canonical_projects").fetchall() == before
    assert conn.execute("SELECT * FROM project_events").fetchall() == event_before
    assert _write_non_project_records(conn, [{**excluded, "record_type": "设备订单"}]) == 0
    conn.close()


def test_merge_excluded_record_cannot_supply_business_facts():
    valid = _project()
    bad = {**valid, "excluded": 1, "project_developer": "没有证据的业主", "entities": []}
    merged = _merge_group(None, None, [bad, valid])
    assert merged["excluded"] == 0
    assert merged["project_developer"] == ""
    assert merged["entities"] == valid["entities"]


def test_load_keeps_review_pending_retryable_and_legacy_compatible(tmp_path):
    path = tmp_path / "source.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE extracted_projects (id INTEGER PRIMARY KEY, imported_to_s02a INTEGER, "
                 "project_name TEXT, excluded INTEGER, publication_status TEXT, exclude_source TEXT, needs_review INTEGER)")
    conn.executemany("INSERT INTO extracted_projects VALUES (?,0,?,?,?,?,?)", [
        (1, "legacy valid", 0, None, None, 0),
        (2, "new pending", 1, "pending_review", "entity_verification", 1),
        (3, "old pending", 1, None, "entity_verification", 1),
        (4, "scope excluded", 1, "excluded", "scope", 0),
        (5, "source verified new company", 0, "eligible", None, 1),
    ])
    conn.commit()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    df, ids = _load_from_db(str(path))
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert ids == [1, 4, 5]
    assert df["id"].tolist() == [1, 5]
    _mark_imported(str(path), ids)
    assert conn.execute("SELECT id FROM extracted_projects WHERE imported_to_s02a=0").fetchall() == [(2,), (3,)]
    conn.execute("UPDATE extracted_projects SET publication_status='eligible', excluded=0 WHERE id=2")
    conn.commit()
    df, ids = _load_from_db(str(path))
    assert ids == [2]
    assert df["id"].tolist() == [2]
    conn.close()


def test_load_only_excluded_returns_ids_for_marking(tmp_path):
    path = tmp_path / "source.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE extracted_projects (id INTEGER, imported_to_s02a INTEGER, excluded INTEGER)")
        conn.execute("INSERT INTO extracted_projects VALUES (1,0,1)")
    df, ids = _load_from_db(str(path))
    assert df.empty and ids == [1]
