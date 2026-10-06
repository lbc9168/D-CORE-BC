"""Source learning, attribution, bounded billing and real pipeline boundaries, offline."""
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import test_source_loop
from s02e_source_finder import recurring
from s02e_source_finder.contracts import Page
from s02e_source_finder.fetch import publisher_identity_text
from s02e_source_finder.publishers import PublisherRegistry, verify_publisher
from s02e_source_finder.recurring_budget import RecurringBudget
from s02e_source_finder.source_admission import (SourceAdmission, SourceVerdict, VERSION, call_review,
    prepare_source_retries, review_spec, url_identity, validate_review)
from s02e_source_finder.structured_client import ExtractionError


class SourceAdmissionTests(unittest.TestCase):
    def setUp(self):
        test_source_loop.SourceLoopTests.setUp(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.page = self.page.model_copy(update={"url": "https://newcompany.cn/a", "final_url": "https://newcompany.cn/a",
            "site_name": "甲集团", "identity_text": "甲集团 版权所有", "http_status": 200,
            "checked_at": datetime.now(timezone.utc).isoformat()})
        self.registry = self.registry.model_copy(update={"records": [] , "affiliations": []})
        self.store = SourceAdmission(self.root / "sources.db")
        self.budget = RecurringBudget(self.root / "calls.db", ceiling=1, search_limit=2, extraction_limit=30)
        self.verdict = SourceVerdict(status="reliable", publisher="甲集团", source_type="official",
            hosting_kind="institutional", domain_identity_confirmed=True, origin_relation="same_group",
            original_publisher=None, publisher_quote="甲集团", identity_quote="甲集团 版权所有",
            origin_quote="来源：甲院", risks=[], reason="网页页脚与集团署名支持机构身份")
        self.call = {"usage": {"prompt_tokens": 100, "completion_tokens": 100}, "response_model": "gpt-6-luna"}

    def admit(self, verdict=None, page=None):
        with patch("s02e_source_finder.source_admission.call_review", return_value=(verdict or self.verdict, self.call)) as call:
            registry, audit = self.store.admit(page or self.page, self.registry, self.budget)
        return registry, audit, call.call_count

    def admitted(self):
        with closing(sqlite3.connect(self.store.path)) as conn:
            return conn.execute("SELECT host,distinct_urls FROM admitted_hosts").fetchall()

    def test_article_can_pass_before_six_urls_then_exact_host_is_learned(self):
        self.store.observe([f"https://newcompany.cn/{i}" for i in range(4)])
        registry, audit, calls = self.admit()
        self.assertEqual((audit["status"], calls), ("accepted", 1))
        self.assertIsNone(verify_publisher(self.page, registry)["problem"])
        self.assertEqual(self.admitted(), [])
        self.store.observe(["http://newcompany.cn/a?utm_source=again#x"] * 8)
        self.admit()
        self.assertEqual(self.admitted(), [])
        self.store.observe(["https://newcompany.cn/sixth"])
        _, _, calls = self.admit()
        self.assertEqual(calls, 0)
        self.assertEqual(self.admitted(), [("newcompany.cn", 6)])
        next_page = self.page.model_copy(update={"final_url": "https://newcompany.cn/next", "text": "甲集团新闻正文"})
        _, audit, calls = self.admit(page=next_page)
        self.assertEqual((audit["status"], calls), ("registered", 0))
        sibling = next_page.model_copy(update={"final_url": "https://sub.newcompany.cn/next"})
        self.assertEqual(verify_publisher(sibling, self.store.registry(self.registry))["problem"], "publisher_not_registered")

    def test_frequency_or_invented_evidence_alone_never_admits(self):
        self.store.observe([f"https://newcompany.cn/{i}" for i in range(10)])
        _, audit, _ = self.admit(self.verdict.model_copy(update={"status": "uncertain"}))
        self.assertEqual(audit["status"], "pending")
        self.assertEqual(self.admitted(), [])
        review, reason = validate_review(self.page, self.verdict.model_copy(update={"publisher_quote": "不存在的官网证据"}), "id")
        self.assertIsNone(review)
        self.assertIn("evidence_missing", reason)

    def test_shared_platform_article_is_allowed_but_host_is_not_promoted(self):
        self.store.observe([f"https://newcompany.cn/{i}" for i in range(20)])
        verdict = self.verdict.model_copy(update={"hosting_kind": "shared_platform", "domain_identity_confirmed": False})
        registry, audit, _ = self.admit(verdict)
        self.assertEqual(audit["status"], "accepted")
        self.assertIsNone(verify_publisher(self.page, registry)["problem"])
        self.assertEqual(self.admitted(), [])

    def test_editorial_media_can_correct_old_platform_label_for_one_host(self):
        self.registry = PublisherRegistry(version="publisher-registry-v1", reviewed_at="2026-10-05", reviewer="fixture",
            records=[{"publisher_id": "old", "name": "甲集团", "hosts": ["newcompany.cn", "sub.newcompany.cn"],
                      "source_type": "platform", "evidence": [{"url": self.page.url, "quote": "甲集团"}]}])
        self.store.observe([f"https://newcompany.cn/{i}" for i in range(6)])
        verdict = self.verdict.model_copy(update={"source_type": "media"})
        self.admit(verdict)
        learned = self.store.registry(self.registry)
        page = self.page.model_copy(update={"text": "甲集团新闻正文"})
        self.assertIsNone(verify_publisher(page, learned)["problem"])
        self.assertEqual(verify_publisher(page.model_copy(update={"final_url": "https://sub.newcompany.cn/a"}), learned)["problem"], "platform_original_article_needed")
        self.assertEqual(self.registry.records[0].source_type, "platform")

    def test_reprint_origin_retained_without_old_registry_membership(self):
        verdict = self.verdict.model_copy(update={"origin_relation": "reprint", "original_publisher": "甲院"})
        registry, audit, _ = self.admit(verdict)
        source = verify_publisher(self.page, registry)
        self.assertTrue(source["is_reprint"])
        self.assertEqual(source["original_publisher"], "甲院")
        self.assertIsNone(source["problem"])
        self.assertEqual(audit["status"], "accepted")
        other = self.page.model_copy(update={"text": self.page.text + "\n正文提到 Reuters"})
        wrong = verdict.model_copy(update={"original_publisher": "Reuters"})
        self.assertEqual(validate_review(other, wrong, "id")[1], "source_review_origin_evidence_missing")

    def test_page_review_cannot_leak_to_another_page_or_expired_evidence(self):
        registry, _, _ = self.admit()
        altered = self.page.model_copy(update={"text": "另一篇文章，未核实"})
        self.assertEqual(verify_publisher(altered, registry)["problem"], "publisher_not_registered")
        expired = self.page.model_copy(update={"checked_at": (datetime.now(timezone.utc) - timedelta(days=81)).isoformat()})
        self.assertEqual(validate_review(expired, self.verdict, "id")[1], "source_review_expired")

    def test_no_budget_defers_without_request_and_unknown_reservation_is_not_repeated(self):
        budget = RecurringBudget(self.root / "zero.db", ceiling=0, search_limit=1, extraction_limit=1)
        with patch("s02e_source_finder.source_admission.call_review", side_effect=AssertionError("No paid calls")):
            _, audit = self.store.admit(self.page, self.registry, budget)
        self.assertEqual(audit["reason"], "trial_budget_exhausted")
        spec = review_spec(self.page, self.registry)
        self.budget.reserve(spec, "new_extract", "openai")
        with patch("s02e_source_finder.source_admission.call_review", side_effect=AssertionError("Unknown request must not repeat")):
            _, audit = self.store.admit(self.page, self.registry, self.budget)
        self.assertEqual(audit["reason"], "reserved_request_unresolved")

    def test_sightings_deduplicate_concurrent_activity_and_tracking_variants(self):
        urls = ["https://newcompany.cn/a", "http://newcompany.cn/a?utm_source=x#b"] * 12
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda u: self.store.observe([u]), urls))
        with closing(sqlite3.connect(self.store.path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sightings").fetchone()[0], 1)
        self.assertNotEqual(url_identity("https://newcompany.cn/a?id=1"), url_identity("https://newcompany.cn/a?id=2"))

    def test_approved_source_still_requires_project_match_and_uses_same_budget(self):
        target = {"project_id": self.context.project_id, "event_id": self.context.event_id, "pool": "reference",
                  "context": self.context.model_dump(), "card": self.card.model_dump(mode="json"),
                  "candidates": [{"url": self.page.url}]}
        cfg = recurring.RecurringConfig(source_db=self.root / "main.db", raw_db=self.root / "raw.db", state_dir=self.root,
            publisher_registry=self.root / "registry.json", source_review_enabled=True, max_candidates_per_event=1)
        class Fetcher:
            calls = 0
            def fetch(inner, url):
                inner.calls += 1
                return self.page
        fetcher = Fetcher()
        from s02e_source_finder.pilot import digest
        (self.root / "pages").mkdir()
        old_page = self.page.model_dump()
        old_page.pop("identity_text")
        (self.root / "pages" / (digest([target["event_id"], 1, self.page.url]) + ".json")).write_text(json.dumps(old_page), encoding="utf-8")
        with patch("s02e_source_finder.source_admission.call_review", return_value=(self.verdict, self.call)), \
             patch("s02e_source_finder.structured_client.extract", return_value=(self.extraction, self.call)):
            result = recurring.process_target(target, 1, cfg, self.registry, self.budget, fetcher, admission=self.store)
        self.assertEqual(fetcher.calls, 1, "Pre-upgrade cache must be refreshed to collect footer identity")
        self.assertTrue(result["rows"][0]["external_reference"])
        self.assertEqual(self.budget.summary()["requests"]["new_extract"], 2)
        other = self.extraction.model_copy(update={"relation": "different_project"})
        with patch("s02e_source_finder.structured_client.extract", return_value=(other, self.call)):
            # A distinct target requests a distinct extraction; source review remains cached.
            target["context"]["project"]["project_name"] = "另一个项目"
            from s02e_source_finder.contracts import EventContext
            target["card"]["input_fingerprint"] = EventContext.model_validate(target["context"]).fingerprint()
            result = recurring.process_target(target, 1, cfg, self.registry, self.budget, Fetcher(), admission=self.store)
        self.assertIsNone(result["rows"][0]["external_reference"])
        self.assertEqual(result["rows"][0]["reason"], "different_project")

    def test_source_only_upgrade_requeues_old_rejections_without_new_search_generation(self):
        target = {"event_id": self.context.event_id, "project_id": self.context.project_id,
                  "context": self.context.model_dump(), "card": self.card.model_dump(mode="json"), "candidates": []}
        queue = self.root / "queue.db"
        recurring.sync_queue(queue, [target], "old-code", 80)
        with closing(sqlite3.connect(queue)) as conn:
            conn.execute("UPDATE jobs SET status='completed',attempts=1,checked_at=?", (datetime.now(timezone.utc).isoformat(),))
            conn.commit()
        self.assertEqual(recurring.sync_queue(queue, [target], "source-policy-only-change", 80, source_policy_upgrade=True), [])
        candidates = [{"event_id": self.context.event_id, "candidate_url": self.page.url,
                       "candidate_json": json.dumps({"reason": "publisher_not_registered", "reference": None})}]
        jobs = prepare_source_retries(queue, candidates)
        self.assertEqual((jobs[0]["generation"], jobs[0]["attempts"]), (1, 1))
        self.assertTrue(jobs[0]["source_review_retry"])
        self.assertEqual(json.loads(jobs[0]["target_json"])["candidates"], [{"url": self.page.url}])
        with closing(sqlite3.connect(queue)) as conn:
            conn.execute("UPDATE jobs SET status='completed',source_policy=?", (VERSION,))
            conn.commit()
        self.assertEqual(prepare_source_retries(queue, candidates), [])

    def test_footer_identity_is_separate_from_article_extraction(self):
        html = '<html><body><article>项目正文</article><footer>甲集团 版权所有</footer></body></html>'.encode()
        self.assertEqual(publisher_identity_text(html, "text/html", "utf-8"), "甲集团 版权所有")

    def test_invalid_model_json_preserves_usage_for_budget_accounting(self):
        class Response:
            status_code = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def json(self):
                return {"model": "gpt-6-luna", "usage": {"prompt_tokens": 9, "completion_tokens": 3},
                        "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}
        with patch.dict("os.environ", {"OPENAI_API_KEY": "fixture-not-real"}), \
             patch("s02e_source_finder.source_admission.requests.post", return_value=Response()):
            with self.assertRaises(ExtractionError) as caught:
                call_review(review_spec(self.page, self.registry))
        self.assertEqual(caught.exception.call["usage"]["completion_tokens"], 3)


if __name__ == "__main__":
    unittest.main()
