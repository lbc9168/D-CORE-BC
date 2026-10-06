"""Bounded recurring execution, resume and public export integration, without API calls."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import test_source_loop
from s02e_source_finder import recurring
from s02e_source_finder.recurring_budget import RecurringBudget
from s02e_source_finder.structured_client import request_spec
from s03_data_exporter.external_references import resolve_reference_snapshot


class RecurringTests(unittest.TestCase):
    def test_frozen_source_recheck_never_starts_a_new_search(self):
        cfg = recurring.RecurringConfig(source_db=self.root / "source.db", raw_db=self.root / "raw.db",
                                       state_dir=self.root, publisher_registry=self.root / "registry.json")
        self.target["candidates"] = [{"url": "https://unregistered.example/article"}]
        with patch.object(recurring, "search_once", side_effect=AssertionError("fresh search forbidden")):
            result = recurring.process_target(self.target, 1, cfg, self.registry, None, None, seeds_only=True)
        self.assertEqual(result["rows"][0]["reason"], "publisher_not_registered")

    def setUp(self):
        test_source_loop.SourceLoopTests.setUp(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.target = {"project_id": self.context.project_id, "event_id": self.context.event_id,
                       "pool": "reference", "context": self.context.model_dump(),
                       "card": self.card.model_dump(mode="json"), "candidates": []}

    def test_queue_replay_change_disappearance_and_expiry(self):
        path = self.root / "queue.db"
        first = recurring.sync_queue(path, [self.target], "rules", 80)
        self.assertEqual(len(first), 1)
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("UPDATE jobs SET status='completed',checked_at=?", (datetime.now(timezone.utc).isoformat(),))
            conn.commit()
        self.assertEqual(recurring.sync_queue(path, [self.target], "rules", 80), [])
        self.target["context"]["event"]["official_website_links"] = '["https://company.cn/a"]'
        self.assertEqual(recurring.sync_queue(path, [self.target], "rules", 80), [])
        self.target["context"]["event"]["project_summary"] = "new activity fact"
        changed = recurring.sync_queue(path, [self.target], "rules", 80)
        self.assertEqual(changed[0]["generation"], 2)
        self.assertEqual(recurring.sync_queue(path, [], "rules", 80), [])
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("UPDATE jobs SET status='completed',checked_at=?", ((datetime.now(timezone.utc)-timedelta(days=81)).isoformat(),))
            conn.commit()
        self.assertEqual(recurring.sync_queue(path, [self.target], "rules", 80)[0]["generation"], 3)

    def test_budget_unknown_request_replay_and_cumulative_authorization(self):
        path = self.root / "calls.sqlite"
        spec = request_spec({"test": 1}, "deepseek")
        budget = RecurringBudget(path, ceiling=1, search_limit=2, extraction_limit=1)
        self.assertTrue(budget.reserve(spec, "new_extract", "deepseek"))
        spent = budget.summary()["charged_upper_usd"]
        self.assertFalse(budget.reserve(spec, "new_extract", "deepseek"))
        self.assertEqual(budget.cached(spec)["error"], "reserved_request_unresolved")
        with self.assertRaisesRegex(ValueError, "budget_exhausted"):
            budget.reserve(request_spec({"test": 2}, "deepseek"), "new_extract", "deepseek")
        resumed = RecurringBudget(path, ceiling=2, search_limit=4, extraction_limit=2)
        self.assertEqual(resumed.summary()["charged_upper_usd"], spent)
        self.assertTrue(resumed.reserve(request_spec({"test": 2}, "deepseek"), "new_extract", "deepseek"))

    def test_transport_failure_keeps_reservation_and_cannot_buy_a_resume_retry(self):
        budget = RecurringBudget(self.root / "calls.sqlite", ceiling=1, search_limit=2, extraction_limit=3)
        spec = request_spec({"test": "response_lost_after_submission"}, "deepseek")
        budget.reserve(spec, "new_extract", "deepseek")
        budget.finish(spec, {"error": "ReadTimeout"}, failed=True)
        before = budget.summary()
        resumed = recurring.AttemptBudget(budget, 2)
        self.assertTrue(resumed.cached(spec)["request_outcome_unknown"])
        self.assertFalse(resumed.reserve(spec, "new_extract", "deepseek"))
        self.assertEqual(budget.summary(), before)

    def test_new_and_changed_increment_precedes_historical_backlog_on_resume(self):
        path = self.root / "queue.db"
        recurring.sync_queue(path, [self.target], "rules", 80)
        fresh = json.loads(json.dumps(self.target))
        fresh["event_id"] = fresh["context"]["event_id"] = "NEW-PV-0001-01"
        targets = [self.target, fresh]
        self.assertEqual(recurring.sync_queue(path, targets, "rules", 80)[0]["event_id"], fresh["event_id"])
        self.assertEqual(recurring.sync_queue(path, targets, "rules", 80)[0]["event_id"], fresh["event_id"])
        self.target["context"]["event"]["project_summary"] = "updated activity"
        self.assertEqual(recurring.sync_queue(path, targets, "rules", 80)[0]["event_id"], self.target["event_id"])
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("UPDATE jobs SET attempts=1 WHERE event_id=?", (self.target["event_id"],))
            conn.commit()
        self.assertEqual(recurring.sync_queue(path, targets, "rules", 80)[0]["event_id"], fresh["event_id"])

    def test_candidate_failure_does_not_hide_success_and_replay_calls_zero(self):
        cfg = recurring.RecurringConfig(source_db=self.root / "source.db", raw_db=self.root / "raw.db",
                                       state_dir=self.root, publisher_registry=self.root / "registry.json")
        self.target["candidates"] = [{"url": "https://company.cn/bad"}, {"url": "https://company.cn/a"}]
        class Fetcher:
            def fetch(inner, url):
                if url.endswith("bad"):
                    raise ValueError("fixture_bad_page")
                return self.page
        budget = RecurringBudget(self.root / "calls.sqlite", ceiling=1, search_limit=2, extraction_limit=2)
        with patch("s02e_source_finder.structured_client.extract", return_value=(self.extraction, {"usage": {"prompt_tokens": 10, "completion_tokens": 10}})) as call:
            first = recurring.process_target(self.target, 1, cfg, self.registry, budget, Fetcher())
            second = recurring.process_target(self.target, 1, cfg, self.registry, budget, Fetcher())
        self.assertEqual(call.call_count, 1)
        self.assertTrue(first["rows"][0]["external_reference"])
        self.assertTrue(second["rows"][0]["external_reference"])
        self.assertTrue(first["diagnostics"])

    def test_export_only_prefers_persisted_snapshot_but_preserves_explicit_override(self):
        config = self.root / "config.json"
        source = self.root / "source.db"
        config.write_text(json.dumps({"version": "external-reference-config-v1", "source_db": "source.db",
                                      "snapshot": "legacy.json", "recurring_snapshot": "current.json"}))
        self.assertEqual(resolve_reference_snapshot(source, reference_config=config), self.root / "legacy.json")
        (self.root / "current.json").write_text('{}')
        self.assertEqual(resolve_reference_snapshot(source, reference_config=config), self.root / "current.json")
        self.assertEqual(resolve_reference_snapshot(source, external_references=self.root / "explicit.json"), self.root / "explicit.json")

    def test_no_budget_creates_queue_without_network_or_main_db_mutation(self):
        source, raw = self.root / "source.db", self.root / "raw.db"
        source.write_bytes(b"unchanged-source")
        raw.write_bytes(b"unchanged-raw")
        registry = self.root / "registry.json"
        registry.write_text(self.registry.model_dump_json(), encoding="utf-8")
        config = self.root / "config.json"
        config.write_text(json.dumps({"source_db": "source.db", "raw_db": "raw.db", "state_dir": "state",
                                      "publisher_registry": "registry.json"}))
        with patch("s02e_source_finder.recurring_targets.build_targets", return_value=[self.target]), \
             patch("s02e_source_finder.recurring_targets.summarize_targets", return_value={}), \
             patch("s02e_source_finder.source_admission.prior_candidates", return_value=[]), \
             patch("s02e_source_finder.grounding.search", side_effect=AssertionError("network forbidden")), \
             patch("s02e_source_finder.structured_client.extract", side_effect=AssertionError("network forbidden")):
            result = recurring.run_incremental(source, raw, config)
        self.assertEqual(result["status"], "awaiting_budget")
        self.assertEqual(result["pending"], 1)
        self.assertEqual(source.read_bytes(), b"unchanged-source")
        self.assertEqual(raw.read_bytes(), b"unchanged-raw")
        self.assertFalse((self.root / "state/calls.sqlite").exists())

    def test_known_failure_can_resume_but_unknown_attempt_is_never_reissued(self):
        base = RecurringBudget(self.root / "calls.sqlite", ceiling=1, search_limit=2, extraction_limit=4)
        spec = request_spec({"test": "retry"}, "deepseek")
        base.reserve(spec, "new_extract", "deepseek")
        base.finish(spec, {"error": "provider_http_429"}, failed=True)
        retry = recurring.AttemptBudget(base, 1)
        self.assertIsNone(retry.cached(spec))
        self.assertTrue(retry.reserve(spec, "new_extract", "deepseek"))
        retry.finish(spec, {"extraction": {"test": True}, "call": {"usage": {"prompt_tokens": 1, "completion_tokens": 1}}})
        self.assertEqual(retry.cached(spec)["extraction"], {"test": True})
        later = recurring.AttemptBudget(base, 2)
        self.assertEqual(later.cached(spec)["extraction"], {"test": True})
        self.assertEqual(base.cached(spec)["error"], "provider_http_429")
        unknown = request_spec({"test": "interrupted"}, "deepseek")
        base.reserve(unknown, "new_extract", "deepseek")
        self.assertEqual(retry.cached(unknown)["error"], "reserved_request_unresolved")
        self.assertFalse(retry.reserve(unknown, "new_extract", "deepseek"))

    def test_concurrent_reservations_cannot_exceed_shared_limit(self):
        base = RecurringBudget(self.root / "calls.sqlite", ceiling=1, search_limit=2, extraction_limit=3)
        def reserve(index):
            try:
                return base.reserve(request_spec({"test": index}, "deepseek"), "new_extract", "deepseek")
            except ValueError as exc:
                self.assertEqual(str(exc), "trial_budget_exhausted")
                return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(reserve, range(12))), 3)
        self.assertEqual(base.summary()["requests"], {"new_extract": 3})

    def test_unknown_publisher_keeps_target_ids_and_does_not_buy_validation(self):
        cfg = recurring.RecurringConfig(source_db=self.root / "source.db", raw_db=self.root / "raw.db",
                                       state_dir=self.root, publisher_registry=self.root / "registry.json", max_candidates_per_event=1)
        self.target["candidates"] = [{"url": "https://unknown.cn/a"}]
        page = self.page.model_copy(update={"url": "https://unknown.cn/a", "final_url": "https://unknown.cn/a"})
        class Fetcher:
            def fetch(inner, url):
                raise AssertionError("Unregistered host needs institution review before article fetching")
        budget = RecurringBudget(self.root / "calls.sqlite", ceiling=1, search_limit=2, extraction_limit=2)
        with patch("s02e_source_finder.structured_client.extract", side_effect=AssertionError("untrusted source must not buy call")):
            result = recurring.process_target(self.target, 1, cfg, self.registry, budget, Fetcher())
        self.assertEqual(result["rows"][0]["project_id"], self.context.project_id)
        self.assertEqual(result["rows"][0]["event_id"], self.context.event_id)
        self.assertEqual(budget.summary()["requests"], {})

    def test_service_failure_remains_pending_and_next_run_can_finish(self):
        source, raw = self.root / "source.db", self.root / "raw.db"
        source.write_bytes(b"fixture-source")
        raw.write_bytes(b"fixture-raw")
        (self.root / "registry.json").write_text(self.registry.model_dump_json(), encoding="utf-8")
        config = self.root / "config.json"
        config.write_text(json.dumps({"source_db": "source.db", "raw_db": "raw.db", "state_dir": "state",
                                      "publisher_registry": "registry.json", "budget_usd": 1,
                                      "max_search_calls": 1, "max_model_calls": 1}))
        failure = {"target": self.target, "rows": [], "capacity_candidates": [], "deferred": False,
                   "service_blocked": True, "technical_failure": True, "diagnostics": ["provider_http_429"]}
        success = failure | {"service_blocked": False, "technical_failure": False, "diagnostics": [],
                             "rows": [{"external_reference": {"scope": "project"}}]}
        with patch("s02e_source_finder.recurring_targets.build_targets", return_value=[self.target]), \
             patch("s02e_source_finder.recurring_targets.summarize_targets", return_value={}), \
             patch("s02e_source_finder.source_admission.prior_candidates", return_value=[]), \
             patch("s02e_source_finder.recurring.process_target", side_effect=[failure, success]), \
             patch("s02e_source_finder.recurring_store.persist_results", return_value={}), \
             patch("s02e_source_finder.grounding.search", side_effect=AssertionError("No network")):
            first = recurring.run_incremental(source, raw, config)
            second = recurring.run_incremental(source, raw, config)
        self.assertEqual(first["processed"], 0)
        self.assertEqual(first["stop_reason"], "service_blocked")
        self.assertEqual(second["processed"], 1)
        with closing(sqlite3.connect(self.root / "state/queue.sqlite")) as conn:
            self.assertEqual(conn.execute("SELECT status,attempts FROM jobs").fetchone(), ("completed", 2))


if __name__ == "__main__":
    unittest.main()
