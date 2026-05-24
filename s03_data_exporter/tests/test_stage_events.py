"""S4.1：projects_flat 的 stage_events（仅 4 大类可见事件，{group,year} 升序）+ 区间年。"""
import json
import sqlite3

from s03_data_exporter import flat_builder


def _mk(src):
    """创建最小测试库：1 个 4 事件项目（含 1 个隐藏阶段 拟开发）。"""
    c = sqlite3.connect(src)
    c.executescript(
        """
        CREATE TABLE canonical_projects(
            id TEXT PRIMARY KEY, project_name TEXT, project_name_en TEXT,
            name_en_quality TEXT, country TEXT, country_iso TEXT, bri_country TEXT,
            ldc TEXT, location TEXT, category TEXT, excluded INTEGER DEFAULT 0,
            excluded_reason TEXT
        );
        CREATE TABLE project_events(
            id TEXT PRIMARY KEY, canonical_id TEXT, project_stage TEXT,
            capacity_mw REAL, publish_date TEXT, investment_date TEXT,
            financing_model TEXT, verification_status TEXT
        );
        CREATE TABLE chinese_entities(
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT,
            implementing_entity TEXT, is_central_soe INTEGER
        );
        """
    )
    c.execute("INSERT INTO canonical_projects(id, project_name, category, excluded) VALUES('P','甲','',0)")
    c.executemany(
        "INSERT INTO project_events(id, canonical_id, project_stage, publish_date) VALUES(?,?,?,?)",
        [
            ("P-00", "P", "拟开发", "2022-06"),   # 隐藏 → 不进 stage_events
            ("P-02", "P", "签约", "2023-01"),
            ("P-03", "P", "开工", "2024-05"),
            ("P-04", "P", "竣工", "2025-10"),
        ],
    )
    c.commit()
    c.close()


def _mk_hidden_only(src):
    """创建测试库：1 个项目，所有事件均为隐藏/未映射阶段。"""
    c = sqlite3.connect(src)
    c.executescript(
        """
        CREATE TABLE canonical_projects(
            id TEXT PRIMARY KEY, project_name TEXT, project_name_en TEXT,
            name_en_quality TEXT, country TEXT, country_iso TEXT, bri_country TEXT,
            ldc TEXT, location TEXT, category TEXT, excluded INTEGER DEFAULT 0,
            excluded_reason TEXT
        );
        CREATE TABLE project_events(
            id TEXT PRIMARY KEY, canonical_id TEXT, project_stage TEXT,
            capacity_mw REAL, publish_date TEXT, investment_date TEXT,
            financing_model TEXT, verification_status TEXT
        );
        CREATE TABLE chinese_entities(
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT,
            implementing_entity TEXT, is_central_soe INTEGER
        );
        """
    )
    c.execute("INSERT INTO canonical_projects(id, project_name, category, excluded) VALUES('Q','乙','',0)")
    c.executemany(
        "INSERT INTO project_events(id, canonical_id, project_stage, publish_date) VALUES(?,?,?,?)",
        [("Q-00", "Q", "拟开发", "2022-06"), ("Q-01", "Q", "Unknown", "2023-03")],
    )
    c.commit()
    c.close()


def test_stage_events_all_hidden(tmp_path):
    """所有事件均为隐藏/未映射阶段 → stage_events 为空、区间年为 NULL。"""
    src = tmp_path / "s2.db"
    dst = tmp_path / "d2.db"
    _mk_hidden_only(str(src))
    flat_builder.build_flat(src, dst, province_map={})
    con = sqlite3.connect(dst)
    row = con.execute(
        "SELECT stage_events, first_event_year, last_event_year "
        "FROM projects_flat WHERE project_id='Q'"
    ).fetchone()
    con.close()
    assert json.loads(row[0]) == []
    assert row[1] is None
    assert row[2] is None


def test_stage_events_built(tmp_path):
    src = tmp_path / "s.db"
    dst = tmp_path / "d.db"
    _mk(str(src))
    flat_builder.build_flat(src, dst, province_map={})

    con = sqlite3.connect(dst)
    row = con.execute(
        "SELECT stage_events, first_event_year, last_event_year "
        "FROM projects_flat WHERE project_id='P'"
    ).fetchone()
    con.close()

    se = json.loads(row[0])
    assert se == [
        {"group": "pre_construction", "year": 2023},
        {"group": "under_construction", "year": 2024},
        {"group": "completed", "year": 2025},
    ]
    assert row[1] == 2023
    assert row[2] == 2025
