"""任务 3.4：flat_builder 新增 tx_flat（输变电建设）/ mf_flat（新能源制造业）专用扁平表。"""
import json
import sqlite3

from s03_data_exporter import flat_builder
import bri_enums

_TX_ZH = "输变电建设"
_MF_ZH = "新能源制造业"

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
            project_developer TEXT, project_developer_en TEXT,
            voltage_kv REAL, line_length_km REAL, transmission_capacity_mw REAL,
            production_capacity_value REAL, production_capacity_unit TEXT
        );
        CREATE TABLE chinese_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT,
            implementing_entity TEXT, implementing_entity_en TEXT,
            subsidiary_name TEXT, subsidiary_name_en TEXT,
            involvement_type TEXT, subcontract_amount REAL, is_central_soe INTEGER
        );
        """
    )

    # TX 项目 T1（保留）：早期事件 voltage/line_length 有值，终态事件三项专用列均有值
    conn.execute(
        "INSERT INTO canonical_projects (id, project_name, project_name_en, country, "
        "country_iso, category, excluded) VALUES ('T1','输电甲','TX Proj A','印尼','IDN',?,0)",
        (_TX_ZH,),
    )
    conn.execute(
        "INSERT INTO project_events (id, canonical_id, project_stage, voltage_kv, "
        "line_length_km, publish_date) VALUES ('T1-01','T1',?,220.0,50.0,'2020-01')",
        (_EARLY,),
    )
    conn.execute(
        "INSERT INTO project_events (id, canonical_id, project_stage, voltage_kv, "
        "line_length_km, transmission_capacity_mw, publish_date, project_developer) "
        "VALUES ('T1-02','T1',?,500.0,120.0,800.0,'2022-06','印尼国网')",
        (_TERMINAL,),
    )
    conn.execute(
        "INSERT INTO chinese_entities (event_id, implementing_entity, implementing_entity_en, "
        "involvement_type, is_central_soe) VALUES ('T1-02','中国电建','PowerChina','EPC总承包',1)"
    )

    # TX 项目 T2（终态事件专用列全空，应从早期事件回填 voltage_kv/line_length_km，
    # 但 transmission_capacity_mw 全部事件均空 → NULL）
    conn.execute(
        "INSERT INTO canonical_projects (id, project_name, country, country_iso, category, excluded) "
        "VALUES ('T2','输电乙','老挝','LAO',?,0)",
        (_TX_ZH,),
    )
    conn.execute(
        "INSERT INTO project_events (id, canonical_id, project_stage, voltage_kv, "
        "line_length_km, publish_date) VALUES ('T2-01','T2',?,110.0,30.0,'2021-01')",
        (_EARLY,),
    )
    conn.execute(
        "INSERT INTO project_events (id, canonical_id, project_stage, publish_date) "
        "VALUES ('T2-02','T2',?,'2023-01')",
        (_TERMINAL,),
    )

    # TX 项目 T3（excluded=1，不应入 tx_flat）
    conn.execute(
        "INSERT INTO canonical_projects (id, project_name, category, excluded, excluded_reason) "
        "VALUES ('T3','输电丙',?,1,'non_renewable')",
        (_TX_ZH,),
    )
    conn.execute(
        "INSERT INTO project_events (id, canonical_id, project_stage, voltage_kv, publish_date) "
        "VALUES ('T3-01','T3',?,220.0,'2021-01')",
        (_TERMINAL,),
    )

    # MF 项目 M1（保留）：早期事件 value=100/unit='MWh产能'，终态事件 value=500/unit='GWh产能'
    # → 必须整体取终态事件的 (500, 'GWh产能')，不得 value 取终态/unit 取早期这种跨事件配对
    conn.execute(
        "INSERT INTO canonical_projects (id, project_name, country, country_iso, category, excluded) "
        "VALUES ('M1','制造甲','越南','VNM',?,0)",
        (_MF_ZH,),
    )
    conn.execute(
        "INSERT INTO project_events (id, canonical_id, project_stage, production_capacity_value, "
        "production_capacity_unit, publish_date) VALUES ('M1-01','M1',?,100.0,'MWh产能','2020-01')",
        (_EARLY,),
    )
    conn.execute(
        "INSERT INTO project_events (id, canonical_id, project_stage, production_capacity_value, "
        "production_capacity_unit, publish_date) VALUES ('M1-02','M1',?,500.0,'GWh产能','2022-06')",
        (_TERMINAL,),
    )

    # MF 项目 M2（终态事件 value/unit 均空，应回填到早期事件的 (value, unit) 配对，
    # 不得 value 取自 M2-01 而 unit 取自另一个事件）
    conn.execute(
        "INSERT INTO canonical_projects (id, project_name, category, excluded) "
        "VALUES ('M2','制造乙',?,0)",
        (_MF_ZH,),
    )
    conn.execute(
        "INSERT INTO project_events (id, canonical_id, project_stage, production_capacity_value, "
        "production_capacity_unit, publish_date) VALUES ('M2-01','M2',?,200.0,'件/年','2019-01')",
        (_EARLY,),
    )
    conn.execute(
        "INSERT INTO project_events (id, canonical_id, project_stage, publish_date) "
        "VALUES ('M2-02','M2',?,'2023-01')",
        (_TERMINAL,),
    )

    # MF 项目 M3（全部事件 value/unit 均空 → 专用列 NULL，但仍应有行）
    conn.execute(
        "INSERT INTO canonical_projects (id, project_name, category, excluded) "
        "VALUES ('M3','制造丙',?,0)",
        (_MF_ZH,),
    )
    conn.execute(
        "INSERT INTO project_events (id, canonical_id, project_stage, publish_date) "
        "VALUES ('M3-01','M3',?,'2021-01')",
        (_TERMINAL,),
    )

    # 普通光伏项目 P1（不应进 tx_flat/mf_flat）
    conn.execute(
        "INSERT INTO canonical_projects (id, project_name, category, excluded) "
        "VALUES ('P1','光伏甲','光伏',0)"
    )
    conn.execute(
        "INSERT INTO project_events (id, canonical_id, project_stage, capacity_mw, publish_date) "
        "VALUES ('P1-01','P1',?,50.0,'2021-01')",
        (_TERMINAL,),
    )

    conn.commit()
    conn.close()


def _rows(dst, table):
    conn = sqlite3.connect(dst)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
    conn.close()
    return {r["project_id"]: r for r in rows}


def test_tx_flat_row_count_and_exclusion(tmp_path):
    """tx_flat 只含未排除的 TX 项目；MF/普通项目不进 tx_flat。"""
    src, dst = tmp_path / "src.db", tmp_path / "dst.db"
    _make_src(str(src))
    n = flat_builder.build_flat(src, dst, province_map={})
    n_tx = flat_builder.build_tx_flat(src, dst, province_map={})
    n_mf = flat_builder.build_mf_flat(src, dst, province_map={})

    assert n_tx == 2  # T1, T2（T3 excluded 排除）
    tx = _rows(dst, "tx_flat")
    assert set(tx.keys()) == {"T1", "T2"}
    assert n_mf == 3  # M1, M2, M3
    mf = _rows(dst, "mf_flat")
    assert set(mf.keys()) == {"M1", "M2", "M3"}


def test_tx_flat_no_capacity_mw_column(tmp_path):
    """tx_flat/mf_flat 不含 capacity_mw、storage_* 列。"""
    src, dst = tmp_path / "src.db", tmp_path / "dst.db"
    _make_src(str(src))
    flat_builder.build_tx_flat(src, dst, province_map={})
    flat_builder.build_mf_flat(src, dst, province_map={})
    conn = sqlite3.connect(dst)
    tx_cols = {r[1] for r in conn.execute("PRAGMA table_info(tx_flat)")}
    mf_cols = {r[1] for r in conn.execute("PRAGMA table_info(mf_flat)")}
    conn.close()
    for banned in ("capacity_mw", "storage_power_mw", "storage_energy_mwh"):
        assert banned not in tx_cols
        assert banned not in mf_cols


def test_tx_flat_specific_columns_priority(tmp_path):
    """tx_flat 专用列：终态事件优先，某列终态为空则回填最早的非空值。"""
    src, dst = tmp_path / "src.db", tmp_path / "dst.db"
    _make_src(str(src))
    flat_builder.build_tx_flat(src, dst, province_map={})
    tx = _rows(dst, "tx_flat")

    # T1：终态事件全部专用列均非空 → 直接取终态
    assert tx["T1"]["voltage_kv"] == 500.0
    assert tx["T1"]["line_length_km"] == 120.0
    assert tx["T1"]["transmission_capacity_mw"] == 800.0
    assert tx["T1"]["project_name"] == "输电甲"
    assert tx["T1"]["project_name_en"] == "TX Proj A"
    assert tx["T1"]["country_iso"] == "IDN"

    # T2：终态事件 voltage_kv/line_length_km 为空 → 回填自早期事件；
    # transmission_capacity_mw 全部事件均空 → NULL
    assert tx["T2"]["voltage_kv"] == 110.0
    assert tx["T2"]["line_length_km"] == 30.0
    assert tx["T2"]["transmission_capacity_mw"] is None


def test_tx_flat_entities_and_stage(tmp_path):
    """tx_flat 实体字段与 projects_flat 同形式（JSON 数组含 name/involvement_type 等）。"""
    src, dst = tmp_path / "src.db", tmp_path / "dst.db"
    _make_src(str(src))
    flat_builder.build_tx_flat(src, dst, province_map={})
    tx = _rows(dst, "tx_flat")
    entities = json.loads(tx["T1"]["all_entities"])
    assert len(entities) == 1
    assert entities[0]["name"] == "中国电建"
    assert entities[0]["name_en"] == "PowerChina"
    assert entities[0]["is_central_soe"] == 1
    assert tx["T1"]["latest_stage"] == _TERMINAL


def test_mf_value_unit_same_event_pairing(tmp_path):
    """MF 的 production_capacity_value/unit 必须取自同一事件，禁止跨事件配对。"""
    src, dst = tmp_path / "src.db", tmp_path / "dst.db"
    _make_src(str(src))
    flat_builder.build_mf_flat(src, dst, province_map={})
    mf = _rows(dst, "mf_flat")

    # M1：终态事件 value/unit 均非空 → 直接取终态 (500, GWh产能)，不得混入早期事件的 100/MWh产能
    assert mf["M1"]["production_capacity_value"] == 500.0
    assert mf["M1"]["production_capacity_unit"] == "GWh产能"

    # M2：终态事件 value/unit 均空 → 回填自早期事件，且两者必须同源 (200, 件/年)
    assert mf["M2"]["production_capacity_value"] == 200.0
    assert mf["M2"]["production_capacity_unit"] == "件/年"

    # M3：全部事件均空 → 两列均 NULL，但项目仍有行
    assert mf["M3"]["production_capacity_value"] is None
    assert mf["M3"]["production_capacity_unit"] is None


def test_mf_flat_country_and_stage_present(tmp_path):
    src, dst = tmp_path / "src.db", tmp_path / "dst.db"
    _make_src(str(src))
    flat_builder.build_mf_flat(src, dst, province_map={})
    mf = _rows(dst, "mf_flat")
    assert mf["M1"]["country_iso"] == "VNM"
    assert mf["M1"]["latest_stage"] == _TERMINAL
