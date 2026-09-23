"""Exercise persisted request reuse and malformed-response audit without network access."""

import copy
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from s02e_source_finder import structured_client
from s02e_source_finder.pilot import PilotStore, digest
from s02e_source_finder.source_loop import LoopInputs, SeedPage, evaluate_page
import test_source_loop


class RequestReuseTests(unittest.TestCase):
    def setUp(self):
        test_source_loop.SourceLoopTests.setUp(self)
        self.seed = SeedPage(event_id=self.context.event_id, origin="fixture", page=self.page, parent_file="fixture", parent_sha256="fixture")
        self.inputs = LoopInputs(source_hashes={}, contexts={self.context.event_id: self.context}, cards={self.context.event_id: self.card}, seeds=[self.seed], reusable_extractions={})

    def test_new_rule_run_reuses_facts_and_rechecks_changed_registry(self):
        with tempfile.TemporaryDirectory() as directory, closing(PilotStore(Path(directory) / "calls.sqlite")) as store:
            store.begin("first", "inputs", "registry-v1", "deepseek", 1)
            with patch("s02e_source_finder.source_loop.extract", return_value=(self.extraction, {"usage": {"total_tokens": 12}})) as paid:
                first = evaluate_page(self.inputs, self.registry, self.seed, store, "first")
                paid.assert_called_once()
            self.assertEqual(first["decision"], "event_reference")
            original = store.conn.execute("SELECT result_json FROM attempts WHERE run_key='first'").fetchone()[0]
            changed = self.registry.model_copy(deep=True)
            changed.records = [record for record in changed.records if record.publisher_id != "member"]
            changed.affiliations = []
            store.begin("second", "inputs", "registry-v2", "deepseek", 0)
            with patch("s02e_source_finder.source_loop.extract", side_effect=AssertionError("must reuse")) as paid:
                second = evaluate_page(self.inputs, changed, self.seed, store, "second")
                paid.assert_not_called()
            self.assertTrue(second["reused_request"])
            self.assertEqual(second["decision"], "pending")
            self.assertEqual(second["reason_codes"], ["original_publisher_unverified"])
            self.assertEqual(store.conn.execute("SELECT result_json FROM attempts WHERE run_key='first'").fetchone()[0], original)
            self.assertEqual(store.conn.execute("SELECT SUM(requested) FROM attempts").fetchone()[0], 1)

    def test_auth_failure_stops_new_calls_but_consumes_cached_success(self):
        from s02e_source_finder.source_loop import prompt_hash
        cached_page = self.page.model_copy(update={"url": "https://company.cn/cached", "final_url": "https://company.cn/cached", "title": "已有成功正文"})
        cached_seed = self.seed.model_copy(update={"page": cached_page})
        fingerprint = prompt_hash(self.context, cached_page)
        self.inputs.reusable_extractions[fingerprint] = {"extraction": self.extraction.model_dump(), "call": {"prompt_sha256": fingerprint}, "parent_file": "fixture", "parent_sha256": "fixture"}
        deferred_seed = self.seed.model_copy(update={"page": self.page.model_copy(update={"url": "https://company.cn/new", "final_url": "https://company.cn/new", "title": "尚未抽取"})})
        with tempfile.TemporaryDirectory() as directory, closing(PilotStore(Path(directory) / "calls.sqlite")) as store:
            store.begin("auth", "inputs", "rules", "deepseek", 3)
            with patch("s02e_source_finder.source_loop.extract", side_effect=RuntimeError("provider_http_401")) as paid, self.assertLogs("s02e_source_finder.source_loop", level="ERROR"):
                failed = evaluate_page(self.inputs, self.registry, self.seed, store, "auth")
                success = evaluate_page(self.inputs, self.registry, cached_seed, store, "auth")
                deferred = evaluate_page(self.inputs, self.registry, deferred_seed, store, "auth")
            paid.assert_called_once()
            self.assertTrue(failed["provider_failed"])
            self.assertEqual(success["decision"], "event_reference")
            self.assertEqual(deferred["reason_codes"], ["validation_service_stopped"])

    def test_previous_format_failure_is_preserved_without_automatic_retry(self):
        call = {"usage": {"total_tokens": 7}, "raw_response": {"fixture": "malformed"}}
        with tempfile.TemporaryDirectory() as directory, closing(PilotStore(Path(directory) / "calls.sqlite")) as store:
            store.begin("first", "inputs", "rules-v1", "deepseek", 1)
            with patch("s02e_source_finder.source_loop.extract", side_effect=structured_client.ExtractionError("provider_invalid_extraction", call)), self.assertLogs("s02e_source_finder.source_loop", level="ERROR"):
                first = evaluate_page(self.inputs, self.registry, self.seed, store, "first")
            store.begin("second", "inputs", "rules-v2", "deepseek", 1)
            with patch("s02e_source_finder.source_loop.extract", side_effect=AssertionError("must not retry")) as paid:
                second = evaluate_page(self.inputs, self.registry, self.seed, store, "second")
                paid.assert_not_called()
            self.assertEqual(second["processing_status"], "failed")
            self.assertEqual(second["reason_codes"], first["reason_codes"])
            self.assertEqual(second["call"], call)
            self.assertFalse(second["model_called"])

    def test_format_failure_retains_usage_and_redacts_response_secret(self):
        secret = "fixture-secret-not-a-real-key"
        body = {"model": "fixture-model", "usage": {"total_tokens": 17}, "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"invalid_field": secret})}}]}
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": secret}), patch("s02e_source_finder.structured_client.requests.post") as post, self.assertLogs("s02e_source_finder.structured_client", level="ERROR"):
            response = post.return_value.__enter__.return_value
            response.status_code = 200
            response.json.return_value = body
            with self.assertRaises(structured_client.ExtractionError) as raised:
                structured_client.extract({"fixture": True}, "deepseek")
            post.assert_called_once()
        call = raised.exception.call
        self.assertEqual(call["usage"], {"total_tokens": 17})
        self.assertEqual(call["raw_response"]["model"], "fixture-model")
        self.assertNotIn(secret, json.dumps(call))
        self.assertIn("[redacted]", json.dumps(call["raw_response"]))
        self.assertTrue(call["validation_errors"])

    def test_generation_parameters_change_identity_and_match_sent_request(self):
        original = structured_client.request_spec({"fixture": True}, "deepseek")
        changed = copy.deepcopy(original)
        changed["request"]["max_tokens"] = 2048
        self.assertEqual(changed["prompt_sha256"], original["prompt_sha256"])
        self.assertNotEqual(digest(changed), digest(original))
        body = {"model": "fixture-model", "usage": {}, "choices": [{"finish_reason": "stop", "message": {"content": self.extraction.model_dump_json()}}]}
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "fixture-key"}), patch("s02e_source_finder.structured_client.request_spec", return_value=changed), patch("s02e_source_finder.structured_client.requests.post") as post:
            response = post.return_value.__enter__.return_value
            response.status_code = 200
            response.json.return_value = body
            _, call = structured_client.extract({"fixture": True}, "deepseek")
        self.assertEqual(post.call_args.kwargs["json"], changed["request"])
        self.assertEqual(call["request_sha256"], digest(changed))


if __name__ == "__main__":
    unittest.main()
