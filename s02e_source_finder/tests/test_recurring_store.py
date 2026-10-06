"""Reference persistence uses isolated SQLite fixtures, never production writes."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from s02e_source_finder.recurring_store import persist_results
from s03_data_exporter.external_references import load_references


class RecurringStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "main.db"
        self.snapshot = self.root / "references.json"
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.executescript("""
                CREATE TABLE canonical_projects(id TEXT PRIMARY KEY, project_name TEXT, country_iso TEXT,
                    category TEXT, excluded INTEGER);
                CREATE TABLE project_events(id TEXT PRIMARY KEY, canonical_id TEXT REFERENCES canonical_projects(id),
                    project_stage TEXT, publication_status TEXT, capacity_mw REAL, capacity_mw_raw TEXT,
                    capacity_details TEXT, capacity_source TEXT, source_links TEXT);
                CREATE TABLE chinese_entities(id TEXT PRIMARY KEY, event_id TEXT REFERENCES project_events(id), entity_name TEXT);
                CREATE TABLE unrelated(id INTEGER PRIMARY KEY, value TEXT);
                INSERT INTO unrelated VALUES(1,'unchanged');
                INSERT INTO canonical_projects VALUES('P1','Alpha','KEN','光伏',0),('P2','Beta','KEN','光伏',0);
                INSERT INTO project_events VALUES('E1','P1','投运','eligible',NULL,NULL,'original detail','original','[]');
                INSERT INTO project_events VALUES('E2','P2','投运','eligible',NULL,NULL,NULL,NULL,'[]');
                INSERT INTO chinese_entities VALUES('C1','E1','中方企业');
            """)
        self.target = {"pool": "capacity_missing", "context": self.context()}

    def context(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.row_factory = sqlite3.Row
            return {"project_id": "P1", "event_id": "E1",
                    "project": dict(conn.execute("SELECT * FROM canonical_projects WHERE id='P1'").fetchone()),
                    "event": dict(conn.execute("SELECT * FROM project_events WHERE id='E1'").fetchone()),
                    "entities": [dict(r) for r in conn.execute("SELECT * FROM chinese_entities WHERE event_id='E1'")],
                    "source_articles": [{"url": "https://example.com/original", "content": "PRIVATE ORIGINAL BODY"}]}

    def row(self, url="https://example.com/alpha"):
        now = datetime.now(timezone.utc) - timedelta(minutes=1)
        return {"case_id": url.rsplit("/", 1)[-1], "project_id": "P1", "event_id": "E1",
                "decision": "event_reference", "reason": "qualified", "evidence_language": "zh",
                "candidate_url": url, "page": {"text": "PRIVATE FETCHED BODY"},
                "external_reference": {"reference_id": hashlib.sha256(url.encode()).hexdigest()[:20],
                    "url": url, "title": "Alpha电站投运", "publisher": "Project owner", "source_type": "official",
                    "scope": "event", "event_id": "E1", "stage": "投运", "stage_en": "Commissioned",
                    "event_date": "2020", "checked_at": now.isoformat(), "valid_until": (now + timedelta(days=90)).isoformat(),
                    "is_reprint": False, "original_publisher": None}}

    def capacity(self, mw=100.0, unit="MWp"):
        raw = str(int(mw))
        body = f"Alpha solar power plant commissioned with {raw} {unit} generation capacity."
        return {"candidate_id": f"E1-{raw}-{unit}", "project_id": "P1", "event_id": "E1", "capacity_mw": mw,
                "raw_value": raw, "raw_unit": unit, "scope": "project", "phase": "whole_project",
                "evidence": {"url": "https://example.com/english", "language": "en", "publisher": "Owner",
                    "source_type": "official", "quote": body, "content_text": body,
                    "content_sha256": hashlib.sha256(body.encode()).hexdigest()},
                "event_binding": {"project_id": "P1", "event_id": "E1", "project_stage": "投运",
                    "phase": "whole_project", "evidence_quote": body},
                "before": deepcopy(self.target["context"]["event"]), "status": "eligible", "conflict": False}

    def persist(self, rows=None, capacities=None, **kwargs):
        return persist_results(self.db, [{"target": self.target, "rows": rows if rows is not None else [self.row()],
                                          "capacity_candidates": capacities or []}],
                               backup_dir=self.root / "backups", snapshot_path=self.snapshot, **kwargs)

    def query(self, sql):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute(sql).fetchall()

    def test_pending_snapshot_replay_does_not_duplicate_or_mislabel_language(self):
        row = self.row()
        row.update(decision="pending", reason="publisher_not_registered", external_reference=None)
        row.pop("evidence_language")
        self.persist([row])
        self.persist([row])
        row["case_id"] = "refetched-new-body-hash"
        self.persist([row])
        document = json.loads(self.snapshot.read_text(encoding="utf-8"))
        self.assertEqual(len(document["candidates"]), 1)
        _, audit = load_references(self.snapshot, self.db, strict=True)
        self.assertEqual(audit[0]["reason"], "publisher_not_registered")

    def test_references_backup_private_boundary_idempotence_and_s03_handoff(self):
        rows = [self.row()]
        before = self.db.read_bytes()
        result = self.persist(rows)
        backup = Path(result["backup"])
        manifest = json.loads(backup.with_suffix(".manifest.json").read_text())
        self.assertEqual(manifest["backup_sha256"], hashlib.sha256(backup.read_bytes()).hexdigest())
        with closing(sqlite3.connect(backup)) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='external_reference_candidates'").fetchone())
        self.assertTrue(before)
        public = self.snapshot.read_text(encoding="utf-8")
        stored = str(self.query("SELECT result_json FROM external_reference_candidates"))
        self.assertNotIn("PRIVATE", public + stored)
        accepted, audit = load_references(self.snapshot, self.db, strict=True)
        self.assertEqual(len(accepted["P1"]), 1)
        self.assertEqual(audit[0]["status"], "success")
        first_snapshot = self.snapshot.read_bytes()
        repeated = self.persist(rows)
        self.assertEqual(repeated["reference_rows_changed"], 0)
        self.assertEqual(first_snapshot, self.snapshot.read_bytes())
        self.assertEqual(self.query("SELECT * FROM unrelated"), [(1, "unchanged")])
        self.assertEqual(self.query("SELECT id FROM project_events ORDER BY id"), [("E1",), ("E2",)])

    def test_single_failed_url_downgrades_only_it_and_overrides_legacy(self):
        first, second = self.row(), self.row("https://example.com/second")
        self.persist([first, second])
        legacy = self.root / "legacy.json"
        legacy.write_bytes(self.snapshot.read_bytes())
        failed = first | {"external_reference": None, "decision": "pending", "reason": "page_not_readable"}
        self.persist([failed], legacy_snapshot=legacy)
        accepted, audit = load_references(self.snapshot, self.db, strict=True)
        self.assertEqual([r["reference"]["url"] for r in accepted["P1"]], [second["candidate_url"]])
        self.assertEqual(len(audit), 2)
        self.persist([])
        accepted, _ = load_references(self.snapshot, self.db, strict=True)
        self.assertEqual(len(accepted["P1"]), 1)

    def test_capacity_fill_audit_and_repeated_exact_input(self):
        capacity, rows = self.capacity(), [self.row()]
        result = self.persist(rows, [capacity])
        self.assertEqual(result["capacity_filled"], 1)
        value = self.query("SELECT capacity_mw,capacity_mw_raw,capacity_details,capacity_source FROM project_events WHERE id='E1'")[0]
        self.assertEqual((value[0], value[1], value[3]), (100.0, "100 MWp", "manual"))
        self.assertIn("[capacity_review:", value[2])
        self.assertIn("user-authorized recurring workflow", value[2])
        audit = self.query("SELECT approval_json,evidence_json FROM capacity_review_audit")[0]
        self.assertEqual(json.loads(audit[0])["approval_kind"], "workflow_authorized")
        self.assertFalse(json.loads(audit[0])["human_candidate_review"])
        self.assertNotIn("content_text", audit[1])
        self.assertEqual(self.query("SELECT capacity_mw FROM project_events WHERE id='E2'"), [(None,)])
        self.assertEqual(self.query("SELECT * FROM unrelated"), [(1, "unchanged")])
        repeated = self.persist(rows, [capacity])
        self.assertEqual(repeated["capacity_filled"], 0)
        self.assertEqual(repeated["reference_rows_changed"], 0)

    def test_s02a_guard_preserves_workflow_capacity_and_records_incoming_conflict(self):
        # The guard is offline; avoid importing S02A's provider-dependent package initializer.
        root = Path(__file__).resolve().parents[2]
        spec = importlib.util.spec_from_file_location("capacity_guard_integration", root / "s02a_duplicate_merger/src/s02a_duplicate_merger/tools/capacity_review_guard.py")
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        self.persist(capacities=[self.capacity()])
        with closing(sqlite3.connect(self.db)) as conn, conn:
            preserved = guard.capture_reviewed_capacity(conn, "E1", {"capacity_mw": 120, "capacity_details": "new disputed capacity"})
            self.assertEqual(preserved["capacity_mw"], 100.0)
            conn.execute("UPDATE project_events SET capacity_mw=120 WHERE id='E1'")
            guard.restore_reviewed_capacity(conn, "E1", preserved)
        self.assertEqual(self.query("SELECT capacity_mw FROM project_events WHERE id='E1'"), [(100.0,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM capacity_review_conflicts"), [(1,)])

    def test_legacy_reference_survives_and_current_identity_is_rechecked(self):
        self.persist()
        legacy = self.root / "legacy.json"
        legacy.write_bytes(self.snapshot.read_bytes())
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("DROP TABLE external_reference_candidates")
        self.snapshot.unlink()
        self.persist([], legacy_snapshot=legacy)
        accepted, _ = load_references(self.snapshot, self.db, strict=True)
        self.assertEqual(len(accepted["P1"]), 1)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE canonical_projects SET project_name='Renamed' WHERE id='P1'")
        persist_results(self.db, [], backup_dir=self.root / "backups", snapshot_path=self.snapshot)
        document = json.loads(self.snapshot.read_text(encoding="utf-8"))
        self.assertTrue(all(row["reference"] is None for row in document["candidates"]))
        self.assertTrue(all(row["reason"] == "reference_current_target_changed" for row in document["candidates"]))

    def test_conflicting_capacity_or_ac_dc_basis_is_retained_without_fill(self):
        for second in (self.capacity(120.0), self.capacity(100.0, "MWac")):
            with self.subTest(second=second["candidate_id"]):
                result = self.persist([], [self.capacity(), second])
                self.assertEqual(result["capacity_conflict_events"], ["E1"])
                self.assertEqual(self.query("SELECT capacity_mw FROM project_events WHERE id='E1'"), [(None,)])
                stored = json.loads(self.query("SELECT result_json FROM external_reference_candidates")[0][0])
                self.assertEqual(len(stored["capacity_candidates"]), 2)

    def test_frozen_identity_participation_and_capacity_before_drift_rejected(self):
        for sql in ("UPDATE canonical_projects SET project_name='Changed' WHERE id='P1'",
                    "UPDATE project_events SET canonical_id='P2' WHERE id='E1'",
                    "UPDATE chinese_entities SET entity_name='Changed'",
                    "UPDATE project_events SET capacity_mw=1 WHERE id='E1'"):
            with self.subTest(sql=sql):
                original = self.db.read_bytes()
                with closing(sqlite3.connect(self.db)) as conn, conn:
                    conn.execute(sql)
                current = self.db.read_bytes()
                with self.assertRaises(ValueError):
                    self.persist(capacities=[self.capacity()])
                self.assertEqual(self.db.read_bytes(), current)
                self.db.write_bytes(original)
        self.assertFalse((self.root / "backups").exists())

    def test_transaction_rolls_back_references_and_capacity_on_unrelated_trigger(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("CREATE TRIGGER tamper AFTER UPDATE OF capacity_mw ON project_events BEGIN UPDATE unrelated SET value='bad'; END")
        with self.assertRaisesRegex(ValueError, "Unexpected database mutation"):
            self.persist(capacities=[self.capacity()])
        self.assertEqual(self.query("SELECT * FROM unrelated"), [(1, "unchanged")])
        self.assertEqual(self.query("SELECT capacity_mw FROM project_events WHERE id='E1'"), [(None,)])
        self.assertEqual(self.query("SELECT name FROM sqlite_master WHERE name='external_reference_candidates'"), [])
        self.assertFalse(self.snapshot.exists())
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_nonempty_raw_capacity_is_not_overwritten(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE project_events SET capacity_mw_raw='unknown' WHERE id='E1'")
        self.target["context"] = self.context()
        with self.assertRaisesRegex(ValueError, "fill-null"):
            self.persist(capacities=[self.capacity()])
        self.assertEqual(self.query("SELECT capacity_mw_raw FROM project_events WHERE id='E1'"), [("unknown",)])


if __name__ == "__main__":
    unittest.main()
