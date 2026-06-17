"""S2a: flat_builder 读 REAL 列 + 投资额代表值（非均值）+ 储能透传 + excluded 过滤。"""
import sqlite3

from s03_data_exporter import flat_builder
import bri_enums

_STAGES = sorted(
    (s for s in bri_enums.valid_stages() if bri_enums.stage_priority(s) >= 0),
    key=bri_enums.stage_priority,
)
_EARLY, _TERMINAL = _STAGES[0], _STAGES[-1]


def _make_src(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE canonical_projects (
            id TEXT PRIMARY KEY, project_name TEXT, project_name_en TEXT,
            name_en_quality TEXT, country TEXT, country_iso TEXT, bri_country TEXT,
            ldc TEXT, location TEXT, category TEXT, excluded INTEGER DEFAULT 0,
            excluded_reason TEXT
        );
        CREATE TABLE project_events (
            id TEXT PRIMARY KEY, canonical_id TEXT, project_stage TEXT,
            capacity_mw REAL, capacity_mw_raw TEXT,
            storage_power_mw REAL, storage_energy_mwh REAL, capacity_source TEXT,
            total_investment_usd REAL, total_investment_usd_raw TEXT,
            publish_date TEXT, investment_date TEXT,
            official_website_links TEXT, non_official_website_links TEXT,
            verification_status TEXT,
            project_developer TEXT, project_developer_en TEXT
        );
        CREATE TABLE chinese_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT,
            implementing_entity TEXT, implementing_entity_en TEXT,
            subsidiary_name TEXT, subsidiary_name_en TEXT,
            involvement_type TEXT, subcontract_amount REAL, is_central_soe INTEGER
        );
        """
    )
    # 项目 A（保留）：早期事件容量50/投资999，终态事件容量100/投资200 + 储能
    conn.execute(
        "INSERT INTO canonical_projects (id, project_name, category, excluded) "
        "VALUES ('A','甲','',0)"
    )
    conn.execute(
        "INSERT INTO project_events (id, canonical_id, project_stage, capacity_mw, "
        "total_investment_usd, publish_date) VALUES ('A-01','A',?,50.0,999.0,'2020-01')",
        (_EARLY,),
    )
    conn.execute(
        "INSERT INTO project_events (id, canonical_id, project_stage, capacity_mw, "
        "storage_power_mw, storage_energy_mwh, capacity_source, total_investment_usd, "
        "publish_date, project_developer, project_developer_en) "
        "VALUES ('A-02','A',?,100.0,20.0,80.0,'legacy',200.0,'2022-06','印尼PLN','PLN')",
        (_TERMINAL,),
    )
    # 项目 B（excluded=1，应被过滤）
    conn.execute(
        "INSERT INTO canonical_projects (id, project_name, category, excluded, excluded_reason) "
        "VALUES ('B','乙','',1,'non_renewable')"
    )
    conn.execute(
        "INSERT INTO project_events (id, canonical_id, project_stage, capacity_mw, publish_date) "
        "VALUES ('B-01','B',?,30.0,'2021-01')",
        (_TERMINAL,),
    )
    conn.commit()
    conn.close()


def test_flat_builder_numeric(tmp_path):
    src = tmp_path / "src.db"
    dst = tmp_path / "dst.db"
    _make_src(str(src))
    n = flat_builder.build_flat(src, dst, province_map={})
    assert n == 1  # B 被 excluded 过滤

    conn = sqlite3.connect(dst)
    row = conn.execute(
        "SELECT project_id, capacity_mw, total_investment_usd, "
        "storage_power_mw, storage_energy_mwh, capacity_source FROM projects_flat"
    ).fetchone()
    conn.close()
    assert row[0] == "A"
    assert row[1] == 100.0          # 终态事件容量
    assert row[2] == 200.0          # 代表值（非均值 599.5，非早期 999）
    assert row[3] == 20.0           # 储能功率透传
    assert row[4] == 80.0           # 储能能量透传
    assert row[5] == "legacy"


def test_flat_builder_carries_owner(tmp_path):
    """projects_flat 带上代表(终态)事件的业主中英文 project_developer/_en。"""
    src, dst = tmp_path / "src.db", tmp_path / "dst.db"
    _make_src(str(src))
    flat_builder.build_flat(src, dst, province_map={})
    conn = sqlite3.connect(dst)
    row = conn.execute(
        "SELECT project_developer, project_developer_en FROM projects_flat WHERE project_id='A'").fetchone()
    conn.close()
    assert row[0] == "印尼PLN"
    assert row[1] == "PLN"
