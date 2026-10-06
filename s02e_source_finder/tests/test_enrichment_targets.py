"""Private target eligibility must not relax publication, evidence, or isolation."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from s02e_source_finder.contracts import EventContext
from s02e_source_finder.enrichment_targets import (
    EnrichmentConfig, assign_components, build_pools, participation_evidence,
    pool_for_flat, project_terms, select_targets, target_card,
)
from s02e_source_finder.sample import MODULE, file_hash


class EligibilityTests(unittest.TestCase):
    def test_capacity_is_only_relaxed_public_field(self):
        from s03_data_exporter.events_exporter import passes_baseline
        row = {"category": "光伏", "country_iso": "IDN", "first_event_year": 2026,
               "last_event_year": 2026, "primary_entity": "中国电建", "capacity_mw": None}
        self.assertEqual(pool_for_flat(row, passes_baseline), ("capacity_missing", []))
        self.assertEqual(pool_for_flat({**row, "capacity_mw": 5}, passes_baseline), ("reference", []))
        for change, reason in (({"country_iso": "CHN"}, "excluded_country"),
                               ({"primary_entity": "Unknown"}, "unknown_primary_participant"),
                               ({"last_event_year": 2021}, "no_public_milestone_since_2022"),
                               ({"category": "Unknown"}, "unknown_category")):
            pool, reasons = pool_for_flat({**row, **change}, passes_baseline)
            self.assertIsNone(pool)
            self.assertIn(reason, reasons)
        for category in ("储能", "输变电建设", "新能源制造业"):
            self.assertEqual(pool_for_flat({**row, "category": category, "event_year": 2026}, passes_baseline), ("reference", []))

    def test_raw_participant_alias_needs_same_project_passage_not_entity_id(self):
        context = EventContext(project_id="X-PV-1", event_id="X-PV-1-01",
                               project={"project_name": "印尼梅拉40MW光伏项目", "country": "印尼"}, event={},
                               entities=[{"implementing_entity": "中国电力建设集团有限公司", "raw_entity_name": "中国电建", "entity_id": None}],
                               source_articles=[{"url": "https://example.com/a", "title": "光伏项目", "content": "中国电建承建印尼梅拉光伏项目。"}])
        evidence = participation_evidence(context, lambda entity: True)
        self.assertEqual(len(evidence), 1)
        self.assertIsNone(evidence[0]["entity_id"])
        context.source_articles[0]["content"] = "中国电建承建雅加达光伏项目。\n另一企业负责梅拉光伏项目。"
        self.assertEqual(participation_evidence(context, lambda entity: True), [])
        context.source_articles[0]["content"] = "中国电建承建梅拉光伏项目。"
        self.assertEqual(participation_evidence(context, lambda entity: False), [])

    def test_project_anchor_retains_phase_code_and_rejects_generic_facility(self):
        self.assertIn("PIF5", project_terms({"project_name": "沙特PIF5风电项目", "country": "沙特"}))
        self.assertNotIn("抽水蓄能", project_terms({"project_name": "乌兹别克斯坦霍吉肯特抽水蓄能项目", "country": "乌兹别克斯坦"}))

    def test_dates_are_not_fabricated_from_publication_or_plans(self):
        context = EventContext(project_id="X", event_id="X-01", project={"project_name": "梅拉光伏项目"},
                               event={"project_stage": "开工", "publish_date": "2026-03-10"}, entities=[], source_articles=[])
        card = target_card(context, [{"url": "https://example.com", "quote": "预计2026年4月1日开工。"}])
        self.assertIsNone(card.time_start)
        self.assertTrue(card.stage_ambiguous)
        self.assertIn("human_confirmed=false", card.notes)

    def test_read_only_projection_keeps_pending_and_old_events_out(self):
        with tempfile.TemporaryDirectory(dir=MODULE / "data") as directory:
            directory = Path(directory)
            main, raw = directory / "main.db", directory / "raw.db"
            with closing(sqlite3.connect(main)) as conn, conn:
                conn.executescript("""
                  CREATE TABLE canonical_projects (id TEXT, project_name TEXT, country TEXT, category TEXT, country_iso TEXT, excluded INTEGER);
                  CREATE TABLE project_events (id TEXT, canonical_id TEXT, project_stage TEXT, publish_date TEXT, capacity_mw REAL, source_links TEXT, publication_status TEXT);
                  CREATE TABLE chinese_entities (event_id TEXT, implementing_entity TEXT, raw_entity_name TEXT, involvement_type TEXT, publication_status TEXT);
                """)
                conn.execute("INSERT INTO canonical_projects VALUES (?,?,?,?,?,?)", ("IDN-PV-01", "印尼梅拉光伏项目", "印尼", "光伏", "IDN", 0))
                for number, year, status in ((1, "2026-01-01", "eligible"), (2, "2026-01-02", "pending_review"), (3, "2021-01-01", "eligible")):
                    event = f"IDN-PV-01-{number:02}"
                    conn.execute("INSERT INTO project_events VALUES (?,?,?,?,?,?,?)", (event, "IDN-PV-01", "开工", year, None, '["https://example.com/source"]', status))
                    conn.execute("INSERT INTO chinese_entities VALUES (?,?,?,?,?)", (event, "中国电力建设集团有限公司", "中国电建", "Unknown", "eligible"))
            with closing(sqlite3.connect(raw)) as conn, conn:
                conn.execute("CREATE TABLE raw_news (url TEXT,title TEXT,content TEXT,publish_date TEXT)")
                conn.execute("INSERT INTO raw_news VALUES (?,?,?,?)", ("https://example.com/source", "项目开工", "中国电建承建印尼梅拉光伏项目。", "2026-01-01"))
            before = {str(path): file_hash(path) for path in (main, raw)}
            rows, _ = build_pools(EnrichmentConfig(main_db=main, raw_db=raw), directory, {"event_ids": [], "project_ids": [], "normalized_urls": []})
            self.assertEqual([row["event_id"] for row in rows if row["eligible"]], ["IDN-PV-01-01"])
            self.assertIn("unknown_participation_role", rows[0]["unknown_issues"])
            self.assertIn("event_pending_or_excluded", rows[1]["exclusion_reasons"])
            self.assertEqual(before, {str(path): file_hash(path) for path in (main, raw)})


class IsolationTests(unittest.TestCase):
    def test_indirect_url_overlap_includes_different_scheme_and_fragment(self):
        rows = [
            {"project_id": "A", "event_id": "A-01", "source_urls": ["http://example.com/shared#x"], "candidates": []},
            {"project_id": "B", "event_id": "B-01", "source_urls": ["https://example.com/shared"], "candidates": [{"url": "https://example.com/second"}]},
            {"project_id": "C", "event_id": "C-01", "source_urls": ["https://example.com/second"], "candidates": []},
        ]
        assign_components(rows)
        self.assertEqual(len({row["leakage_group"] for row in rows}), 1)

    def test_deterministic_selection_never_reuses_connected_project(self):
        rows = []
        for pool in ("reference", "capacity_missing"):
            for number in range(6):
                rows.append({"pool": pool, "eligible": True, "event_id": f"{pool}-{number}", "project_id": f"{pool}-{number}",
                             "category_code": ["PV", "WD", "HY", "BM", "HB", "GT"][number], "country_iso": str(number),
                             "stage": "签约", "leakage_group": f"{pool}-{number}"})
        # A second event of a project must not take a second slot.
        rows.append({**rows[0], "event_id": "reference-extra"})
        first = select_targets(rows)
        self.assertEqual(first, select_targets(list(reversed(rows))))
        self.assertEqual(len(first), 10)
        self.assertEqual(len({row["leakage_group"] for row in first}), 10)


if __name__ == "__main__":
    unittest.main()
