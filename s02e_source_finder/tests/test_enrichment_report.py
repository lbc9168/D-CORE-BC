"""Offline review gates and metrics distinguish fixtures from real approvals."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from s02e_source_finder.contracts import EventContext
from s02e_source_finder.enrichment_report import QUANTITY, build_capacity_review, paired_metrics, rehearse_eligible_report
from s02e_source_finder.sample import MODULE, file_hash


class ReviewTests(unittest.TestCase):
    def test_report_quantity_parser_rejects_spaced_energy_units(self):
        for value in ("27 MW·h", "27 兆瓦·时", "27 MW h", "27 兆瓦 小时"):
            with self.subTest(value=value):
                self.assertIsNone(QUANTITY.search(value))
        self.assertIsNotNone(QUANTITY.search("27 MW hydro plant"))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=MODULE / "data")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "main.db"
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.executescript("""
                CREATE TABLE canonical_projects(id TEXT PRIMARY KEY, project_name TEXT, category TEXT, excluded INTEGER);
                CREATE TABLE project_events(id TEXT PRIMARY KEY, canonical_id TEXT REFERENCES canonical_projects(id),
                project_stage TEXT, capacity_mw REAL, capacity_mw_raw TEXT, capacity_details TEXT, capacity_source TEXT,
                publication_status TEXT, source_links TEXT);
            """)
            for index in range(10):
                pid = f"KEN-PV-{index + 1:04d}"
                conn.execute("INSERT INTO canonical_projects VALUES(?,?,?,0)", (pid, "Alpha光伏电站", "光伏"))
                conn.execute("INSERT INTO project_events VALUES(?,?,?,NULL,NULL,NULL,NULL,'eligible','[]')", (pid + "-01", pid, "投入运营"))
        self.events, self.cards = [], {}
        original = "2025年1月2日，中企承建的Alpha光伏电站投入运营。"
        with closing(sqlite3.connect(self.db)) as conn:
            conn.row_factory = sqlite3.Row
            for before in conn.execute("SELECT * FROM project_events ORDER BY id"):
                pid, eid = before["canonical_id"], before["id"]
                project = dict(conn.execute("SELECT * FROM canonical_projects WHERE id=?", (pid,)).fetchone())
                context = EventContext(project_id=pid, event_id=eid, project=project, event=dict(before),
                    entities=[{"implementing_entity": "中企", "publication_status": "eligible"}],
                    source_articles=[{"url": "https://cn.example/original", "content": original}])
                evidence = {"url": "https://cn.example/original", "quote": original,
                            "participant": "中企", "matched_alias": "中企", "project_anchor": "Alpha"}
                self.events.append({"project_id": pid, "event_id": eid, "pool": "capacity_missing", "context": context.model_dump(), "participation_evidence": [evidence]})
                self.cards[eid] = {"project_id": pid, "event_id": eid, "input_fingerprint": context.fingerprint(),
                    "stage": "投入运营", "scope_terms": ["Alpha"], "time_start": "2025-01-02", "time_end": "2025-01-02",
                    "time_precision": "day", "stage_ambiguous": False, "identity_conflicts": [], "evidence": [], "notes": "Synthetic fixture, not real approval"}
        text = "On January 2, 2025, Alpha solar power plant was commissioned with 27 MWp generation capacity."
        page = {"url": "https://developer.example/alpha", "final_url": "https://developer.example/alpha", "checked_at": "2026-10-05T00:00:00Z",
                "http_status": 200, "title": "Alpha commissioned", "text": text, "text_hash": hashlib.sha256(text.encode()).hexdigest()}
        self.case = {"case_id": "fixture", "event_id": self.events[0]["event_id"], "project_id": self.events[0]["project_id"],
                     "context": self.events[0]["context"], "card": self.cards[self.events[0]["event_id"]], "page": page}
        span = {"block_ids": ["b0001"]}
        extraction = {"language": "en", "relation": "same_project", "identity_evidence": [span], "identity_reason": "Alpha exact anchor",
                      "identity_conflicts": [], "participants": [], "attribution_evidence": None,
                      "measurements": [{"value": 27.0, "unit": "MWp", "kind": "generation_power", "scope": "whole_project", "evidence": span}],
                      "observations": [{"action": "operating", "actuality": "actual", "evidence": span,
                                        "when": {"expression": "January 2, 2025", "evidence": span}}]}
        self.rows = [{"case_id": "fixture", "project_id": self.case["project_id"], "event_id": self.case["event_id"], "provider": provider,
                      "decision": "pending", "reason": "english_not_public", "extraction": deepcopy(extraction), "call": {
                        "request_sha256": provider + "-request", "elapsed_seconds": 2.0, "usage": {"prompt_tokens": 100, "completion_tokens": 30}}}
                     for provider in ("deepseek", "openai")]
        self.registry = {"version": "publisher-registry-v1", "reviewed_at": "2026-10-05", "reviewer": "test fixture", "human_confirmed": False,
                         "records": [{"publisher_id": "developer", "name": "Developer", "hosts": ["developer.example"], "source_type": "official",
                                      "evidence": [{"url": "https://developer.example", "quote": "Developer official website"}]}]}

    def save(self, name, value):
        (self.root / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def freeze(self):
        self.save("selected-targets.json", {"events": self.events, "source_hashes": {str(self.db): file_hash(self.db)}})
        self.save("reviewed-target-cards.json", {"selection_sha256": file_hash(self.root / "selected-targets.json"), "cards": self.cards, "human_confirmed": False})
        self.save("publisher-registry.json", self.registry)
        self.save("new-corpus.json", {"cases": [self.case], "selection_sha256": file_hash(self.root / "selected-targets.json"),
                                      "reviewed_cards_sha256": file_hash(self.root / "reviewed-target-cards.json")})
        self.save("new-labels.json", {"corpus_sha256": file_hash(self.root / "new-corpus.json"), "human_confirmed": False, "reviewer": "test fixture",
                                     "cases": {"fixture": {"expected_decision": "pending", "acceptable_decisions": ["pending"], "capacity_expected_mw": 27.0, "reason": "English capacity evidence remains private"}}})
        self.save("new-comparison.json", {"split": "new", "freeze": {"corpus_sha256": file_hash(self.root / "new-corpus.json"),
                  "registry_sha256": file_hash(self.root / "publisher-registry.json"), "labels_sha256": file_hash(self.root / "new-labels.json")}, "rows": self.rows})

    def test_private_english_capacity_independent_of_public_decision(self):
        self.freeze()
        source_hash = file_hash(self.db)
        report = build_capacity_review(self.root, self.db)
        self.assertEqual(len(report["projects"]), 10)
        self.assertEqual(report["candidate_count"], 1)
        self.assertFalse(report["human_confirmed"])
        candidate = report["projects"][0]["proposals"][0]
        self.assertEqual(candidate["raw_unit"], "MWp")
        self.assertEqual(candidate["evidence"]["language"], "en")
        self.assertEqual(report["projects"][0]["after"]["source_links"], "[]")
        self.assertEqual(file_hash(self.db), source_hash)
        receipt = rehearse_eligible_report(self.db, self.root / "review")
        self.assertEqual(receipt["approval_kind"], "developer_rehearsal")
        self.assertEqual(file_hash(self.db), source_hash)

    def test_model_agreement_cannot_override_unknown_event_or_publisher(self):
        self.cards[self.case["event_id"]]["stage_ambiguous"] = True
        self.registry["records"][0]["hosts"] = ["other.example"]
        self.freeze()
        report = build_capacity_review(self.root, self.db)
        self.assertEqual(report["candidate_count"], 0)
        obs = report["projects"][0]["observations"][0]
        self.assertIn("existing_event_binding_not_proven", obs["reasons"])
        self.assertEqual(obs["quantities"][0]["normalized_mw"], 27.0)
        result = rehearse_eligible_report(self.db, self.root / "review")
        self.assertEqual(result["status"], "blocked_no_eligible_capacity_candidate")
        self.assertFalse((self.root / "review" / "developer-rehearsal-approval.json").exists())

    def test_quantity_from_neighbour_project_does_not_pass(self):
        self.case["page"]["text"] += "\nBeta solar has 27 MWp generation capacity."
        self.case["page"]["text_hash"] = hashlib.sha256(self.case["page"]["text"].encode()).hexdigest()
        for row in self.rows:
            row["extraction"]["measurements"][0]["evidence"] = {"block_ids": ["b0002"]}
        self.freeze()
        report = build_capacity_review(self.root, self.db)
        self.assertEqual(report["candidate_count"], 0)
        self.assertIn("capacity_clause_has_no_project_anchor", report["projects"][0]["reasons"])

    def test_same_paragraph_neighbour_project_cannot_borrow_anchor(self):
        self.case["page"]["text"] = "On January 2, 2025, Alpha solar power plant was commissioned. Beta solar has 27 MWp generation capacity."
        self.case["page"]["text_hash"] = hashlib.sha256(self.case["page"]["text"].encode()).hexdigest()
        self.freeze()
        report = build_capacity_review(self.root, self.db)
        self.assertEqual(report["candidate_count"], 0)
        self.assertIn("capacity_sentence_has_no_project_anchor", report["projects"][0]["reasons"])

    def test_stale_full_before_and_tampered_body_rejected(self):
        self.case["page"]["text_hash"] = "0" * 64
        self.freeze()
        report = build_capacity_review(self.root, self.db)
        self.assertEqual(report["candidate_count"], 0)
        self.assertIn("body_hash_mismatch", report["projects"][0]["reasons"])
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE project_events SET capacity_details='changed'")
        with self.assertRaises(ValueError):
            build_capacity_review(self.root, self.db, self.root / "stale")

    def test_metrics_differentiate_calls_format_evidence_and_page_failure(self):
        self.rows[0]["decision"] = "event_reference"
        self.rows[1]["extraction"]["identity_evidence"] = [{"block_ids": ["nonexistent"]}]
        self.rows[1]["reason"] = "evidence_or_projection_invalid:ValueError"
        self.freeze()
        report = paired_metrics(self.root / "new-comparison.json", self.root / "new-corpus.json", self.root / "new-labels.json")
        self.assertEqual(report["providers"]["deepseek"]["false_event_allow"], 1)
        self.assertEqual(report["providers"]["openai"]["evidence_or_projection_invalid"], 1)
        self.assertEqual(report["providers"]["openai"]["format_failure"], 0)
        self.assertEqual(report["providers"]["deepseek"]["actual_call_records"], 1)
        self.assertFalse(report["human_confirmed"])
        self.case["page"]["problem"] = "body_unreadable"
        self.rows = [{k: v for k, v in row.items() if k not in {"extraction", "call"}} | {"decision": "pending", "reason": "body_unreadable"} for row in self.rows]
        self.freeze()
        report = paired_metrics(self.root / "new-comparison.json", self.root / "new-corpus.json", self.root / "new-labels.json")
        self.assertEqual(report["providers"]["openai"]["page_problem_cases"], 1)
        self.assertEqual(report["providers"]["openai"]["actual_call_records"], 0)
        self.assertEqual(report["providers"]["openai"]["format_failure"], 0)


if __name__ == "__main__":
    unittest.main()
