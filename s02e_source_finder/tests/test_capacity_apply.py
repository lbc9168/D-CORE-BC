"""Reviewed generation capacity transaction tests (temporary copies only)."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from s02e_source_finder.capacity_apply import (
    apply_plan, manifest_hash, prepare_plan, rehearse_plan,
)


class CapacityApplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "main.db"
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.executescript("""
                CREATE TABLE canonical_projects(id TEXT PRIMARY KEY, category TEXT, excluded INTEGER);
                CREATE TABLE project_events(id TEXT PRIMARY KEY, canonical_id TEXT REFERENCES canonical_projects(id),
                  project_stage TEXT, publication_status TEXT, capacity_mw REAL, capacity_mw_raw TEXT,
                  capacity_details TEXT, capacity_source TEXT, source_links TEXT);
                INSERT INTO canonical_projects VALUES ('KEN-PV-0001','光伏',0),('KEN-PV-0002','光伏',0);
                INSERT INTO project_events VALUES ('KEN-PV-0001-01','KEN-PV-0001','投运','eligible',NULL,NULL,'existing detail','original','["https://cn.example/original"]');
                INSERT INTO project_events VALUES ('KEN-PV-0002-01','KEN-PV-0002','投运','eligible',NULL,NULL,NULL,NULL,'[]');
                CREATE TABLE raw_news(id INTEGER PRIMARY KEY, text TEXT);
                INSERT INTO raw_news VALUES(1,'untouched');
            """)
        self.manifest = {"schema_version": "capacity-candidates-v1", "candidates": [self.candidate()]}

    def candidate(self, project_id="KEN-PV-0001"):
        event_id = project_id + "-01"
        with closing(sqlite3.connect(self.db)) as conn:
            conn.row_factory = sqlite3.Row
            before = dict(conn.execute("SELECT * FROM project_events WHERE id=?", (event_id,)).fetchone())
        body = "Kipeto solar project was commissioned with 100 MWp generation capacity."
        return {"candidate_id": project_id + "-capacity", "project_id": project_id, "event_id": event_id,
                "capacity_mw": 100.0, "raw_value": "100", "raw_unit": "MWp", "scope": "project", "phase": "whole_project",
                "evidence": {"url": "https://en.example/project", "language": "en", "publisher": "Developer",
                             "source_type": "official", "quote": body, "content_text": body,
                             "content_sha256": hashlib.sha256(body.encode()).hexdigest()},
                "event_binding": {"project_id": project_id, "event_id": event_id, "project_stage": "投运",
                                  "phase": "whole_project", "evidence_quote": body},
                "before": before, "status": "eligible", "conflict": False}

    def approval(self, manifest=None, kind="developer_rehearsal"):
        manifest = manifest or self.manifest
        return {"schema_version": "capacity-approval-v1", "candidate_manifest_sha256": manifest_hash(manifest),
                "approved_candidate_ids": [c["candidate_id"] for c in manifest["candidates"]],
                "reviewer": "developer test fixture (not human approval)", "reviewed_at": "2026-10-05T00:00:00Z",
                "review_note": "Test-only decision", "approval_kind": kind}

    def plan(self):
        return prepare_plan(self.db, self.manifest, self.approval())

    def test_fill_empty_audit_private_evidence_and_idempotence(self):
        source_hash = hashlib.sha256(self.db.read_bytes()).hexdigest()
        plan = self.plan()
        result = rehearse_plan(self.db, plan, self.root / "rehearsal")
        target = Path(result["database"])
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), source_hash)
        with closing(sqlite3.connect(target)) as conn:
            row = conn.execute("SELECT capacity_mw,capacity_mw_raw,capacity_details,capacity_source,source_links FROM project_events WHERE id='KEN-PV-0001-01'").fetchone()
            self.assertEqual((row[0], row[1], row[3]), (100.0, "100 MWp", "manual"))
            self.assertIn("existing detail", row[2])
            self.assertNotIn("en.example", str(row))
            self.assertIn("en.example", conn.execute("SELECT evidence_json FROM capacity_review_audit").fetchone()[0])
            self.assertEqual(conn.execute("SELECT capacity_mw FROM project_events WHERE id='KEN-PV-0002-01'").fetchone()[0], None)
            self.assertEqual(conn.execute("SELECT text FROM raw_news").fetchone()[0], "untouched")
        repeated = apply_plan(target, plan, plan["approved_manifest_hash"], self.root / "unused", rehearsal=True)
        self.assertEqual(repeated["status"], "already_applied")
        self.assertFalse((self.root / "unused").exists())

    def test_production_requires_human_exact_hash_and_opt_in(self):
        plan = self.plan()
        for kwargs in ({}, {"allow_production": True}, {"rehearsal": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                apply_plan(self.db, plan, plan["approved_manifest_hash"], self.root / "backup", **kwargs)
        with self.assertRaises(ValueError):
            apply_plan(self.db, plan, "a" * 64, self.root / "backup", allow_production=True)

    def test_unapproved_selection_or_value_rejected(self):
        approval = self.approval()
        changed = deepcopy(self.manifest)
        changed["candidates"][0]["capacity_mw"] = 120.0
        with self.assertRaises(ValueError):
            prepare_plan(self.db, changed, approval)
        approval["approved_candidate_ids"] = ["not-reviewed"]
        with self.assertRaises(ValueError):
            prepare_plan(self.db, self.manifest, approval)

    def test_drift_and_wrong_owner_rejected(self):
        plan = self.plan()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE raw_news SET text='changed'")
        with self.assertRaisesRegex(ValueError, "backup differs"):
            rehearse_plan(self.db, plan, self.root / "drift")
        wrong = deepcopy(self.manifest)
        wrong["candidates"][0]["project_id"] = "KEN-PV-0002"
        wrong["candidates"][0]["event_binding"]["project_id"] = "KEN-PV-0002"
        with self.assertRaisesRegex(ValueError, "owner"):
            prepare_plan(self.db, wrong, self.approval(wrong))

    def test_scope_unit_evidence_and_status_fail_closed(self):
        changes = [("raw_unit", "MWh"), ("raw_unit", "MWac"), ("capacity_mw", 101.0),
                   ("scope", "phase"), ("status", "pending"), ("conflict", True)]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                changed = deepcopy(self.manifest)
                changed["candidates"][0][field] = value
                with self.assertRaises(ValueError):
                    prepare_plan(self.db, changed, self.approval(changed))
        for field in ("quote", "content_sha256"):
            changed = deepcopy(self.manifest)
            changed["candidates"][0]["evidence"][field] = "invented"
            with self.assertRaises(ValueError):
                prepare_plan(self.db, changed, self.approval(changed))

    def test_chinese_units_preserved_and_energy_or_annual_units_rejected(self):
        for phrase, unit, raw, mw, accepted in (
            ("电站装机27兆瓦。", "兆瓦", "27", 27.0, True),
            ("电站装机2.7万千瓦。", "万千瓦", "2.7", 27.0, True),
            ("储能27兆瓦时。", "兆瓦", "27", 27.0, False),
            ("储能2.7万千瓦时。", "万千瓦", "2.7", 27.0, False),
            ("年产27兆瓦组件。", "兆瓦", "27", 27.0, False),
            ("产能27兆瓦/年。", "兆瓦", "27", 27.0, False),
            ("Storage 27 MW·h.", "MW", "27", 27.0, False),
            ("储能27 兆瓦·时。", "兆瓦", "27", 27.0, False),
            ("Storage 27 MW h.", "MW", "27", 27.0, False),
            ("储能27 兆瓦 小时。", "兆瓦", "27", 27.0, False),
            ("The 27 MW hydro plant was commissioned.", "MW", "27", 27.0, True),
        ):
            with self.subTest(phrase=phrase):
                changed = deepcopy(self.manifest)
                candidate = changed["candidates"][0]
                candidate.update(raw_value=raw, raw_unit=unit, capacity_mw=mw)
                candidate["evidence"].update(quote=phrase, content_text=phrase, content_sha256=hashlib.sha256(phrase.encode()).hexdigest())
                candidate["event_binding"]["evidence_quote"] = phrase
                if accepted:
                    prepare_plan(self.db, changed, self.approval(changed))
                else:
                    with self.assertRaises(ValueError):
                        prepare_plan(self.db, changed, self.approval(changed))

    def test_nonempty_excluded_pending_and_unknown_rejected(self):
        statements = ["UPDATE project_events SET capacity_mw=0", "UPDATE project_events SET capacity_mw_raw='unknown'",
                      "UPDATE canonical_projects SET excluded=1", "UPDATE canonical_projects SET category='Unknown'",
                      "UPDATE project_events SET publication_status='pending_review'"]
        for sql in statements:
            with self.subTest(sql=sql), closing(sqlite3.connect(self.db)) as conn:
                conn.execute("SAVEPOINT fixture")
                conn.execute(sql)
                conn.commit()
                changed = {"schema_version": "capacity-candidates-v1", "candidates": [self.candidate()]}
                with self.assertRaises(ValueError):
                    prepare_plan(self.db, changed, self.approval(changed))
                conn.execute("UPDATE project_events SET capacity_mw=NULL,capacity_mw_raw=NULL,publication_status='eligible'")
                conn.execute("UPDATE canonical_projects SET excluded=0,category='光伏'")
                conn.commit()

    def test_batch_rollback_including_audit_schema(self):
        self.manifest["candidates"].append(self.candidate("KEN-PV-0002"))
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("""CREATE TRIGGER reject_second BEFORE UPDATE OF capacity_mw ON project_events
                            WHEN NEW.id='KEN-PV-0002-01' BEGIN SELECT RAISE(ABORT,'injected failure'); END""")
        plan = self.plan()
        # Trigger schemas must also be frozen in the database fingerprint.
        with self.assertRaises(sqlite3.IntegrityError):
            rehearse_plan(self.db, plan, self.root / "rollback")
        with closing(sqlite3.connect(self.root / "rollback" / "rehearsal.db")) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM project_events WHERE capacity_mw IS NOT NULL").fetchone()[0], 0)
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='capacity_review_audit'").fetchone())


if __name__ == "__main__":
    unittest.main()
