import ast
import json
import sqlite3
from pathlib import Path

import pytest

from bri_pipeline.historical_reprocess import MappingSpec, apply_plan, build_plan

ROOT = Path(__file__).resolve().parents[2]


def schema_fixture(path, main):
    file = ROOT / ("s02a_duplicate_merger/src/s02a_duplicate_merger/tools/duplicate_merger_tool.py" if main else "s01b_project_extractor/src/s01b_project_extractor/tools/project_extractor_tool.py")
    tree = ast.parse(file.read_text(encoding="utf-8"))
    with sqlite3.connect(path) as conn:
        if main:
            schema = next(n.value.value for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SCHEMA_SQL" for t in n.targets))
        else:
            schema = next(n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and "CREATE TABLE IF NOT EXISTS extracted_projects (" in n.value)
        conn.executescript(schema)


@pytest.fixture
def case(tmp_path):
    paths = {name: str(tmp_path / (name + ".db")) for name in ("source_extracted", "source_main", "candidate_extracted", "candidate_main")}
    url = "https://example.org/project"
    for label, path in paths.items():
        main = label.endswith("main")
        schema_fixture(path, main)
        candidate = label.startswith("candidate")
        ident = "NEW" if candidate else "OLD"
        rowid = 11 if candidate else 1
        with sqlite3.connect(path) as conn:
            if main:
                conn.execute("INSERT INTO canonical_projects(id,project_name,country_iso,category) VALUES(?,?,?,?)", (ident, "wind", "ARG", "风电"))
                conn.execute("INSERT INTO project_events(id,canonical_id,capacity_mw,storage_power_mw,total_investment_usd,source_links,verification_sources) VALUES(?,?,?,?,?,?,?)", (ident + "-01", ident, 9 if candidate else 4.5, None if candidate else 4.5, 60 if candidate else 6, json.dumps([url]), '["external-reference"]'))
                conn.execute("INSERT INTO chinese_entities(id,event_id,implementing_entity,involvement_type) VALUES(?,?,?,?)", (rowid, ident + "-01", "correct" if candidate else "wrong", "Unknown" if candidate else "Developer"))
                conn.execute("INSERT INTO canonical_projects(id,project_name) VALUES('untouched','keep')")
            else:
                conn.execute("INSERT INTO extracted_projects(id,source_link,capacity_mw,storage_power_mw,total_investment_usd,implementing_entity,involvement_type,imported_to_s02a) VALUES(?,?,?,?,?,?,?,?)", (rowid, url, 9 if candidate else 4.5, None if candidate else 4.5, 60 if candidate else 6, "correct" if candidate else "wrong", "Unknown" if candidate else "Developer", 0 if candidate else 1))
    mapping = MappingSpec(article_url=url, source_extracted_id=1, candidate_extracted_id=11, source_canonical_id="OLD", candidate_canonical_id="NEW", source_event_id="OLD-01", candidate_event_id="NEW-01", relations=[{"source_id": 1, "candidate_id": 11}], evidence_review_note="Reviewed original equipment quotation")
    return paths, mapping


def test_coordinated_apply_backup_ids_fields_and_repeat(case, tmp_path):
    paths, mapping = case
    plan = build_plan(**paths, mapping=mapping)
    assert plan["status"] == "ready"
    backup = tmp_path / "backup"
    assert apply_plan(plan, backup)["status"] == "applied"
    assert apply_plan(plan, backup) == {"status": "already_applied", "changes": 0}
    with sqlite3.connect(paths["source_main"]) as conn:
        assert conn.execute("SELECT id,capacity_mw,storage_power_mw,total_investment_usd,verification_sources FROM project_events").fetchone() == ("OLD-01", 9, None, 60, '["external-reference"]')
        assert conn.execute("SELECT id,event_id,implementing_entity,involvement_type FROM chinese_entities").fetchone() == (1, "OLD-01", "correct", "Unknown")
        assert conn.execute("SELECT project_name FROM canonical_projects WHERE id='untouched'").fetchone() == ("keep",)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    with sqlite3.connect(paths["source_extracted"]) as conn:
        assert conn.execute("SELECT id,capacity_mw,imported_to_s02a FROM extracted_projects").fetchone() == (1, 9, 1)
    with sqlite3.connect(backup / "main.db") as conn:
        assert conn.execute("SELECT capacity_mw FROM project_events").fetchone() == (4.5,)


def test_stale_or_tampered_plan_rejected(case, tmp_path):
    paths, mapping = case
    plan = build_plan(**paths, mapping=mapping)
    plan["changes"][0]["after"]["capacity_mw"] = 1000
    with pytest.raises(ValueError, match="Stale or modified"):
        apply_plan(plan, tmp_path / "tampered")
    plan = build_plan(**paths, mapping=mapping)
    with sqlite3.connect(paths["source_main"]) as conn:
        conn.execute("UPDATE canonical_projects SET project_name='new human edit' WHERE id='untouched'")
    with pytest.raises(ValueError, match="Stale or modified"):
        apply_plan(plan, tmp_path / "stale")


@pytest.mark.parametrize("change", ["UPDATE canonical_projects SET name_en_quality='manual_verified' WHERE id='OLD'", "UPDATE project_events SET source_links='[\"https://example.org/project\",\"https://other.org\"]'", "INSERT INTO project_classification_shadow(canonical_id,classification_version,updated_at,manual_locked) VALUES('OLD','v1','today',1)"])
def test_ambiguous_or_manual_not_applicable(case, tmp_path, change):
    paths, mapping = case
    with sqlite3.connect(paths["source_main"]) as conn:
        conn.execute(change)
    plan = build_plan(**paths, mapping=mapping)
    assert plan["status"] == "requires_review"
    with pytest.raises(ValueError):
        apply_plan(plan, tmp_path / "no-write")


def test_candidate_pending_and_split_require_review(case):
    paths, mapping = case
    with sqlite3.connect(paths["candidate_extracted"]) as conn:
        conn.execute("UPDATE extracted_projects SET needs_review=1")
    assert build_plan(**paths, mapping=mapping)["status"] == "requires_review"
    with sqlite3.connect(paths["candidate_extracted"]) as conn:
        conn.execute("UPDATE extracted_projects SET needs_review=0")
        conn.execute("INSERT INTO extracted_projects(source_link) VALUES(?)", (mapping.article_url,))
    assert build_plan(**paths, mapping=mapping)["status"] == "requires_review"


def test_explicit_new_relation(case, tmp_path):
    paths, mapping = case
    with sqlite3.connect(paths["source_main"]) as conn:
        conn.execute("DELETE FROM chinese_entities")
    mapping = mapping.model_copy(update={"relations": []}).model_dump()
    mapping["relations"] = [{"source_id": None, "candidate_id": 11, "insert_id": 20}]
    plan = build_plan(**paths, mapping=mapping)
    assert plan["status"] == "ready"
    apply_plan(plan, tmp_path / "insert")
    with sqlite3.connect(paths["source_main"]) as conn:
        assert conn.execute("SELECT id,event_id FROM chinese_entities").fetchone() == (20, "OLD-01")


def test_rollback_both_databases_on_sql_failure(case, tmp_path):
    paths, mapping = case
    with sqlite3.connect(paths["source_main"]) as conn:
        conn.execute("CREATE TRIGGER reject_update BEFORE UPDATE ON chinese_entities BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
    plan = build_plan(**paths, mapping=mapping)
    with pytest.raises(sqlite3.IntegrityError, match="fixture failure"):
        apply_plan(plan, tmp_path / "rollback")
    with sqlite3.connect(paths["source_extracted"]) as conn:
        assert conn.execute("SELECT capacity_mw FROM extracted_projects").fetchone() == (4.5,)
    with sqlite3.connect(paths["source_main"]) as conn:
        assert conn.execute("SELECT capacity_mw FROM project_events").fetchone() == (4.5,)


def test_wal_production_mode_restored(case, tmp_path):
    paths, mapping = case
    for label in ("source_extracted", "source_main"):
        with sqlite3.connect(paths[label]) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
    plan = build_plan(**paths, mapping=mapping)
    assert apply_plan(plan, tmp_path / "wal")["status"] == "applied"
    for label in ("source_extracted", "source_main"):
        with sqlite3.connect(paths[label]) as conn:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_unexpected_non_target_trigger_rolls_back(case, tmp_path):
    paths, mapping = case
    with sqlite3.connect(paths["source_main"]) as conn:
        conn.execute("CREATE TRIGGER unwanted_change AFTER UPDATE ON chinese_entities BEGIN UPDATE canonical_projects SET project_name='oops' WHERE id='untouched'; END")
    plan = build_plan(**paths, mapping=mapping)
    with pytest.raises(RuntimeError, match="Unexpected target/non-target"):
        apply_plan(plan, tmp_path / "unexpected")
    with sqlite3.connect(paths["source_main"]) as conn:
        assert conn.execute("SELECT project_name FROM canonical_projects WHERE id='untouched'").fetchone() == ("keep",)


def test_manual_exclusion_preserved_and_candidate_disagreement(case):
    paths, mapping = case
    with sqlite3.connect(paths["source_main"]) as conn:
        conn.execute("UPDATE canonical_projects SET excluded=1,excluded_reason='人工裁决：保留排除' WHERE id='OLD'")
    assert build_plan(**paths, mapping=mapping)["status"] == "requires_review"
    with sqlite3.connect(paths["source_main"]) as conn:
        conn.execute("UPDATE canonical_projects SET excluded=0,excluded_reason=NULL WHERE id='OLD'")
    with sqlite3.connect(paths["candidate_main"]) as conn:
        conn.execute("UPDATE chinese_entities SET involvement_type='EPC'")
    plan = build_plan(**paths, mapping=mapping)
    assert plan["status"] == "requires_review"
    assert "Candidate extraction/entity relation disagree" in plan["reasons"]


def test_new_candidate_legacy_source_requires_explicit_migration(case):
    paths, mapping = case
    with sqlite3.connect(paths["candidate_extracted"]) as conn:
        conn.execute("ALTER TABLE extracted_projects ADD COLUMN publication_status TEXT")
        conn.execute("ALTER TABLE extracted_projects ADD COLUMN entity_evidence_json TEXT")
        conn.execute("UPDATE extracted_projects SET publication_status='eligible',entity_evidence_json='{}'")
    plan = build_plan(**paths, mapping=mapping)
    assert plan["status"] == "requires_review"
    assert any("schema_needs_migration" in reason and "publication_status" in reason and "entity_evidence_json" in reason for reason in plan["reasons"])


def test_eligible_noncritical_warning_allowed_but_core_error_blocked(case):
    paths, mapping = case
    for label in ("source_extracted", "candidate_extracted"):
        with sqlite3.connect(paths[label]) as conn:
            conn.execute("ALTER TABLE extracted_projects ADD COLUMN publication_status TEXT")
            conn.execute("ALTER TABLE extracted_projects ADD COLUMN entity_evidence_json TEXT")
            conn.execute("UPDATE extracted_projects SET publication_status='eligible',needs_review=1,entity_evidence_json=?", (json.dumps({"field_review_warnings": ["amount missing"], "structure_validation_errors": []}),))
    plan = build_plan(**paths, mapping=mapping)
    assert plan["status"] == "ready", plan["reasons"]
    with sqlite3.connect(paths["candidate_extracted"]) as conn:
        conn.execute("UPDATE extracted_projects SET entity_evidence_json=?", (json.dumps({"structure_validation_errors": ["project match uncertain"]}),))
    assert build_plan(**paths, mapping=mapping)["status"] == "requires_review"
    with sqlite3.connect(paths["candidate_extracted"]) as conn:
        conn.execute("UPDATE extracted_projects SET publication_status='pending_review',entity_evidence_json='{}'")
    assert build_plan(**paths, mapping=mapping)["status"] == "requires_review"


def test_legacy_both_sides_explicit_limitation(case):
    paths, mapping = case
    plan = build_plan(**paths, mapping=mapping)
    assert plan["status"] == "ready"
    assert any("Legacy candidate" in limitation for limitation in plan["limitations"])


def test_actual_stage_normalization_is_audited_not_blocked(case):
    paths, mapping = case
    with sqlite3.connect(paths["candidate_extracted"]) as conn:
        conn.execute("UPDATE extracted_projects SET capacity_mw=872,capacity_mw_raw='总装机容量为872MW',planned_start_date=NULL,planned_complete_date=NULL,total_investment_usd_raw=NULL,implementing_entity_en='',subsidiary_name_en=''")
    with sqlite3.connect(paths["candidate_main"]) as conn:
        conn.execute("UPDATE project_events SET capacity_mw='872.0',capacity_mw_raw='872.0',planned_start_date='',planned_complete_date='',total_investment_usd_raw=''")
        conn.execute("UPDATE chinese_entities SET implementing_entity_en='Goldwind',subsidiary_name_en=NULL")
    plan = build_plan(**paths, mapping=mapping)
    assert plan["status"] == "ready", plan["reasons"]
    assert {w["kind"] for w in plan["warnings"]} == {"raw_text_normalized", "derived_english_name"}
    with sqlite3.connect(paths["candidate_main"]) as conn:
        conn.execute("UPDATE project_events SET capacity_mw=87.2")
    plan = build_plan(**paths, mapping=mapping)
    assert plan["status"] == "requires_review"
    assert "Candidate extraction/event disagree: capacity_mw" in plan["reasons"]


def test_explicit_entity_id_conflict_remains_blocking(case):
    paths, mapping = case
    for label in ("source_extracted", "candidate_extracted"):
        with sqlite3.connect(paths[label]) as conn:
            conn.execute("ALTER TABLE extracted_projects ADD COLUMN entity_id TEXT")
            conn.execute("UPDATE extracted_projects SET entity_id='CORP-1'")
    for label in ("source_main", "candidate_main"):
        with sqlite3.connect(paths[label]) as conn:
            conn.execute("ALTER TABLE chinese_entities ADD COLUMN entity_id TEXT")
            conn.execute("UPDATE chinese_entities SET entity_id='CORP-2'")
    plan = build_plan(**paths, mapping=mapping)
    assert plan["status"] == "requires_review"
    assert "Candidate extraction/entity relation disagree" in plan["reasons"]


def test_historical_text_affinity_plan_matches_actual_storage(case, tmp_path):
    paths, mapping = case
    # Real production extraction has TEXT capacity, whereas isolated candidates
    # use the current REAL column. Recreate only this fixture with that old DDL.
    with sqlite3.connect(paths["source_extracted"]) as conn:
        schema = conn.execute("SELECT sql FROM sqlite_master WHERE name='extracted_projects'").fetchone()[0]
        cursor = conn.execute("SELECT * FROM extracted_projects")
        columns = [column[0] for column in cursor.description]
        rows = cursor.fetchall()
        conn.execute("DROP TABLE extracted_projects")
        import re
        conn.execute(re.sub(r"capacity_mw\s+REAL", "capacity_mw TEXT", schema))
        conn.executemany(f'INSERT INTO extracted_projects ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})', rows)
    plan = build_plan(**paths, mapping=mapping)
    extracted = next(c for c in plan["changes"] if c["table"] == "extracted_projects")
    assert extracted["after"]["capacity_mw"] == "9.0"
    assert apply_plan(plan, tmp_path / "text-affinity")["status"] == "applied"
    with sqlite3.connect(paths["source_extracted"]) as conn:
        assert conn.execute("SELECT capacity_mw,typeof(capacity_mw) FROM extracted_projects").fetchone() == ("9.0", "text")
