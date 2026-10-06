"""Bounded second traversal, using isolated fixtures and no external requests."""
import json
import sqlite3
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import test_recurring
import test_recurring_store
from s02e_source_finder import recurring
from s02e_source_finder.contracts import EventContext
from s02e_source_finder.recurring_budget import RecurringBudget
from s02e_source_finder.structured import TargetCard


class BoundedRetryTests(unittest.TestCase):
    def setUp(self):
        test_recurring.RecurringTests.setUp(self)
        self.cfg = recurring.RecurringConfig(source_db=self.root / "source.db", raw_db=self.root / "raw.db",
                                             state_dir=self.root, publisher_registry=self.root / "registry.json")
        self.old = [f"https://company.cn/old{i}" for i in range(4)]
        self.target["candidates"] = [{"url": url} for url in self.old]
        self.budget = RecurringBudget(self.root / "calls.sqlite", ceiling=1, search_limit=10, extraction_limit=10)
        self.fetched = []

    def fetcher(self, good=()):
        owner = self
        class Fetcher:
            def fetch(self, url):
                owner.fetched.append(url)
                return owner.page.model_copy(update={"url": url, "final_url": url,
                                                       "problem": None if url in good else "http_500"})
        return Fetcher()

    def search_result(self, urls):
        return {"grounding": {"groundingChunks": [{"web": {"uri": url, "title": "fixture"}} for url in urls]}}

    def run_retry(self, searches, *, good=(), judge=None, attempt=3):
        with patch.object(recurring, "search_once", side_effect=searches) as search, \
             patch.object(recurring, "resolve_citation", side_effect=lambda uri, _: uri), \
             patch.object(recurring, "judge_case", return_value=judge or {"decision": "project_reference",
                                                                         "external_reference": {"scope": "project"}}):
            result = recurring.process_target(self.target, 1, self.cfg, self.registry, self.budget,
                                               self.fetcher(good), bounded_retry=True, attempt=attempt)
        return result, search

    def test_four_old_then_four_plus_four_new_and_success_ends_work(self):
        first = [f"https://company.cn/new{i}" for i in range(4)]
        second = [f"https://company.cn/new{i}" for i in range(4, 9)]
        result, search = self.run_retry([self.search_result(first), self.search_result(second)], good=[second[2]])
        self.assertEqual(self.fetched, self.old + first + second[:3])
        self.assertEqual(search.call_count, 2)
        self.assertTrue(result["rows"][-1]["external_reference"])
        self.assertFalse(result["retry_exhausted"])
        self.assertTrue(result["technical_failure"])  # Failed siblings do not erase the success.
        self.assertEqual(search.call_args_list[1].kwargs["excluded_urls"], sorted(self.old + first))

    def test_exhaustion_and_restart_never_replay_old_urls_or_search(self):
        first = [f"https://company.cn/new{i}" for i in range(4)]
        second = [f"https://company.cn/new{i}" for i in range(4, 8)]
        duplicates = ["http://company.cn/old0?utm_source=fixture", first[0], first[0]]
        result, search = self.run_retry([self.search_result(duplicates + first), self.search_result(first + second)])
        self.assertEqual(self.fetched, self.old + first + second)
        self.assertTrue(result["retry_exhausted"])
        self.assertEqual(search.call_count, 2)
        again, search = self.run_retry([AssertionError("No third search")], attempt=4)
        self.assertTrue(again["retry_exhausted"])
        self.assertEqual(len(self.fetched), 12)
        search.assert_not_called()

    def test_old_success_stops_before_new_search_and_remaining_old_pages(self):
        result, search = self.run_retry([AssertionError("Fresh search unnecessary")], good=[self.old[0]])
        self.assertEqual(self.fetched, self.old[:1])
        self.assertTrue(result["rows"][0]["external_reference"])
        search.assert_not_called()

    def test_new_alias_redirecting_to_old_url_is_not_validated_again(self):
        alias = "https://company.cn/alias"
        owner = self
        class Fetcher:
            def fetch(self, url):
                owner.fetched.append(url)
                return owner.page.model_copy(update={"url": url, "final_url": owner.old[0] if url == alias else url,
                                                       "problem": None if url == alias else "http_500"})
        with patch.object(recurring, "search_once", side_effect=[self.search_result([alias]), self.search_result([])]), \
             patch.object(recurring, "resolve_citation", side_effect=lambda uri, _: uri), \
             patch.object(recurring, "judge_case", side_effect=AssertionError("Old article must not be judged again")):
            result = recurring.process_target(self.target, 1, self.cfg, self.registry, self.budget, Fetcher(),
                                               bounded_retry=True, attempt=3)
        self.assertTrue(result["retry_exhausted"])
        self.assertEqual(result["rows"][-1]["reason"], "previously_tried_url_redirect")

    def test_provider_failure_preserves_cursor_and_resumes_without_exhaustion(self):
        with patch.object(recurring, "judge_case", side_effect=[
                {"external_reference": None, "reason": "provider_http_429", "processing_status": "failed"},
                {"external_reference": {"scope": "project"}, "decision": "project_reference"}]), \
             patch.object(recurring, "search_once", side_effect=AssertionError("No new search")):
            first = recurring.process_target(self.target, 1, self.cfg, self.registry, self.budget,
                                              self.fetcher([self.old[1]]), bounded_retry=True, attempt=3)
            second = recurring.process_target(self.target, 1, self.cfg, self.registry, self.budget,
                                               self.fetcher([self.old[1]]), bounded_retry=True, attempt=4)
        self.assertTrue(first["service_blocked"])
        self.assertFalse(first["retry_exhausted"])
        self.assertTrue(second["rows"][-1]["external_reference"])
        self.assertEqual(self.fetched, self.old[:2])  # The successful page was cached before 429.

    def test_queue_closes_success_reopens_old_empty_completion_and_keeps_exhausted(self):
        queue = self.root / "queue.sqlite"
        recurring.sync_queue(queue, [self.target], "rules", 80)
        with closing(sqlite3.connect(queue)) as conn, conn:
            conn.execute("UPDATE jobs SET status='completed',attempts=3")
        jobs = recurring.prepare_bounded_retries(queue, [])
        self.assertEqual(len(jobs), 1)
        now = datetime.now(timezone.utc)
        candidate = {"event_id": self.context.event_id, "candidate_url": self.old[0],
                     "context_sha256": self.context.fingerprint(),
                     "candidate_json": json.dumps({"decision": "project_reference", "reference": {
                         "checked_at": now.isoformat(), "valid_until": (now + timedelta(days=90)).isoformat()}})}
        self.assertEqual(recurring.prepare_bounded_retries(queue, [candidate]), [])
        with closing(sqlite3.connect(queue)) as conn, conn:
            self.assertEqual(json.loads(conn.execute("SELECT result_json FROM jobs").fetchone()[0])["outcome"], "qualified")
            conn.execute("UPDATE jobs SET result_json=?", (json.dumps({"search_policy": recurring.SEARCH_POLICY,
                                                                       "outcome": "no_reference_found"}),))
        self.assertEqual(recurring.prepare_bounded_retries(queue, []), [])
        self.assertEqual(recurring.sync_queue(queue, [self.target], "rules", 80), [])
        self.assertTrue(queue.with_name(f"before-{recurring.SEARCH_POLICY}.sqlite").exists())
        # Upstream changes still start a new generation.
        self.target["context"]["event"]["project_summary"] = "changed"
        self.assertEqual(recurring.sync_queue(queue, [self.target], "rules", 80)[0]["generation"], 2)
        self.assertEqual(len(recurring.prepare_bounded_retries(queue, [])), 1)

    def test_prohibited_legacy_url_does_not_abort_queue_preparation(self):
        queue = self.root / "queue.sqlite"
        self.target["candidates"].append({"url": "https://news.goalfore.cn/article/1"})
        recurring.sync_queue(queue, [self.target], "rules", 80)
        with closing(sqlite3.connect(queue)) as conn, conn:
            conn.execute("UPDATE jobs SET attempts=1 WHERE event_id=?", (self.context.event_id,))

        jobs = recurring.prepare_bounded_retries(queue, [])

        self.assertEqual(len(jobs), 1)
        prepared = json.loads(jobs[0]["target_json"])
        self.assertEqual(prepared["candidates"], [{"url": url} for url in self.old])

    def test_prohibited_url_in_saved_exclusions_does_not_abort_resume(self):
        progress_path = self.root / "results" / f"{self.target['event_id']}-1-{recurring.SEARCH_POLICY}.progress.json"
        progress_path.parent.mkdir(parents=True)
        progress = recurring.RetryProgress(fingerprint=EventContext.model_validate(self.target["context"]).fingerprint(),
                                           phase=1, index=0, old_urls=[],
                                           excluded_urls=["https://news.goalfore.cn/article/1"])
        progress_path.write_text(progress.model_dump_json(), encoding="utf-8")

        with patch.object(recurring, "search_once", return_value=self.search_result([])) as search:
            result = recurring.process_target(self.target, 1, self.cfg, self.registry, self.budget,
                                               self.fetcher(), bounded_retry=True, attempt=3)

        self.assertTrue(result["retry_exhausted"])
        self.assertEqual(search.call_count, 2)

    def test_fresh_request_has_separate_cache_and_four_candidates(self):
        empty = self.search_result([])
        with patch.object(recurring.grounding, "search", return_value=empty) as paid:
            recurring.search_once(self.target, self.card, 1, 1, self.budget)
            recurring.search_once(self.target, self.card, 1, 1, self.budget, fresh=True, excluded_urls=self.old)
            recurring.search_once(self.target, self.card, 1, 1, self.budget, fresh=True, excluded_urls=self.old)
        self.assertEqual(paid.call_count, 2)
        prompt = paid.call_args.args[0]["contents"][0]["parts"][0]["text"]
        self.assertIn("最多4个", prompt)
        self.assertIn(self.old[0], prompt)
        self.assertEqual(self.budget.summary()["requests"]["search"], 2)

    def test_no_source_terminal_outcome_is_not_requeued_by_stage(self):
        source, raw = self.root / "source.db", self.root / "raw.db"
        with closing(sqlite3.connect(source)):
            pass
        raw.write_bytes(b"unused fixture raw")
        (self.root / "registry.json").write_text(self.registry.model_dump_json(), encoding="utf-8")
        config = self.root / "config.json"
        config.write_text(json.dumps({"source_db": "source.db", "raw_db": "raw.db", "state_dir": "state",
                                      "publisher_registry": "registry.json", "budget_usd": 1,
                                      "max_search_calls": 10, "max_model_calls": 10}))
        failed = {"target": self.target, "rows": [], "capacity_candidates": [], "diagnostics": [],
                  "deferred": False, "service_blocked": False, "technical_failure": True}
        with patch("s02e_source_finder.recurring_targets.build_targets", return_value=[self.target]), \
             patch("s02e_source_finder.recurring_targets.summarize_targets", return_value={}), \
             patch("s02e_source_finder.recurring_store.persist_results", return_value={}), \
             patch.object(recurring, "process_target", side_effect=[failed, failed | {"retry_exhausted": True}]) as process:
            first = recurring.run_incremental(source, raw, config)
            second = recurring.run_incremental(source, raw, config)
            third = recurring.run_incremental(source, raw, config)
        self.assertEqual(first["processed"], 0)
        self.assertEqual(second["exhausted"], 1)
        self.assertEqual(second["pending"], 0)
        self.assertEqual(third["pending"], 0)
        self.assertEqual(process.call_count, 2)
        self.assertFalse(process.call_args_list[0].kwargs["bounded_retry"])
        self.assertTrue(process.call_args_list[1].kwargs["bounded_retry"])

    def test_retry_success_persists_reference_and_survives_stage_restart(self):
        fixture = test_recurring_store.RecurringStoreTests()
        fixture.setUp()
        self.addCleanup(fixture.temp.cleanup)
        context = EventContext.model_validate(fixture.context())
        target = {"event_id": "E1", "project_id": "P1", "pool": "reference", "context": context.model_dump(),
                  "card": TargetCard(project_id="P1", event_id="E1", input_fingerprint=context.fingerprint(),
                                     stage="投运", stage_ambiguous=True, scope_terms=[], time_start="2020-01-01",
                                     time_end="2020-12-31", time_precision="year", identity_conflicts=[], evidence=[],
                                     notes="fixture").model_dump(mode="json"), "candidates": [{"url": self.old[0]}]}
        (fixture.root / "registry.json").write_text(self.registry.model_dump_json(), encoding="utf-8")
        (fixture.root / "raw.db").write_bytes(b"unchanged raw")
        config = fixture.root / "config.json"
        config.write_text(json.dumps({"source_db": "main.db", "raw_db": "raw.db", "state_dir": "state",
                                      "publisher_registry": "registry.json", "budget_usd": 1,
                                      "max_search_calls": 10, "max_model_calls": 10}))
        good = "https://company.cn/fresh"
        fetcher = self.fetcher([good])
        fetcher.close = lambda: None
        with patch("s02e_source_finder.recurring_targets.build_targets", return_value=[target]), \
             patch("s02e_source_finder.recurring_targets.summarize_targets", return_value={}), \
             patch.object(recurring, "PublicFetcher", return_value=fetcher), \
             patch.object(recurring, "search_once", side_effect=[self.search_result([]), self.search_result([]),
                                                                  self.search_result([good])]) as search, \
             patch.object(recurring, "resolve_citation", side_effect=lambda uri, _: uri), \
             patch.object(recurring, "judge_case", return_value=fixture.row(good)):
            first = recurring.run_incremental(fixture.db, fixture.root / "raw.db", config)
            second = recurring.run_incremental(fixture.db, fixture.root / "raw.db", config)
            before_restart = fixture.db.read_bytes()
            third = recurring.run_incremental(fixture.db, fixture.root / "raw.db", config)
        self.assertEqual(first["processed"], 0)
        self.assertEqual(second["found_reference"], 1)
        self.assertEqual(third["pending"], 0)
        self.assertEqual(search.call_count, 3)
        self.assertEqual(fixture.db.read_bytes(), before_restart)
        snapshot = json.loads((fixture.root / "state/external-references.json").read_text())
        self.assertTrue(any(row.get("reference") for row in snapshot["candidates"]))
        with closing(sqlite3.connect(fixture.db)) as conn:
            self.assertEqual(conn.execute("SELECT value FROM unrelated").fetchone()[0], "unchanged")
            self.assertEqual(conn.execute("SELECT id FROM project_events ORDER BY id").fetchall(), [("E1",), ("E2",)])
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
