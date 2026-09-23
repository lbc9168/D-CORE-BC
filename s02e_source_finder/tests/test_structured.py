"""Real failure shapes exercise independent gates, not model-reported confidence flags."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from s02e_source_finder.contracts import EventContext, Page
from s02e_source_finder.pilot import PilotCase, PilotCorpus
from s02e_source_finder.structured import Extraction, Measurement, Observation, Span, TargetCard, TargetCards, actual_action, blocks, date_interval, date_supports_action, decide, measurement_evidence, measurement_value, model_payload, publication_date, span_text
from s02e_source_finder.structured_run import run
from s02e_source_finder.structured_client import extract, native_schema


class StructuredTests(unittest.TestCase):
    """Keep useful official positives while blocking the pilot's concrete false accepts."""

    def setUp(self):
        self.context = EventContext(project_id="EGY-ES-0001", event_id="EGY-ES-0001-01", project={"project_name": "本班储能", "country": "埃及"}, event={"project_stage": "开工", "storage_energy_mwh": 1000}, entities=[], source_articles=[])
        self.card = TargetCard(project_id=self.context.project_id, event_id=self.context.event_id, input_fingerprint=self.context.fingerprint(), stage="开工", scope_terms=[], time_start="2026-02-25", time_end="2026-02-25", time_precision="day", stage_ambiguous=False, identity_conflicts=[], evidence=[], notes="fixture")
        self.page = Page(url="https://www.ceec.net.cn/a", final_url="https://www.ceec.net.cn/a", checked_at="2026-09-06T00:00:00+00:00", title="本班项目", site_name="中国能源建设股份有限公司", text="发布时间：2026-02-25\n2月25日，埃及本班1000兆瓦时储能项目开工。\n项目位于埃及南部本班地区。")
        self.span = Span(block_ids=["b0002"])
        self.extraction = Extraction(language="zh", relation="same_project", identity_evidence=[self.span], identity_reason="具名项目", identity_conflicts=[], measurements=[], observations=[Observation(action="groundbreaking", actuality="actual", evidence=self.span, when={"expression": "2月25日", "evidence": self.span})], attribution_evidence=None)

    def test_actual_event_and_grounded_date_pass(self):
        result = decide(self.context, self.card, self.page, self.extraction)
        self.assertEqual(result["decision"], "event_reference")
        self.assertEqual(result["event_date"], ["2026-02-25", "2026-02-25"])

    def test_real_quote_with_wrong_action_cannot_pass(self):
        page = self.page.model_copy(update={"text": self.page.text.replace("开工", "中标")})
        self.assertEqual(decide(self.context, self.card, page, self.extraction)["decision"], "project_reference")

    def test_planned_word_overrides_claimed_actual_flag(self):
        page = self.page.model_copy(update={"text": self.page.text.replace("项目开工", "项目计划开工")})
        self.assertEqual(decide(self.context, self.card, page, self.extraction)["decision"], "project_reference")
        signed = Observation(action="signed", actuality="actual", evidence=self.span, when=None)
        self.assertTrue(actual_action(signed, "已签署合同，项目计划明年开工。"))

    def test_year_only_case_is_not_an_operating_event(self):
        page = self.page.model_copy(update={"text": "中国能源建设埃及本班储能项目案例展示项目容量一千兆瓦时\n2026年"})
        obs = Observation.model_validate(self.extraction.observations[0].model_dump() | {"action": "operating", "when": {"expression": "2026年", "evidence": self.span.model_dump()}})
        extraction = self.extraction.model_copy(update={"observations": [obs]})
        card = self.card.model_copy(update={"stage": "投入运营"})
        self.assertEqual(decide(self.context, card, page, extraction)["decision"], "project_reference")

    def test_existing_grid_connection_stage_mapping(self):
        page = self.page.model_copy(update={"text": self.page.text.replace("开工", "并网发电")})
        extraction = self.extraction.model_copy(update={"observations": [self.extraction.observations[0].model_copy(update={"action": "grid_connected"})]})
        self.assertEqual(decide(self.context, self.card.model_copy(update={"stage": "投入运营"}), page, extraction)["decision"], "event_reference")

    def test_substation_scope_and_different_event_date_downgrade(self):
        self.assertEqual(decide(self.context, self.card.model_copy(update={"scope_terms": ["纳磨3变电站"]}), self.page, self.extraction)["decision"], "project_reference")
        page = self.page.model_copy(update={"text": self.page.text.replace("2月25日", "4月20日")})
        observation = self.extraction.observations[0].model_copy(update={"when": None})
        observation = Observation.model_validate(observation.model_dump() | {"when": {"expression": "4月20日", "evidence": self.span.model_dump()}})
        self.assertEqual(decide(self.context, self.card, page, self.extraction.model_copy(update={"observations": [observation]}))["decision"], "project_reference")

    def test_platform_cannot_inherit_media_trust(self):
        page = self.page.model_copy(update={"final_url": "https://finance.sina.com.cn/roll/a", "site_name": "新浪财经", "text": self.page.text + "\n（来源：储能世界）"})
        self.assertEqual(decide(self.context, self.card, page, self.extraction)["reason_codes"], ["original_publisher_unverified"])

    def test_capacity_conflict_cannot_escape_as_project_reference(self):
        measure = Measurement(value=600, unit="MWh", kind="storage_energy", scope="whole_project", evidence=self.span)
        extraction = self.extraction.model_copy(update={"measurements": [measure]})
        page = self.page.model_copy(update={"text": self.page.text.replace("1000", "600")})
        self.assertEqual(decide(self.context, self.card, page, extraction)["decision"], "pending")
        self.assertFalse(measurement_value(Measurement(value=600, unit="MW", kind="generation_power", scope="whole_project", evidence=self.span), "600MWh"))

    def test_input_does_not_contain_target_stage_or_source_narrative(self):
        payload = model_payload(self.context.model_copy(update={"event": {"project_stage": "开工", "project_summary": "目标已开工"}, "source_articles": [{"content": "原文确认已开工"}]}), self.page)
        self.assertNotIn("project_stage", json.dumps(payload))
        self.assertNotIn("原文确认", json.dumps(payload, ensure_ascii=False))
        self.assertNotIn("目标已开工", json.dumps(payload, ensure_ascii=False))

    def test_dates_and_block_ids_fail_closed(self):
        self.assertIsNone(publication_date(self.page.model_copy(update={"text": "没有日期", "final_url": "https://www.ceec.net.cn/2026/2/25/a"})))
        self.assertIsNone(date_interval("2026年2月30日", None))
        self.assertIsNone(date_interval("明年", None))
        with self.assertRaisesRegex(ValueError, "not_consecutive"):
            span_text(Span(block_ids=["b0002", "b0001"]), blocks(self.page))

    def test_dateline_cannot_supply_missing_event_time(self):
        page = self.page.model_copy(update={"text": "发布时间：2026-02-25\n埃及本班一千兆瓦时储能项目正式举行开工仪式。"})
        obs = Observation(action="groundbreaking", actuality="actual", evidence=Span(block_ids=["b0001", "b0002"]), when={"expression": "2026-02-25", "evidence": {"block_ids": ["b0001"]}})
        self.assertEqual(decide(self.context, self.card, page, self.extraction.model_copy(update={"observations": [obs]}))["decision"], "project_reference")

    def test_another_actions_date_cannot_be_borrowed(self):
        obs = self.extraction.observations[0]
        for text in ("2月25日举行签约仪式。此前项目已开工。", "2月25日签约，此前项目已开工。", "2月25日签约，项目3月1日正式开工。"):
            self.assertFalse(date_supports_action(obs, text, "2月25日"))
        self.assertTrue(date_supports_action(obs, "2月25日，项目签约并正式开工。", "2月25日"))

    def test_date_cannot_borrow_actuality_or_scope_from_another_clause(self):
        """Require the dated clause to describe the actual action on the target facility."""
        obs = self.extraction.observations[0]
        self.assertFalse(date_supports_action(obs, "一期已开工，2月25日计划开工二期。", "2月25日"))
        self.assertFalse(date_supports_action(obs, "二期仍在筹备，2月25日一期正式开工。", "2月25日", ["二期"]))
        self.assertTrue(date_supports_action(obs, "2月25日，二期正式开工。", "2月25日", ["二期"]))

    def test_unknown_source_at_article_end_is_not_ignored(self):
        page = self.page.model_copy(update={"text": self.page.text + "\n" + "正文内容。" * 300 + "\n（来源：未经核实自媒体）"})
        self.assertEqual(decide(self.context, self.card, page, self.extraction)["reason_codes"], ["original_publisher_unverified"])

    def test_production_scale_is_grounded_and_kept_for_audit(self):
        measure = Measurement(value=2, unit="t/year", kind="production", scope="whole_project", evidence=self.span)
        resolved = measurement_value(measure, "项目年产能2万吨碳酸锂。")
        self.assertEqual(resolved["canonical_value"], 20000)
        self.assertEqual(resolved["model_value"], 2)
        self.assertIsNone(measurement_value(measure, "项目运输2万吨材料。"))

    def test_repeated_quantity_passages_are_individually_grounded(self):
        """Keep two complete quantity statements as separate quotations, with their IDs."""
        body = [{"id": "b0001", "text": "项目电压800千伏。"}, {"id": "b0002", "text": "中间段落。"}, {"id": "b0003", "text": "工程新建两座800千伏换流站。"}]
        measure = Measurement(value=800, unit="kV", kind="voltage", scope="whole_project", evidence={"block_ids": ["b0001", "b0003"]})
        resolved = measurement_evidence(measure, body)
        self.assertEqual(resolved["canonical_value"], 800)
        self.assertEqual([item["block_ids"] for item in resolved["independent_evidence"]], [["b0001"], ["b0003"]])
        body[2]["text"] = "工程新建两座600千伏换流站。"
        self.assertIsNone(measurement_evidence(measure, body))

    def test_quantity_cannot_be_stitched_across_a_missing_passage(self):
        """Refuse incomplete pieces that only become a quantity when concatenated."""
        measure = Measurement(value=800, unit="kV", kind="voltage", scope="whole_project", evidence={"block_ids": ["b0001", "b0003"]})
        body = [{"id": "b0001", "text": "本项目预算800"}, {"id": "b0002", "text": "万美元。另一个项目电压为600"}, {"id": "b0003", "text": "千伏。"}]
        self.assertIsNone(measurement_evidence(measure, body))
        reversed_measure = measure.model_copy(update={"evidence": Span(block_ids=["b0003", "b0001"])})
        with self.assertRaisesRegex(ValueError, "reordered"):
            measurement_evidence(reversed_measure, body)

    def test_invalid_action_span_preserves_valid_project_reference(self):
        obs = self.extraction.observations[0].model_copy(update={"evidence": Span(block_ids=["b0001", "b0003"])})
        result = decide(self.context, self.card, self.page, self.extraction.model_copy(update={"observations": [obs]}))
        self.assertEqual(result["decision"], "project_reference")
        self.assertEqual(result["event_checks"][0]["problem"], "evidence_blocks_not_consecutive")

    def test_gemini_uses_native_schema(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-only"}), patch("s02e_source_finder.structured_client.requests.post") as post:
            response = post.return_value.__enter__.return_value
            response.status_code = 200
            response.json.return_value = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": self.extraction.model_dump_json()}]}}], "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1}}
            extraction, _ = extract(model_payload(self.context, self.page), "gemini")
        self.assertEqual(extraction, self.extraction)
        config = post.call_args.kwargs["json"]["generationConfig"]
        self.assertEqual(config["responseJsonSchema"], native_schema())
        self.assertNotIn("maxItems", json.dumps(native_schema()))
        self.assertIn("maxItems", json.dumps(Extraction.model_json_schema()))
        self.assertNotIn("responseSchema", config)

    def test_offline_preview_does_not_create_ledger_or_call_model(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            corpus_path, cards_path = folder / "corpus.json", folder / "cards.json"
            corpus = PilotCorpus(selected_sha256="frozen", created_at=self.page.checked_at, event_count=1, source_hashes={"source": "hash"}, events_without_candidates=[], cases=[PilotCase(case_id="one", domain="main", split="development", context=self.context, candidate={}, page=self.page)])
            cards = TargetCards(version="target-cards-v1", reviewer="test", human_confirmed=False, selected_sha256="frozen", cards={self.context.event_id: self.card})
            corpus_path.write_text(corpus.model_dump_json(), encoding="utf-8")
            cards_path.write_text(cards.model_dump_json(), encoding="utf-8")
            with patch("s02e_source_finder.structured_run.private_path", side_effect=lambda path, **kwargs: path), patch("s02e_source_finder.structured_run.assert_sources"), patch("s02e_source_finder.structured_run.extract") as paid, patch("s02e_source_finder.structured_run.PilotStore") as store:
                result = run(corpus_path, cards_path, provider="deepseek")
            paid.assert_not_called()
            store.assert_not_called()
            self.assertFalse(result["execute"])
            self.assertEqual(len(list(folder.iterdir())), 2)

    def test_holdout_cannot_run_without_qualified_development(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            corpus_path, cards_path = folder / "corpus.json", folder / "cards.json"
            corpus = PilotCorpus(selected_sha256="frozen", created_at=self.page.checked_at, event_count=1, source_hashes={"source": "hash"}, events_without_candidates=[], cases=[PilotCase(case_id="one", domain="main", split="holdout", context=self.context, candidate={}, page=self.page)])
            cards = TargetCards(version="target-cards-v1", reviewer="test", human_confirmed=False, selected_sha256="frozen", cards={self.context.event_id: self.card})
            corpus_path.write_text(corpus.model_dump_json(), encoding="utf-8")
            cards_path.write_text(cards.model_dump_json(), encoding="utf-8")
            with patch("s02e_source_finder.structured_run.private_path", side_effect=lambda path, **kwargs: path), patch("s02e_source_finder.structured_run.assert_sources"), patch("s02e_source_finder.structured_run.extract") as paid:
                with self.assertRaisesRegex(ValueError, "holdout_requires_development_pass"):
                    run(corpus_path, cards_path, provider="deepseek", split="holdout", max_calls=8, execute=True)
            paid.assert_not_called()
            self.assertFalse((folder / "pilot.sqlite").exists())


if __name__ == "__main__":
    unittest.main()
