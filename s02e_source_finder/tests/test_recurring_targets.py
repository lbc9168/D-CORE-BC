"""Recurring coverage reuses live s03 policies without changing source data."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from s02e_source_finder.recurring_targets import build_targets, pool_for_projection, summarize_targets
from s02e_source_finder.sample import file_hash


class RecurringTargetsTests(unittest.TestCase):
    def test_only_year_and_generation_capacity_are_relaxed(self):
        row = {"category": "光伏", "country_iso": "IDN", "primary_entity": "中国电建",
               "capacity_mw": 10, "first_event_year": 2018, "last_event_year": 2021}
        self.assertEqual(pool_for_projection(row, "main"), ("reference", "hidden_by_year"))
        self.assertEqual(pool_for_projection({**row, "last_event_year": 2022}, "main"), ("reference", "public"))
        self.assertEqual(pool_for_projection({**row, "capacity_mw": None}, "main"),
                         ("capacity_missing", "hidden_by_year_and_capacity"))
        for changes in ({"last_event_year": 2017}, {"country_iso": "CHN"},
                        {"primary_entity": "Unknown"}, {"first_event_year": None}, {"category": "火电"}):
            self.assertEqual(pool_for_projection({**row, **changes}, "main"), (None, None))
        self.assertEqual(pool_for_projection({**row, "category": "综合能源", "capacity_mw": None}, "main"), (None, None))
        self.assertEqual(pool_for_projection({**row, "category": "综合能源", "capacity_mw": None,
                                             "generation_sources": '["光伏"]'}, "main")[0], "capacity_missing")
        for domain in ("TX", "MF"):
            self.assertEqual(pool_for_projection({"country_iso": "IDN", "event_year": 2018}, domain),
                             ("reference", "hidden_by_year"))

    def _fixture(self, root):
        main, raw = root / "main.db", root / "raw.db"
        with closing(sqlite3.connect(main)) as conn, conn:
            conn.executescript("""
                CREATE TABLE canonical_projects (id TEXT, project_name TEXT, country TEXT,
                  category TEXT, country_iso TEXT, excluded INTEGER);
                CREATE TABLE project_events (id TEXT, canonical_id TEXT, project_stage TEXT,
                  publish_date TEXT, capacity_mw REAL, source_links TEXT, publication_status TEXT);
                CREATE TABLE chinese_entities (event_id TEXT, implementing_entity TEXT,
                  raw_entity_name TEXT, involvement_type TEXT, publication_status TEXT);
            """)
            for number, year, category, capacity, status, excluded in (
                (1, 2017, "光伏", 10, "eligible", 0),
                (2, 2018, "光伏", 10, "eligible", 0),
                (3, 2021, "光伏", 10, "eligible", 0),
                (4, 2022, "光伏", 10, "eligible", 0),
                (5, 2026, "光伏", None, "eligible", 0),
                (6, 2018, "风电", None, "eligible", 0),
                (7, 2026, "光伏", 10, "pending_review", 0),
                (8, 2026, "光伏", 10, "eligible", 1),
                (9, 2018, "输变电建设", None, "eligible", 0),
                (10, 2022, "新能源制造业", None, "eligible", 0),
                (11, 2026, "储能", None, "eligible", 0),
            ):
                pid = f"IDN-X-{number:04}"
                conn.execute("INSERT INTO canonical_projects VALUES (?,?,?,?,?,?)", (pid, "梅拉" + category + "项目", "印尼", category, "IDN", excluded))
                conn.execute("INSERT INTO project_events VALUES (?,?,?,?,?,?,?)", (pid + "-01", pid, "Unknown" if number == 9 else "开工", f"{year}-01-01", capacity, '["https://example.com/source"]', status))
                conn.execute("INSERT INTO chinese_entities VALUES (?,?,?,?,?)", (pid + "-01", "中国电建", "中国电建", "工程总包", "eligible"))
            # A withheld relation must not confer main-project eligibility.
            conn.execute("INSERT INTO canonical_projects VALUES ('IDN-X-0012','梅拉光伏项目','印尼','光伏','IDN',0)")
            conn.execute("INSERT INTO project_events VALUES ('IDN-X-0012-01','IDN-X-0012','开工','2026-01-01',NULL,'[]','eligible')")
            conn.execute("INSERT INTO chinese_entities VALUES ('IDN-X-0012-01','中国电建','中国电建','工程总包','pending_review')")
            # Participation is already established at project level; a second
            # activity need not repeat the participant relationship to be found.
            conn.execute("INSERT INTO project_events VALUES ('IDN-X-0005-02','IDN-X-0005','签约','2025-01-01',NULL,'[\"https://example.com/source\"]','eligible')")
        with closing(sqlite3.connect(raw)) as conn, conn:
            conn.execute("CREATE TABLE raw_news (url TEXT,title TEXT,content TEXT,publish_date TEXT)")
            # Deliberately no canonical project phrase. Public coverage must not
            # depend on the pilot's narrow lexical evidence matching.
            conn.execute("INSERT INTO raw_news VALUES (?,?,?,?)", ("https://example.com/source", "项目消息", "承建单位中国电建。", "2026-01-01"))
        return main, raw

    def test_real_projection_coverage_incremental_filter_and_source_invariance(self):
        with tempfile.TemporaryDirectory() as directory:
            main, raw = self._fixture(Path(directory))
            before = [file_hash(main), file_hash(raw)]
            with closing(sqlite3.connect(main)) as conn:
                counts = [conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                          for table in ("canonical_projects", "project_events", "chinese_entities")]
            rows = build_targets(main, raw)
            self.assertEqual({r["project_id"] for r in rows}, {f"IDN-X-{i:04}" for i in (2, 3, 4, 5, 6, 9, 10, 11)})
            self.assertEqual({r["project_id"] for r in rows if r["pool"] == "capacity_missing"}, {"IDN-X-0005", "IDN-X-0006"})
            self.assertIn("IDN-X-0005-02", {r["event_id"] for r in rows})
            self.assertEqual(summarize_targets(rows)["public_baseline_year"], 2022)
            self.assertTrue(all(r["context"]["source_articles"] for r in rows))
            self.assertTrue(all(r["card"]["stage_ambiguous"] for r in rows))
            self.assertTrue(all(r["card"]["time_start"] is None for r in rows))
            incremental = build_targets(main, raw, changed_event_ids={"IDN-X-0002-01", "IDN-X-0007-01"})
            self.assertEqual([r["event_id"] for r in incremental], ["IDN-X-0002-01"])
            self.assertEqual(build_targets(main, raw, changed_event_ids=[]), [])
            self.assertEqual(before, [file_hash(main), file_hash(raw)])
            with closing(sqlite3.connect(main)) as conn:
                self.assertEqual(counts, [conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                                         for table in ("canonical_projects", "project_events", "chinese_entities")])

    def test_explicit_event_date_is_available_but_publication_date_is_not_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            main, raw = self._fixture(Path(directory))
            with closing(sqlite3.connect(raw)) as conn, conn:
                conn.execute("UPDATE raw_news SET content=?", ("2026年1月1日，中国电建承建的梅拉光伏项目正式开工。",))
            row = build_targets(main, raw, changed_event_ids={"IDN-X-0005-01"})[0]
            self.assertEqual(row["card"]["time_start"], "2026-01-01")
            self.assertFalse(row["card"]["stage_ambiguous"])
            with closing(sqlite3.connect(raw)) as conn, conn:
                conn.execute("UPDATE raw_news SET content=?", ("2025年1月1日另一个项目开工。中国电建承建的梅拉光伏项目正式开工。",))
            row = build_targets(main, raw, changed_event_ids={"IDN-X-0005-01"})[0]
            self.assertIsNone(row["card"]["time_start"])
            self.assertTrue(row["card"]["stage_ambiguous"])
            with self.assertRaisesRegex(ValueError, "collection"):
                build_targets(main, raw, changed_event_ids="IDN-X-0005-01")


if __name__ == "__main__":
    unittest.main()
