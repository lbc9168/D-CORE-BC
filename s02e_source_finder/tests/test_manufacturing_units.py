"""Manufacturing annual throughput keeps its dimension and original unit evidence."""

import unittest

from s02e_source_finder.contracts import EventContext, Page
from s02e_source_finder.structured import Extraction, Measurement, TargetCard, decide, measurement_value, model_payload


class ManufacturingUnitTests(unittest.TestCase):
    def setUp(self):
        self.context = EventContext(project_id="SAU-MF-0007", event_id="SAU-MF-0007-01", project={"project_name": "沙特晶科工厂", "country": "沙特"}, event={"production_capacity_value": 10, "production_capacity_unit": "GW/年"}, entities=[], source_articles=[])
        self.page = Page(url="https://www.jinkosolar.com/a", final_url="https://www.jinkosolar.com/a", checked_at="2026-09-06T00:00:00+00:00", title="晶科能源", site_name="晶科能源", text="晶科能源沙特工厂项目年产能10GW，生产光伏电池及组件。")
        self.card = TargetCard(project_id=self.context.project_id, event_id=self.context.event_id, input_fingerprint=self.context.fingerprint(), stage="签约", scope_terms=[], time_start=None, time_end=None, time_precision="unknown", stage_ambiguous=False, identity_conflicts=[], evidence=[], notes="development fixture")

    def measure(self, unit="GW", kind="production", value=10):
        return Measurement(value=value, unit=unit, kind=kind, scope="whole_project", evidence={"block_ids": ["b0001"]})

    def decision(self, measure, page=None):
        extraction = Extraction(language="zh", relation="same_project", identity_evidence=[{"block_ids": ["b0001"]}], identity_reason="具名沙特晶科工厂", identity_conflicts=[], measurements=[measure], observations=[], attribution_evidence=None)
        return decide(self.context, self.card, page or self.page, extraction)

    def test_value_and_unit_reach_payload_and_annual_power_is_comparable(self):
        quantities = model_payload(self.context, self.page)["target_identity"]["quantities"]
        self.assertEqual((quantities["production_capacity_value"], quantities["production_capacity_unit"]), (10, "GW/年"))
        for unit, value in (("GW", 10), ("GW/年", 10), ("GW/year", 10), ("MW/year", 10000)):
            with self.subTest(unit=unit):
                result = self.decision(self.measure(unit=unit, value=value))
                self.assertEqual(result["decision"], "project_reference")
                self.assertEqual(result["measurements"][0]["canonical_value"], 10000)
                self.assertEqual(result["measurements"][0]["unit"], "MW/year")
                self.assertEqual(result["measurements"][0]["model_unit"], unit)
                self.assertEqual(result["measurements"][0]["raw_quantity"], "10GW")

    def test_annual_throughput_cannot_be_generation_or_borrow_another_clause(self):
        self.assertIsNone(measurement_value(self.measure(kind="generation_power"), self.page.text))
        self.assertIsNone(measurement_value(self.measure(), "该电站装机10GW。"))
        self.assertIsNone(measurement_value(self.measure(), "该电站装机10GW。另一个工厂年产组件。"))
        self.assertIsNone(measurement_value(self.measure(unit="GW/年", kind="generation_power"), "该厂10GW/年。"))
        self.assertIsNone(measurement_value(self.measure(unit="MWh"), "项目年产10MWh。"))
        resolved = measurement_value(self.measure(unit="GW/年"), "工厂产能10GW/年。")
        self.assertEqual(resolved["raw_quantity"], "10GW/年")

    def test_wrong_throughput_and_wrong_dimension_remain_hidden(self):
        page = self.page.model_copy(update={"text": self.page.text.replace("10GW", "12GW")})
        self.assertEqual(self.decision(self.measure(value=12), page)["reason_codes"], ["target_capacity_version_conflict"])
        page = self.page.model_copy(update={"text": "晶科能源沙特工厂项目年产能10吨，生产光伏材料并面向当地市场。"})
        self.assertEqual(self.decision(self.measure(unit="t/year"), page)["reason_codes"], ["measurement_not_grounded"])


if __name__ == "__main__":
    unittest.main()
