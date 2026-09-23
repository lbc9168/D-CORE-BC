"""Publisher attribution and cached extraction exercise the new local discovery loop."""

import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from s02e_source_finder.contracts import EventContext, Page
from s02e_source_finder.fetch import parse_content, source_statements
from s02e_source_finder.pilot import PilotStore
from s02e_source_finder.publishers import PublisherRegistry, stated_origins, verify_publisher
from s02e_source_finder.structured import Extraction, TargetCard, decide
from s02e_source_finder.source_loop import LoopInputs, SeedPage, evaluate_page, project_reference, prompt_hash, stage_satisfied, write_frozen


class SourceLoopTests(unittest.TestCase):
    """Keep internal authorship, external reprints, and unknown platforms distinct."""

    def setUp(self):
        """Use a small official project article with an independently recorded subsidiary."""
        evidence = [{"url": "https://company.cn/about", "quote": "甲院为本集团成员单位"}]
        self.registry = PublisherRegistry(version="publisher-registry-v1", reviewed_at="2026-09-06", reviewer="fixture", records=[
            {"publisher_id": "group", "name": "甲集团", "hosts": ["company.cn"], "source_type": "official", "evidence": evidence},
            {"publisher_id": "member", "name": "甲院", "hosts": [], "source_type": "official", "evidence": evidence},
            {"publisher_id": "paper", "name": "人民日报", "hosts": [], "source_type": "media", "evidence": evidence}],
            affiliations=[{"owner_id": "group", "signatory_id": "member", "relationship": "same_group", "evidence": evidence}])
        self.context = EventContext(project_id="EGY-ES-0001", event_id="EGY-ES-0001-01", project={"project_name": "本班储能", "country": "埃及"}, event={"project_stage": "中标", "publish_date": "2026-02-25"}, entities=[], source_articles=[])
        self.card = TargetCard(project_id=self.context.project_id, event_id=self.context.event_id, input_fingerprint=self.context.fingerprint(), stage="中标", scope_terms=[], time_start="2026-02-25", time_end="2026-02-25", time_precision="day", stage_ambiguous=False, identity_conflicts=[], evidence=[], notes="fixture")
        self.page = Page(url="https://company.cn/a", final_url="https://company.cn/a", checked_at="2026-09-06T00:00:00+00:00", title="本班储能项目", text="来源：甲院\n发布时间：2026-02-25\n2月25日，甲院中标埃及本班一千兆瓦时储能项目，项目位于本班地区。")
        self.extraction = Extraction(language="zh", relation="same_project", identity_evidence=[{"block_ids": ["b0003"]}], identity_reason="具体项目", identity_conflicts=[], measurements=[], observations=[{"action": "awarded", "actuality": "actual", "evidence": {"block_ids": ["b0003"]}, "when": {"expression": "2月25日", "evidence": {"block_ids": ["b0003"]}}}], attribution_evidence=None)

    def test_internal_signature_is_not_an_unknown_reprint(self):
        """Accept an independently registered same-group signature."""
        result = verify_publisher(self.page, self.registry)
        self.assertIsNone(result["problem"])
        self.assertFalse(result["is_reprint"])

    def test_information_source_preserves_external_reprint(self):
        """Keep the external origin when a source label spans two lines."""
        page = self.page.model_copy(update={"text": self.page.text.replace("来源：甲院", "信息来源：\n人民日报")})
        result = verify_publisher(page, self.registry)
        self.assertEqual(result["original_publisher"], "人民日报")
        self.assertTrue(result["is_reprint"])

    def test_unknown_source_is_distinct_from_unregistered_host(self):
        """Distinguish unresolved authorship from an unreviewed hosting institution."""
        unknown = self.page.model_copy(update={"text": self.page.text.replace("来源：甲院", "来源：走出去情报")})
        self.assertEqual(verify_publisher(unknown, self.registry)["problem"], "original_publisher_unverified")
        self.assertEqual(verify_publisher(self.page.model_copy(update={"final_url": "https://other.cn/a"}), self.registry)["problem"], "publisher_not_registered")

    def test_article_extraction_retains_separate_visible_source(self):
        """Retain header attribution even when the article selector excludes it."""
        raw = ('<html><title>报道</title><div class="date-source"><span class="source">走出去情报</span></div><div id="artibody">' + '这是一篇项目报道。' * 20 + '</div></html>').encode()
        _, _, _, body = parse_content(raw, "text/html", "utf-8")
        self.assertNotIn("走出去情报", body)
        statements = source_statements(raw, "text/html", "utf-8")
        page = self.page.model_copy(update={"source_statements": statements})
        self.assertEqual(stated_origins(page)[0]["name"], "走出去情报")
        self.assertEqual(verify_publisher(page, self.registry)["problem"], "original_publisher_unverified")

    def test_cached_extraction_reaches_existing_reference_contract_without_call(self):
        """Project an exact cached extraction with no paid request or invented event date."""
        fingerprint = prompt_hash(self.context, self.page)
        seed = SeedPage(event_id=self.context.event_id, origin="legacy", page=self.page, parent_file="fixture", parent_sha256="fixture")
        inputs = LoopInputs(source_hashes={}, contexts={self.context.event_id: self.context}, cards={self.context.event_id: self.card}, seeds=[seed], reusable_extractions={fingerprint: {"extraction": self.extraction.model_dump(), "call": {"prompt_sha256": fingerprint}, "parent_file": "old-run", "parent_sha256": "old-hash"}})
        with tempfile.TemporaryDirectory() as directory, closing(PilotStore(Path(directory) / "calls.sqlite")) as store:
            store.begin("test", "inputs", "registry", "deepseek", 0)
            with patch("s02e_source_finder.source_loop.extract") as paid:
                result = evaluate_page(inputs, self.registry, seed, store, "test")
            paid.assert_not_called()
            self.assertEqual(result["decision"], "event_reference")
            reference = project_reference(self.context, result, self.registry)
            self.assertEqual(reference["event_date"], "2026-02-25")
            self.assertEqual(reference["publisher"], "甲集团")
            self.assertTrue(stage_satisfied([result], self.card))

    def test_incomplete_provider_response_is_candidate_local(self):
        """Retain known usage and signal provider failure after a malformed paid extraction."""
        from s02e_source_finder.structured_client import ExtractionError
        seed = SeedPage(event_id=self.context.event_id, origin="fixture", page=self.page, parent_file="fixture", parent_sha256="fixture")
        inputs = LoopInputs(source_hashes={}, contexts={self.context.event_id: self.context}, cards={self.context.event_id: self.card}, seeds=[seed], reusable_extractions={})
        with tempfile.TemporaryDirectory() as directory, closing(PilotStore(Path(directory) / "calls.sqlite")) as store:
            store.begin("test", "inputs", "registry", "deepseek", 1)
            with patch("s02e_source_finder.source_loop.extract", side_effect=ExtractionError("provider_incomplete_response", {"usage": {"total_tokens": 12}})), self.assertLogs("s02e_source_finder.source_loop", level="ERROR"):
                result = evaluate_page(inputs, self.registry, seed, store, "test")
            self.assertFalse(result.get("provider_failed", False))
            self.assertEqual(result["call"]["usage"]["total_tokens"], 12)
            self.assertEqual(result["decision"], "pending")

    def test_old_page_json_remains_compatible(self):
        """Load older page records without the optional source controls."""
        old = self.page.model_dump()
        old.pop("source_statements")
        self.assertEqual(Page.model_validate_json(json.dumps(old)).source_statements, [])

    def test_source_label_excludes_adjacent_date_and_keeps_inline_origin(self):
        """Separate a publisher from adjacent date fields and inline separators."""
        page = self.page.model_copy(update={"text": "文章来源：甲院　　发布时间：2024-04-08\n2021年12月08日16:35 | 来源：\n人民日报"})
        self.assertEqual([row["name"] for row in stated_origins(page)], ["甲院", "人民日报"])

    def test_reprint_keeps_locatable_alias_for_public_projection(self):
        """Keep the visible origin alias through validation and public projection."""
        self.registry.records[2].aliases.append("人民报")
        page = self.page.model_copy(update={"text": self.page.text.replace("来源：甲院", "来源：人民报")})
        source = verify_publisher(page, self.registry)
        self.assertEqual(source["original_publisher"], "人民报")
        self.assertEqual(source["original_publisher_canonical"], "人民日报")
        row = decide(self.context, self.card, page, self.extraction, self.registry)
        row.update(page=page.model_dump(), reason="supported_event")
        self.assertEqual(project_reference(self.context, row, self.registry)["original_publisher"], "人民报")

    def test_composite_editor_and_original_source_are_not_one_publisher(self):
        """Parse only explicit editorial/source roles and retain their original quotation."""
        page = self.page.model_copy(update={"text": "来源：甲集团整理，素材来源于人民日报官网\n" + self.page.text.split("\n", 1)[1]})
        source = verify_publisher(page, self.registry)
        self.assertIsNone(source["problem"])
        self.assertEqual(source["original_publisher"], "人民日报")
        ambiguous = page.model_copy(update={"text": "来源：甲集团联合人民日报\n正文"})
        self.assertEqual(verify_publisher(ambiguous, self.registry)["problem"], "original_publisher_unverified")

    def test_resume_preserves_frozen_outputs_and_rejects_different_data(self):
        """Resume identical audit artifacts without permitting an overwrite."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.json"
            write_frozen(path, {"version": 1})
            before = path.read_bytes()
            write_frozen(path, {"version": 1})
            self.assertEqual(path.read_bytes(), before)
            with self.assertRaisesRegex(ValueError, "frozen_output_changed"):
                write_frozen(path, {"version": 2})

    def test_missing_visible_publisher_stays_pending_without_aborting_batch(self):
        """Keep a source-projection failure local to its candidate before saving acceptance."""
        page = self.page.model_copy(update={"text": self.page.text.replace("来源：甲院", "报道正文").replace("甲院中标", "承包商中标")})
        fingerprint = prompt_hash(self.context, page)
        seed = SeedPage(event_id=self.context.event_id, origin="fixture", page=page, parent_file="fixture", parent_sha256="fixture")
        inputs = LoopInputs(source_hashes={}, contexts={self.context.event_id: self.context}, cards={self.context.event_id: self.card}, seeds=[seed], reusable_extractions={fingerprint: {"extraction": self.extraction.model_dump(), "call": {"prompt_sha256": fingerprint}, "parent_file": "fixture", "parent_sha256": "fixture"}})
        with tempfile.TemporaryDirectory() as directory, closing(PilotStore(Path(directory) / "calls.sqlite")) as store:
            store.begin("test", "inputs", "registry", "deepseek", 0)
            with self.assertLogs("s02e_source_finder.source_loop", level="ERROR"):
                result = evaluate_page(inputs, self.registry, seed, store, "test")
            self.assertEqual(result["decision"], "pending")
            self.assertEqual(result["reason_codes"], ["projection_publisher_evidence_missing"])


if __name__ == "__main__":
    unittest.main()
