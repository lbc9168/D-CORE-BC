"""S4.1：events_export.csv 一行一个 project_event，含 event_id + 4 大类映射列。"""
import csv
import sqlite3

from s03_data_exporter import events_exporter


def _mk(src):
    c = sqlite3.connect(src)
    c.executescript(
        """
        CREATE TABLE canonical_projects(
            id TEXT PRIMARY KEY, project_name TEXT, project_name_en TEXT,
            country TEXT, country_iso TEXT, category TEXT, excluded INTEGER DEFAULT 0
        );
        CREATE TABLE project_events(
            id TEXT PRIMARY KEY, canonical_id TEXT, project_stage TEXT,
            capacity_mw REAL, total_investment_usd REAL, publish_date TEXT,
            investment_date TEXT, financing_model TEXT, verification_status TEXT
        );
        CREATE TABLE chinese_entities(
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT,
            implementing_entity TEXT, is_central_soe INTEGER
        );
        """
    )
    c.execute(
        "INSERT INTO canonical_projects(id, project_name_en, country_iso, category, excluded) "
        "VALUES('IND-PV-0001','Solar A','IND','光伏',0)"
    )
    c.executemany(
        "INSERT INTO project_events(id, canonical_id, project_stage, capacity_mw, publish_date) "
        "VALUES(?,?,?,?,?)",
        [
            ("IND-PV-0001-01", "IND-PV-0001", "签约", 50.0, "2023-01-15"),
            ("IND-PV-0001-02", "IND-PV-0001", "竣工", 50.0, "2025-10-01"),
        ],
    )
    c.execute(
        "INSERT INTO chinese_entities(event_id, implementing_entity, is_central_soe) "
        "VALUES('IND-PV-0001-01','中国电建',1)"
    )
    # excluded 项目的事件不应进 CSV
    c.execute(
        "INSERT INTO canonical_projects(id, project_name_en, country_iso, category, excluded) "
        "VALUES('X','X','IND','光伏',1)"
    )
    c.execute(
        "INSERT INTO project_events(id, canonical_id, project_stage, publish_date) "
        "VALUES('X-01','X','签约','2024-01')"
    )
    c.commit()
    c.close()


def test_events_csv_one_row_per_event(tmp_path):
    src = tmp_path / "s.db"
    out = tmp_path / "events_export.csv"
    _mk(str(src))

    n = events_exporter.export_events_csv(src, [out], iso3_to_en={"IND": "India"})
    assert n == 2  # excluded 项目的事件被过滤

    with open(out, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2

    r0 = next(r for r in rows if r["event_id"] == "IND-PV-0001-01")
    assert r0["canonical_id"] == "IND-PV-0001"
    assert r0["stage_cn"] == "签约" and r0["stage_en"] == "Signed"
    assert r0["stage_group_cn"] == "前期" and r0["stage_group_en"] == "Pre-construction"
    assert r0["year"] == "2023" and r0["month"] == "2023-01"
    assert r0["country_en"] == "India"
    assert r0["entities"] == "中国电建" and r0["is_central_soe"] == "1"

    r1 = next(r for r in rows if r["event_id"] == "IND-PV-0001-02")
    assert r1["stage_cn"] == "竣工" and r1["stage_en"] == "Completed"
    assert r1["stage_group_cn"] == "完工" and r1["stage_group_en"] == "Completed"
    assert r1["year"] == "2025" and r1["month"] == "2025-10"
    # 该事件无中方实体 → entities 空、is_central_soe 为 0
    assert r1["entities"] == "" and r1["is_central_soe"] == "0"
