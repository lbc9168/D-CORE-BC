"""Model isolation, bounded billing and bilingual evidence boundaries, without network calls."""

import hashlib
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from s02e_source_finder import structured_client
from s02e_source_finder.contracts import EventContext, Page
from s02e_source_finder.pilot import PilotStore, digest
from s02e_source_finder.publishers import PublisherRegistry
from s02e_source_finder.sample import MODULE
from s02e_source_finder.source_loop import LoopInputs, SeedPage, evaluate_page
from s02e_source_finder.structured import Extraction, Measurement, Span, TargetCard, decide, measurement_value, model_payload
from s02e_source_finder.trial_budget import TrialBudget, usage_cost
import test_source_loop


class ModelIsolationTests(unittest.TestCase):
    def setUp(self):
        test_source_loop.SourceLoopTests.setUp(self)
        self.seed = SeedPage(event_id=self.context.event_id, origin="test", page=self.page, parent_file="fixture", parent_sha256="fixture")
        self.inputs = LoopInputs(source_hashes={}, contexts={self.context.event_id: self.context}, cards={self.context.event_id: self.card}, seeds=[self.seed], reusable_extractions={})

    def test_luna_request_has_low_effort_and_distinct_request_identity(self):
        with patch.dict("os.environ", {"OPENAI_BASE_URL": "https://api.openai.com/v1"}):
            ds = structured_client.request_spec({"fixture": True}, "deepseek")
            luna = structured_client.request_spec({"fixture": True}, "openai")
        self.assertEqual(luna["request"]["model"], "gpt-6-luna")
        self.assertEqual(luna["request"]["reasoning_effort"], "low")
        self.assertNotIn("temperature", luna["request"])
        self.assertEqual(ds["prompt_sha256"], luna["prompt_sha256"])
        self.assertNotEqual(digest(ds), digest(luna))

    def test_different_model_cannot_reuse_deepseek_observation(self):
        with tempfile.TemporaryDirectory() as d, closing(PilotStore(Path(d) / "calls.sqlite")) as store:
            for provider in ("deepseek", "openai"):
                inputs = self.inputs.model_copy(update={"extraction_provider": provider})
                store.begin(provider, "input", "rules", provider, 1)
                with patch("s02e_source_finder.source_loop.extract", return_value=(self.extraction, {"usage": {}})) as extract:
                    row = evaluate_page(inputs, self.registry, self.seed, store, provider)
                extract.assert_called_once()
                self.assertEqual(row["decision"], "event_reference")
            self.assertEqual(store.conn.execute("SELECT sum(requested) FROM attempts").fetchone()[0], 2)

    def test_same_run_changed_endpoint_rejects_replay(self):
        inputs = self.inputs.model_copy(update={"extraction_provider": "openai"})
        with tempfile.TemporaryDirectory() as d, closing(PilotStore(Path(d) / "calls.sqlite")) as store:
            store.begin("luna", "input", "rules", "openai", 1)
            with patch.dict("os.environ", {"OPENAI_BASE_URL": "https://api.openai.com/v1"}), patch("s02e_source_finder.source_loop.extract", return_value=(self.extraction, {"usage": {}})):
                evaluate_page(inputs, self.registry, self.seed, store, "luna")
            with patch.dict("os.environ", {"OPENAI_BASE_URL": "https://different.example.com/v1"}):
                with self.assertRaisesRegex(ValueError, "request_identity_changed"):
                    evaluate_page(inputs, self.registry, self.seed, store, "luna")


class BillingTests(unittest.TestCase):
    def test_unknown_attempt_remains_reserved_and_cannot_retry(self):
        with tempfile.TemporaryDirectory(dir=MODULE / "data") as d:
            budget = TrialBudget(Path(d) / "calls.sqlite")
            spec = structured_client.request_spec({"x": 1}, "openai")
            self.assertTrue(budget.reserve(spec, "old_extract", "openai"))
            original = budget.summary()["charged_upper_usd"]
            self.assertFalse(budget.reserve(spec, "old_extract", "openai"))
            budget.finish(spec, {"error": "Timeout"}, failed=True)
            self.assertEqual(budget.summary()["charged_upper_usd"], original)
            self.assertEqual(budget.cached(spec)["error"], "Timeout")
            self.assertEqual(budget.summary()["requests"]["old_extract"], 1)

    def test_dollar_limit_checked_before_reserving(self):
        with tempfile.TemporaryDirectory(dir=MODULE / "data") as d:
            budget = TrialBudget(Path(d) / "calls.sqlite", ceiling=.001)
            with self.assertRaisesRegex(ValueError, "budget_exhausted"):
                budget.reserve(structured_client.request_spec({"x": 1}, "openai"), "old_extract", "openai")
            self.assertEqual(budget.summary()["requests"], {})

    def test_google_thoughts_are_billed_and_unknown_usage_not_zero(self):
        small = usage_cost({"usage": {"promptTokenCount": 100, "candidatesTokenCount": 200}}, "search")
        large = usage_cost({"usage": {"promptTokenCount": 100, "candidatesTokenCount": 200, "thoughtsTokenCount": 1000}}, "search")
        self.assertAlmostEqual(large - small, .0004)
        self.assertIsNone(usage_cost({"usage": {"promptTokenCount": 100}}, "search"))


class BilingualEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.context = EventContext(project_id="AAA-PV-0001", event_id="AAA-PV-0001-01", project={"project_name": "Alpha solar plant", "country": "Test"}, event={"project_stage": "开工", "capacity_mw": None}, entities=[], source_articles=[])
        self.card = TargetCard(project_id=self.context.project_id, event_id=self.context.event_id, input_fingerprint=self.context.fingerprint(), stage="开工", scope_terms=[], time_start="2024-03-12", time_end="2024-03-12", time_precision="day", stage_ambiguous=False, identity_conflicts=[], evidence=[], notes="fixture")
        text = "Example Power\nOn March 12, 2024, construction began on the Alpha solar plant with a total installed capacity of 25 MWp. This project is located in Test."
        self.page = Page(url="https://power.example.com/news", final_url="https://power.example.com/news", title="Alpha project", checked_at="2026-10-05T00:00:00+00:00", text=text, text_hash=hashlib.sha256(text.encode()).hexdigest())
        self.registry = PublisherRegistry(version="publisher-registry-v1", reviewed_at="2026-10-05", reviewer="test", records=[{"publisher_id": "power", "name": "Example Power", "hosts": ["power.example.com"], "source_type": "official", "evidence": [{"url": "https://power.example.com/about", "quote": "Example Power"}]}])
        self.extraction = Extraction(language="en", relation="same_project", identity_evidence=[Span(block_ids=["b0002"])], identity_reason="named", identity_conflicts=[], measurements=[Measurement(value=25,unit="MWp",kind="generation_power",scope="whole_project",evidence=Span(block_ids=["b0002"]))], observations=[{"action":"groundbreaking","actuality":"actual","evidence":{"block_ids":["b0002"]},"when":{"expression":"March 12, 2024","evidence":{"block_ids":["b0002"]}}}], attribution_evidence=None)

    def test_english_capacity_evidence_never_becomes_public_reference(self):
        private = decide(self.context, self.card, self.page, self.extraction, self.registry, allow_english=True)
        public = decide(self.context, self.card, self.page, self.extraction, self.registry)
        self.assertEqual(private["decision"], "event_reference")
        self.assertEqual(private["measurements"][0]["canonical_value"], 25)
        self.assertEqual(private["measurements"][0]["raw_quantity"], "25MWp")
        self.assertEqual(public["decision"], "pending")
        self.assertEqual(public["reason_codes"], ["candidate_language_not_confirmed_chinese"])

    def test_storage_units_cannot_support_generation_capacity(self):
        measurement = self.extraction.measurements[0]
        self.assertIsNone(measurement_value(measurement, "The battery stores 25 MWh of energy."))
        self.assertIsNone(measurement_value(measurement, "年产25MW组件"))

    def test_future_english_action_remains_project_evidence(self):
        page = self.page.model_copy(update={"text": self.page.text.replace("construction began", "construction is expected to begin")})
        result = decide(self.context, self.card, page, self.extraction, self.registry, allow_english=True)
        self.assertEqual(result["decision"], "project_reference")


if __name__ == "__main__":
    unittest.main()
