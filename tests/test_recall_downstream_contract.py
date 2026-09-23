"""Accepted unnamed-registry relationships survive storage and optional-field gaps."""
import json

import pandas as pd

from s01b_project_extractor.tools.project_extractor_tool import _entity_record_metadata
from s01b_project_extractor.tools.storage_subcat_backfill import run as storage_backfill
from s01b_project_extractor.tools.txmf_backfill import run as txmf_backfill
from s02a_duplicate_merger.tools.duplicate_merger_tool import (
    _group_into_logical_projects, _init_db, _write_to_db,
)
from s03_data_exporter import events_exporter


def _project(category="光伏", details="", withheld=()):
    entity = {
        "Implementing Chinese Entity": "新辰能源", "Subsidiary Name": "",
        "Type of involvement of Chinese Entities": "Unknown",
        "_entity_verification_status": "model_supported_chinese",
        "_identity_standardization_pending": True,
        "Participation Assessment": {"participates": True, "is_chinese": True,
                                     "identity_basis": "model_knowledge"},
    }
    project = {"chinese_entities": [entity], "common_info": {
        "withheld_capacity_fields": list(withheld), "capacity_mw_parsed": None,
        "storage_power_mw_parsed": None, "storage_energy_mwh_parsed": None,
    }}
    metadata = _entity_record_metadata(entity, project, content="新辰能源参与此项目。")
    row = {
        "Source Link": "https://example.test/semantic", "Project Name": "测试项目",
        "Country": "肯尼亚", "Country ISO": "KEN", "Category": category,
        "Capacity (MW)": None, "Capacity Details": details, "Project Stage": "在建",
        "Publish Date": "2026-09-23", "Implementing Chinese Entity": "新辰能源",
        "Type of involvement of Chinese Entities": "Unknown",
        "publication_status": "eligible", "needs_review": 1,
        "generation_sources_json": '["光伏"]' if category == "光伏" else "[]",
        "Storage Status": "confirmed" if category == "储能" else "not_mentioned",
        **{key.removeprefix("_"): value for key, value in metadata.items()},
    }
    return _group_into_logical_projects(pd.DataFrame([row]))[0]


def test_unknown_role_unregistered_name_null_capacity_export_and_idempotency(tmp_path):
    connection = _init_db(str(tmp_path / "main.db"))
    project = _project()
    first = _write_to_db(connection, [project])
    ids_before = connection.execute("SELECT id FROM project_events").fetchall()
    again = _write_to_db(connection, [project])
    assert first["entity_new"] == 1 and again["entity_new"] == 0
    assert connection.execute("SELECT id FROM project_events").fetchall() == ids_before
    entity = connection.execute("SELECT entity_id,identity_status,involvement_type FROM chinese_entities").fetchone()
    assert entity == (None, "model_supported_chinese", "Unknown")
    assert connection.execute("SELECT capacity_mw FROM project_events").fetchone() == (None,)
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()
    # The export baseline must not reintroduce a capacity gate after accepted storage.
    assert events_exporter.passes_baseline({
        "country_iso": "KEN", "first_event_year": 2026, "last_event_year": 2026,
        "category": "光伏", "primary_entity": "新辰能源", "capacity_mw": None,
    })


def test_storage_backfill_respects_withheld_field_and_records_safe_repair(tmp_path):
    database = tmp_path / "main.db"
    connection = _init_db(str(database))
    project = _project("储能", "储能规模100MW/200MWh", ["storage_energy_mwh"])
    _write_to_db(connection, [project])
    connection.close()
    report = storage_backfill(str(database), apply=True)
    assert report["would_change"]["storage_power_mw"] == 1
    assert report["would_change"]["storage_energy_mwh"] == 0
    import sqlite3
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT storage_power_mw,storage_energy_mwh FROM project_events").fetchone() == (100.0, None)
        payload = json.loads(connection.execute("SELECT entity_evidence_json FROM chinese_entities").fetchone()[0])
    assert payload["field_validation"]["storage_power_mw"]["status"] == "repaired"
    assert payload["field_validation"]["storage_energy_mwh"]["status"] == "withheld"
    assert payload["field_validation_audit"][0]["previous"]["status"] == "unknown"
    assert not any(storage_backfill(str(database))["would_change"].values())


def test_txmf_backfill_cannot_restore_rejected_portfolio_spec(tmp_path):
    database = tmp_path / "main.db"
    connection = _init_db(str(database))
    project = _project("新能源制造业", "年产能5吉瓦", ["production_capacity_value", "production_capacity_unit"])
    _write_to_db(connection, [project])
    connection.close()
    assert not any(txmf_backfill(str(database), apply=True)["would_change"].values())


def test_nonparticipating_chinese_candidate_has_participation_reason_not_foreign_identity():
    entity = {"Implementing Chinese Entity": "中国企业", "_entity_verification_status": "model_supported_chinese",
              "_participation_evidence_status": "not_participating"}
    metadata = _entity_record_metadata(entity, {"entity_candidates": [entity], "chinese_entities": []})
    assert metadata["_identity_status"] == "model_supported_chinese"
    assert metadata["_publication_status"] == "excluded"
    assert metadata["_exclude_reason"] == "未参与当前目标项目"
    assert metadata["_needs_review"] == 0
