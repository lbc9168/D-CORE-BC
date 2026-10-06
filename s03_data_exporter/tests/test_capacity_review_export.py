"""Synthetic English evidence crosses the capacity transaction and real s03 export.

This is a contract test, not a real project approval or evidence-quality result.
"""
import csv
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from s02a_duplicate_merger.tools.duplicate_merger_tool import _init_db
from s02e_source_finder.capacity_apply import manifest_hash, prepare_plan, rehearse_plan
from s03_data_exporter import main


def test_english_capacity_rehearsal_exports_numeric_value_without_private_reference(tmp_path):
    source = tmp_path / "synthetic.db"
    with closing(_init_db(str(source))) as conn, conn:
        conn.execute("INSERT INTO canonical_projects(id,project_name,project_name_en,category,country,country_iso) "
                     "VALUES ('KEN-PV-9999','测试光伏站','Synthetic capacity fixture','光伏','肯尼亚','KEN')")
        conn.execute("INSERT INTO project_events(id,canonical_id,project_stage,publish_date,source_links) "
                     "VALUES ('KEN-PV-9999-01','KEN-PV-9999','签约','2026-01-15','[]')")
        conn.execute("INSERT INTO chinese_entities(event_id,implementing_entity,involvement_type) "
                     "VALUES ('KEN-PV-9999-01','中国电建','工程总承包')")
    with closing(sqlite3.connect(source)) as conn:
        conn.row_factory = sqlite3.Row
        before = dict(conn.execute("SELECT * FROM project_events").fetchone())
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    first = main.run(src_db=source, dst_dir=tmp_path / "before", web_data_dir=tmp_path / "before-web", allow_model_calls=False)
    assert first["event_rows"] == 0
    text = "Synthetic test evidence: on January 15, 2026, the contract for this 27 MWp solar project was signed."
    candidate = {
        "candidate_id": "synthetic-english-capacity", "project_id": "KEN-PV-9999", "event_id": "KEN-PV-9999-01",
        "capacity_mw": 27.0, "raw_value": "27", "raw_unit": "MWp", "scope": "project", "phase": "whole_project",
        "evidence": {"url": "https://english.example/private-fixture", "language": "en", "publisher": "Synthetic owner",
                     "source_type": "official", "quote": text, "content_text": text,
                     "content_sha256": hashlib.sha256(text.encode()).hexdigest()},
        "event_binding": {"project_id": "KEN-PV-9999", "event_id": "KEN-PV-9999-01", "project_stage": "签约",
                          "phase": "whole_project", "evidence_quote": text},
        "before": before, "status": "eligible", "conflict": False,
    }
    manifest = {"schema_version": "capacity-candidates-v1", "candidates": [candidate]}
    approval = {"schema_version": "capacity-approval-v1", "candidate_manifest_sha256": manifest_hash(manifest),
                "approved_candidate_ids": [candidate["candidate_id"]], "reviewer": "synthetic test only",
                "reviewed_at": "2026-10-05T00:00:00Z", "review_note": "Not a human approval or real evidence",
                "approval_kind": "developer_rehearsal"}
    plan = prepare_plan(source, manifest, approval)
    receipt = rehearse_plan(source, plan, tmp_path / "rehearsal")
    result = main.run(src_db=Path(receipt["database"]), dst_dir=tmp_path / "after", web_data_dir=tmp_path / "after-web", allow_model_calls=False)
    assert result["event_rows"] == 1
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    rows = json.loads((tmp_path / "after-web/projects_flat.json").read_text(encoding="utf-8"))
    assert len(rows) == 1 and rows[0]["capacity_mw"] == 27.0
    assert rows[0]["external_references"] == []
    with (tmp_path / "after-web/events_export.csv").open(encoding="utf-8-sig", newline="") as stream:
        events = list(csv.DictReader(stream))
    assert len(events) == 1 and float(events[0]["capacity_mw"]) == 27.0
    for output in (tmp_path / "after-web").iterdir():
        assert "english.example" not in output.read_text(encoding="utf-8-sig")
    with closing(sqlite3.connect(receipt["database"])) as conn:
        assert "english.example" in conn.execute("SELECT evidence_json FROM capacity_review_audit").fetchone()[0]
