"""Isolated intake/refresh tests: no production database, model calls or inferred roles."""
import hashlib
import json
import sqlite3

import pytest

from s01b_project_extractor.stages.entity_normalize import EntityResolution
from s01b_project_extractor.tools.entity_intake import _fingerprint, export_intake, refresh_entities


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _entity(name="新辰能源"):
    quote = f"{name}承建纳沃伊光伏项目。"
    return {
        "Implementing Chinese Entity": name,
        "Type of involvement of Chinese Entities": "Unknown",
        "_entity_raw_name": name,
        "Participation Evidence": [{"role": "Unknown", "quote": quote, "project_anchor": "纳沃伊光伏项目"}],
        "Chinese Identity Evidence": "",
    }


def _db(tmp_path, rows=None):
    path = tmp_path / "projects.db"
    connection = sqlite3.connect(path)
    connection.execute("""CREATE TABLE extracted_projects (
        id INTEGER PRIMARY KEY, project_name TEXT, source_link TEXT,
        implementing_entity TEXT, subsidiary_name TEXT, involvement_type TEXT,
        entity_id TEXT, entity_name TEXT, group_entity_id TEXT, raw_entity_name TEXT,
        identity_status TEXT, entity_evidence_json TEXT, publication_status TEXT,
        excluded INTEGER, exclude_reason TEXT, exclude_source TEXT, needs_review INTEGER,
        imported_to_s02a INTEGER, project_developer TEXT, financing_model TEXT,
        implementing_entity_en TEXT, subsidiary_name_en TEXT, is_central_soe INTEGER)""")
    for changes in rows or [{}]:
        entity = _entity()
        payload = {"entity": entity, "candidates": [entity], "source": {
            "title": "", "content": "新辰能源承建纳沃伊光伏项目。"}, "preserved": "audit"}
        row = {
            "id": 1, "project_name": "纳沃伊光伏项目", "source_link": "https://example.test/project",
            "implementing_entity": "新辰能源", "subsidiary_name": "", "involvement_type": "Unknown",
            "raw_entity_name": "新辰能源", "entity_name": "新辰能源", "entity_id": None, "group_entity_id": None,
            "identity_status": "unmatched_needs_review", "entity_evidence_json": json.dumps(payload, ensure_ascii=False),
            "publication_status": "pending_review", "excluded": 1, "exclude_reason": "企业身份待核实",
            "exclude_source": "entity_verification", "needs_review": 1, "imported_to_s02a": 0,
            "project_developer": "外企甲公司", "financing_model": "Unknown",
            "implementing_entity_en": "Old Translation", "subsidiary_name_en": "Old Subsidiary", "is_central_soe": 1,
        }
        row.update(changes)
        columns = ", ".join(row)
        connection.execute(f"INSERT INTO extracted_projects ({columns}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
    connection.commit()
    connection.close()
    return path


def _registered(monkeypatch):
    monkeypatch.setattr("s01b_project_extractor.stages.s4_entity.resolve_entity", lambda name: EntityResolution(
        "新辰集团", name, "verified_chinese", "hierarchy", "entity:child", "新辰能源有限公司", "entity:group", None, name,
    ))


def _read(path):
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute("SELECT * FROM extracted_projects ORDER BY id")]


def test_export_groups_names_and_preserves_read_only_fingerprint(tmp_path):
    database = _db(tmp_path, [{}, {"id": 2, "source_link": "https://example.test/second"}])
    before = _hash(database)
    report = export_intake(database, tmp_path / "intake.json")
    assert _hash(database) == before
    assert report["candidate_count"] == 1
    assert report["entities"][0]["row_ids"] == [1, 2]
    assert report["entities"][0]["records"][0]["evidence"]["source"]["content"]
    with pytest.raises(FileExistsError):
        export_intake(database, tmp_path / "intake.json")


def test_eligible_unregistered_relation_is_standardization_only(tmp_path):
    database = _db(tmp_path, [{
        "identity_status": "model_supported_chinese", "publication_status": "eligible",
        "excluded": 0, "exclude_source": None, "needs_review": 0, "imported_to_s02a": 1,
    }])
    before = _hash(database)
    report = export_intake(database, tmp_path / "intake.json")
    record = report["entities"][0]["records"][0]
    assert record["action"] == "standardize_identity"
    assert record["publication_status"] == "eligible"
    assert _hash(database) == before


def test_legacy_export_needs_reextraction_and_refresh_cannot_recover(tmp_path):
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE extracted_projects(id INTEGER PRIMARY KEY, implementing_entity TEXT, excluded INTEGER, exclude_source TEXT)")
        connection.execute("INSERT INTO extracted_projects VALUES(1,'Unknown',1,'entity_verification')")
    before = _hash(database)
    report = export_intake(database, tmp_path / "intake.json")
    assert report["entities"][0]["records"][0]["action"] == "requires_reextraction"
    assert refresh_entities(database)["rows"][0]["action"] == "requires_reextraction"
    assert _hash(database) == before


def test_refresh_dryrun_and_apply_relink_pending_preserving_roles_and_ids(tmp_path, monkeypatch):
    _registered(monkeypatch)
    database = _db(tmp_path)
    before_hash, original = _hash(database), _read(database)
    dryrun = refresh_entities(database)
    assert dryrun["changed_rows"] == 1
    assert dryrun["rows"][0]["after_status"] == "eligible"
    assert _hash(database) == before_hash
    assert list(tmp_path.glob("*.bak.db")) == []
    result = refresh_entities(database, apply=True)
    updated = _read(database)
    assert _read(result["backup"]) == original
    assert [row["id"] for row in updated] == [1]
    row = updated[0]
    assert row["entity_id"] == "entity:child"
    assert row["group_entity_id"] == "entity:group"
    assert row["publication_status"] == "eligible" and row["excluded"] == 0
    assert row["involvement_type"] == "Unknown"
    assert row["project_developer"] == "外企甲公司"
    assert row["financing_model"] == "Unknown"
    assert row["implementing_entity_en"] is None
    assert row["subsidiary_name_en"] is None
    assert row["is_central_soe"] is None
    evidence = json.loads(row["entity_evidence_json"])
    assert evidence["source"]["content"] == "新辰能源承建纳沃伊光伏项目。"
    assert evidence["preserved"] == "audit"
    assert evidence["entity"]["Participation Evidence"] == _entity()["Participation Evidence"]
    assert refresh_entities(database)["changed_rows"] == 0


def test_existing_import_is_marked_for_idempotent_replay(tmp_path, monkeypatch):
    _registered(monkeypatch)
    database = _db(tmp_path, [{"publication_status": "eligible", "excluded": 0, "exclude_source": None,
                              "imported_to_s02a": 1}])
    refresh_entities(database, apply=True)
    assert _read(database)[0]["imported_to_s02a"] == 0
    assert _read(database)[0]["id"] == 1


def test_scope_exclusion_stays_excluded_even_with_registered_entity(tmp_path, monkeypatch):
    _registered(monkeypatch)
    database = _db(tmp_path, [{"exclude_source": "extraction", "publication_status": "excluded"}])
    before = _hash(database)
    result = refresh_entities(database, apply=True)
    assert result["rows"][0]["action"] == "protected_scope_exclusion"
    assert result["changed_rows"] == 0
    assert _hash(database) == before


@pytest.mark.parametrize("defect", ["no_original", "false_quote", "other_project"])
def test_registry_does_not_replace_participation_evidence(tmp_path, monkeypatch, defect):
    _registered(monkeypatch)
    database = _db(tmp_path)
    row = _read(database)[0]
    payload = json.loads(row["entity_evidence_json"])
    if defect == "no_original":
        del payload["source"]
    elif defect == "false_quote":
        payload["source"]["content"] = "新辰能源出席会议。"
    else:
        quote = "新辰能源承建布哈拉光伏项目。"
        payload["source"]["content"] = quote
        payload["entity"]["Participation Evidence"] = [{"role": "Unknown", "quote": quote, "project_anchor": "布哈拉光伏项目"}]
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE extracted_projects SET entity_evidence_json=?", (json.dumps(payload),))
    result = refresh_entities(database, apply=True)
    assert _read(database)[0]["publication_status"] == "pending_review"
    assert result["rows"][0]["after_status"] != "eligible"


def test_backup_never_overwritten(tmp_path, monkeypatch):
    _registered(monkeypatch)
    database = _db(tmp_path)
    backup = tmp_path / "existing.db"
    backup.write_bytes(b"protected backup")
    before = _hash(database)
    with pytest.raises(FileExistsError):
        refresh_entities(database, apply=True, backup_path=backup)
    assert _hash(database) == before
    assert backup.read_bytes() == b"protected backup"


def test_external_original_database_stays_readonly(tmp_path, monkeypatch):
    _registered(monkeypatch)
    database = _db(tmp_path)
    with sqlite3.connect(database) as connection:
        payload = json.loads(connection.execute("SELECT entity_evidence_json FROM extracted_projects").fetchone()[0])
        del payload["source"]
        connection.execute("UPDATE extracted_projects SET entity_evidence_json=?", (json.dumps(payload),))
    source = tmp_path / "news.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE raw_news(url TEXT, title TEXT, content TEXT)")
        connection.execute("INSERT INTO raw_news VALUES(?, '', ?)", ("https://example.test/project", "新辰能源承建纳沃伊光伏项目。"))
    before = _hash(source)
    assert refresh_entities(database, source_db=source)["rows"][0]["after_status"] == "eligible"
    assert _hash(source) == before


def test_legacy_imported_history_not_automatically_enqueued(tmp_path):
    database = _db(tmp_path, [
        {"id": 1, "entity_evidence_json": None, "imported_to_s02a": 1,
         "publication_status": None, "excluded": 0, "needs_review": 0, "exclude_source": None},
        {"id": 2, "entity_evidence_json": None, "imported_to_s02a": 1},
        {"id": 3, "entity_evidence_json": None, "imported_to_s02a": 0,
         "publication_status": None, "excluded": 0, "needs_review": 0, "exclude_source": None},
        {"id": 4, "entity_evidence_json": json.dumps({"entity": {"Participation Evidence": []}})},
    ])
    report = export_intake(database, tmp_path / "intake.json")
    records = report["entities"][0]["records"]
    assert [row["row_id"] for row in records] == [2, 3, 4]
    assert all(row["action"] == "requires_reextraction" for row in records)


def test_refresh_retains_translations_when_corresponding_names_stay_same(tmp_path, monkeypatch):
    monkeypatch.setattr("s01b_project_extractor.stages.s4_entity.resolve_entity", lambda name: EntityResolution(
        name, "", "verified_chinese", "hierarchy", "entity:self", name, "entity:self", None, name,
    ))
    database = _db(tmp_path)
    refresh_entities(database, apply=True)
    row = _read(database)[0]
    assert row["implementing_entity_en"] == "Old Translation"
    assert row["subsidiary_name_en"] == "Old Subsidiary"
    assert row["is_central_soe"] == 1


def test_empty_wal_is_equivalent_but_nonempty_wal_is_hashed(tmp_path):
    database = _db(tmp_path)
    before = _fingerprint(database)
    wal = tmp_path / "projects.db-wal"
    wal.write_bytes(b"")
    assert _fingerprint(database) == before
    wal.write_bytes(b"nonempty WAL must be visible")
    assert _fingerprint(database) != before


def test_export_wal_mode_database_without_wal_pages(tmp_path):
    database = _db(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    connection.close()
    before = _fingerprint(database)
    assert export_intake(database, tmp_path / "intake.json")["candidate_count"] == 1
    assert _fingerprint(database) == before


@pytest.mark.parametrize("field,value", [
    ("entity_id", "entity:replacement"), ("entity_id", None),
    ("implementing_entity", "新集团"), ("subsidiary_name", "新别名"),
    ("entity_name", "新规范名"), ("group_entity_id", "entity:new-group"),
    ("involvement_type", "EPC总承包"),
])
def test_imported_existing_identity_changes_require_downstream_review(tmp_path, monkeypatch, field, value):
    _registered(monkeypatch)
    database = _db(tmp_path)
    refresh_entities(database, apply=True)
    # Establish the previously imported identity, then emulate changed registry/input.
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE extracted_projects SET imported_to_s02a=1")
        connection.execute(f"UPDATE extracted_projects SET {field}=?", (value,))
    before = _hash(database)
    report = refresh_entities(database, apply=True)
    if field == "entity_id" and value is None:
        # This is the supported first-time null→ID bridge, not replacement of a known ID.
        assert report["rows"][0]["action"] == "refresh"
    else:
        assert report["rows"][0]["action"] == "requires_downstream_review"
        assert report["changed_rows"] == 0
        assert _hash(database) == before


def test_imported_entity_id_cannot_be_unlinked(tmp_path, monkeypatch):
    _registered(monkeypatch)
    database = _db(tmp_path)
    refresh_entities(database, apply=True)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE extracted_projects SET imported_to_s02a=1")
    monkeypatch.setattr("s01b_project_extractor.stages.s4_entity.resolve_entity", lambda name: EntityResolution(
        "新辰集团", name, "verified_chinese", "hierarchy", None, name, "entity:group", None, name,
    ))
    before = _hash(database)
    report = refresh_entities(database, apply=True)
    assert report["rows"][0]["action"] == "requires_downstream_review"
    assert _hash(database) == before


def test_imported_null_id_with_changed_role_is_also_protected(tmp_path, monkeypatch):
    _registered(monkeypatch)
    database = _db(tmp_path, [{"imported_to_s02a": 1, "involvement_type": "EPC总承包"}])
    before = _hash(database)
    assert refresh_entities(database, apply=True)["rows"][0]["action"] == "requires_downstream_review"
    assert _hash(database) == before
