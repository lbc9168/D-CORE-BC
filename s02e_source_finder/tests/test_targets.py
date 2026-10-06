"""Offline s03 integration and frozen pilot isolation checks."""

import sqlite3
import tempfile
import unittest
from collections import Counter
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from s02e_source_finder.sample import MODULE, file_hash
from s02e_source_finder.targets import SEEN_EVENT, SPLITS, TargetConfig, _assign_leakage_groups, build_live_targets, freeze_pilot, select_events


class LiveBaselineTests(unittest.TestCase):
    def test_live_projection_uses_s03_dual_baseline_and_deduplicates_entities(self):
        """TX planning survives; missing-capacity generation belongs to the separate pool."""
        with tempfile.TemporaryDirectory(dir=MODULE / "data") as directory:
            folder = Path(directory)
            main = folder / "main.db"
            with closing(sqlite3.connect(main)) as conn, conn:
                conn.executescript("""
                    CREATE TABLE canonical_projects (id TEXT, project_name TEXT, category TEXT, country_iso TEXT, excluded INTEGER);
                    CREATE TABLE project_events (id TEXT, canonical_id TEXT, project_stage TEXT, publish_date TEXT, capacity_mw REAL, source_links TEXT, official_website_links TEXT, non_official_website_links TEXT);
                    CREATE TABLE chinese_entities (event_id TEXT, implementing_entity TEXT);
                """)
                conn.executemany("INSERT INTO canonical_projects VALUES (?,?,?,?,?)", [
                    ("A-PV", "光伏", "光伏", "IDN", 0), ("B-PV", "排除", "光伏", "IDN", 1),
                    ("C-PV", "中国", "光伏", "CHN", 0), ("D-PV", "无容量", "光伏", "IDN", 0),
                    ("E-TX", "输电", "输变电建设", "IDN", 0), ("F-MF", "工厂", "新能源制造业", "IDN", 0),
                    ("G-TX", "中国输电", "输变电建设", "CHN", 0), ("H-PV", "旧项目", "光伏", "IDN", 0),
                ])
                for project, stage, date, cap in [
                    ("A-PV", "开工", "2026-01-01", 100), ("B-PV", "开工", "2026-01-01", 100),
                    ("C-PV", "开工", "2026-01-01", 100), ("D-PV", "开工", "2026-01-01", None),
                    ("E-TX", "拟开发", "2026-01-01", None), ("F-MF", "拟开发", "2026-01-01", None),
                    ("G-TX", "拟开发", "2026-01-01", None), ("H-PV", "开工", "2021-01-01", 100),
                ]:
                    conn.execute("INSERT INTO project_events VALUES (?,?,?,?,?,?,?,?)", (project + "-01", project, stage, date, cap, "[]", "[]", "[]"))
                    conn.execute("INSERT INTO chinese_entities VALUES (?,?)", (project + "-01", "中国能建"))
                conn.execute("INSERT INTO chinese_entities VALUES (?,?)", ("A-PV-01", "中国电建"))
                conn.execute("INSERT INTO project_events VALUES (?,?,?,?,?,?,?,?)", ("A-PV-00", "A-PV", "签约", "2021-01-01", 100, "[]", "[]", "[]"))
            before = file_hash(main)
            rows, metadata = build_live_targets(main, folder)
            self.assertEqual({t["event_id"] for t in rows}, {"A-PV-01", "E-TX-01", "F-MF-01"})
            self.assertEqual({t["domain"] for t in rows}, {"main", "TX", "MF"})
            self.assertEqual(metadata["projection_counts"]["event_entity_rows"], 4)
            self.assertEqual(file_hash(main), before)


class SelectionTests(unittest.TestCase):
    @staticmethod
    def targets():
        targets = []
        for domain in ("main", "TX", "MF"):
            for index in range(30):
                event_id = SEEN_EVENT if domain == "main" and index == 0 else f"{domain}-{index:02}-01"
                project_id = event_id.rsplit("-", 1)[0]
                targets.append({"project_id": project_id, "event_id": event_id, "domain": domain,
                                "category_code": domain, "country_iso": "IDN", "stage": str(index % 3), "year": 2026,
                                "source_urls": [f"https://example.org/{domain}/{index // 2}"],
                                "candidates": [], "features": {"no_legacy_candidates": True}})
        _assign_leakage_groups(targets)
        return targets

    def test_fixed_quotas_seen_event_and_connected_source_isolation(self):
        targets = self.targets()
        rows = select_events(targets)
        self.assertEqual(Counter((t["split"], t["domain"]) for t in rows),
                         Counter({(split, domain): n for split, quotas in SPLITS.items() for domain, n in quotas.items()}))
        self.assertEqual(next(t["split"] for t in rows if t["event_id"] == SEEN_EVENT), "development")
        groups = {split: {t["leakage_group"] for t in rows if t["split"] == split} for split in SPLITS}
        self.assertFalse(groups["development"] & groups["holdout"])
        self.assertTrue(all(t["manual_label"] is None for t in rows))
        self.assertEqual([t["event_id"] for t in rows], [t["event_id"] for t in select_events(list(reversed(targets)))])

    def test_shared_candidate_transitive_groups_are_not_split(self):
        targets = self.targets()[:3]
        targets[1]["candidates"] = [{"url": "https://example.org/common"}]
        targets[2]["candidates"] = [{"url": "https://example.org/common"}]
        _assign_leakage_groups(targets)
        self.assertEqual(len({t["leakage_group"] for t in targets}), 1)

    def test_output_isolation_and_no_overwrite_precede_source_access(self):
        with patch("s02e_source_finder.targets.source_hashes") as hashes:
            with self.assertRaisesRegex(ValueError, "under_module_data"):
                freeze_pilot(TargetConfig(output_dir=MODULE.parent / "output"))
            with tempfile.TemporaryDirectory(dir=MODULE / "data") as directory:
                target = Path(directory) / "targets.json"
                target.write_text("preserved", encoding="utf-8")
                with self.assertRaises(FileExistsError):
                    freeze_pilot(TargetConfig(output_dir=Path(directory)))
                self.assertEqual(target.read_text(), "preserved")
            hashes.assert_not_called()

    def test_source_change_fails_before_publishing_artifacts(self):
        with tempfile.TemporaryDirectory(dir=MODULE / "data") as directory:
            output = Path(directory)
            with patch("s02e_source_finder.targets.source_hashes", side_effect=[{"main": "old"}, {"main": "new"}]), \
                 patch("s02e_source_finder.targets.build_live_targets", return_value=([], {})), \
                 patch("s02e_source_finder.targets.select_events", return_value=[]):
                with self.assertRaisesRegex(RuntimeError, "source_changed"):
                    freeze_pilot(TargetConfig(output_dir=output))
            self.assertFalse((output / "targets.json").exists())
            self.assertFalse((output / "selected_events.json").exists())


if __name__ == "__main__":
    unittest.main()
