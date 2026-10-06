"""Real writer integration: approved capacity survives later incremental merging."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch

import pytest

from s02a_duplicate_merger.tools import duplicate_merger_tool as merger
from s02a_duplicate_merger.tools.capacity_review_guard import capture_reviewed_capacity

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
sys.path.insert(0, str(ROOT / "s02e_source_finder" / "src"))
from s02e_source_finder.capacity_apply import manifest_hash, prepare_plan, rehearse_plan


def _reviewed_database(tmp_path):
    source = tmp_path / "source.db"
    with closing(merger._init_db(str(source))) as conn, conn:
        conn.execute("""INSERT INTO canonical_projects(id,project_name,project_name_en,country_iso,category)
                        VALUES ('KEN-PV-0001','测试光伏项目','Test Solar','KEN','光伏')""")
        conn.execute("""INSERT INTO project_events(id,canonical_id,project_stage,source_links,capacity_details)
                        VALUES ('KEN-PV-0001-01','KEN-PV-0001','投运','[]','original detail')""")
        conn.row_factory = sqlite3.Row
        before = dict(conn.execute("SELECT * FROM project_events").fetchone())
    body = "Test Solar was commissioned at 100 MWac."
    candidate = {"candidate_id": "test-only", "project_id": "KEN-PV-0001", "event_id": "KEN-PV-0001-01",
                 "capacity_mw": 100.0, "raw_value": "100", "raw_unit": "MWac", "scope": "project", "phase": "whole_project",
                 "evidence": {"url": "https://private-evidence.example/english", "language": "en", "publisher": "Developer",
                              "source_type": "official", "quote": body, "content_text": body,
                              "content_sha256": hashlib.sha256(body.encode()).hexdigest()},
                 "event_binding": {"project_id": "KEN-PV-0001", "event_id": "KEN-PV-0001-01", "project_stage": "投运",
                                   "phase": "whole_project", "evidence_quote": body},
                 "before": before, "status": "eligible", "conflict": False}
    manifest = {"schema_version": "capacity-candidates-v1", "candidates": [candidate]}
    review = {"schema_version": "capacity-approval-v1", "candidate_manifest_sha256": manifest_hash(manifest),
              "approved_candidate_ids": ["test-only"], "reviewer": "developer fixture", "reviewed_at": "2026-10-05T00:00:00Z",
              "review_note": "Acceptance rehearsal only", "approval_kind": "developer_rehearsal"}
    plan = prepare_plan(source, manifest, review)
    result = rehearse_plan(source, plan, tmp_path / "rehearsal")
    return Path(result["database"])


@pytest.mark.parametrize("with_arbitration", [False, True])
def test_incremental_writer_preserves_manual_capacity_and_private_evidence(tmp_path, with_arbitration):
    db = _reviewed_database(tmp_path)
    with closing(sqlite3.connect(db)) as conn, conn:
        original_audit = conn.execute("SELECT * FROM capacity_review_audit").fetchone()
        incoming = merger._fetch_event_as_dict(conn, "KEN-PV-0001-01")
        incoming.update(capacity_mw="250", capacity_details="new disputed 250 MWp", source_links=["https://cn.example/later"])
        with patch.object(merger, "_merge_group", return_value=incoming):
            merger._write_to_db(conn, [incoming], client=object() if with_arbitration else None,
                                config=None, resolved_canonical_id="KEN-PV-0001")
        protected = conn.execute("SELECT capacity_mw,capacity_mw_raw,capacity_details,capacity_source,source_links FROM project_events").fetchone()
        assert protected[0:2] == (100.0, "100 MWac")
        assert protected[3] == "manual"
        assert "original detail" in protected[2] and "100 MWac" in protected[2]
        assert "private-evidence" not in protected[4]
        assert conn.execute("SELECT * FROM capacity_review_audit").fetchone() == original_audit
        conflict = json.loads(conn.execute("SELECT incoming_json FROM capacity_review_conflicts").fetchone()[0])
        assert conflict["capacity_mw"] == "250"
        assert conn.execute("SELECT COUNT(*) FROM project_events").fetchone()[0] == 1


def test_guard_does_not_protect_unreviewed_manual_values(tmp_path):
    with closing(merger._init_db(str(tmp_path / "normal.db"))) as conn:
        assert capture_reviewed_capacity(conn, "unreviewed", {"capacity_mw": "200"}) is None


def test_guard_fails_closed_on_approved_value_drift(tmp_path):
    db = _reviewed_database(tmp_path)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("UPDATE project_events SET capacity_mw=999")
        with pytest.raises(ValueError, match="drifted"):
            capture_reviewed_capacity(conn, "KEN-PV-0001-01", {"capacity_mw": "200"})
