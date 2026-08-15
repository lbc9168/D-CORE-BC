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


def test_iso3_to_en_name_loaded():
    """路径修复回归测试：ISO3→英文名映射必须成功加载且含已知国家（防 parents 层级回归）。"""
    assert len(flat_builder.ISO3_TO_EN_NAME) > 100          # WorldCountryList 有 200+ 国
    assert flat_builder.ISO3_TO_EN_NAME.get("CHL") == "Chile"
    assert flat_builder.ISO3_TO_EN_NAME.get("CHN") == "China"


# ── 指标扩展 2026-W30：储能修复 + 子类 + 金额 ──────────────────────────────

def _make_src_ext(path):
    """扩展 schema 测试库：含 subcategory/pv_capacity_mw/金额列。"""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE canonical_projects (
            id TEXT PRIMARY KEY, project_name TEXT, project_name_en TEXT,
            name_en_quality TEXT, country TEXT, country_iso TEXT, bri_country TEXT,
            ldc TEXT, location TEXT, category TEXT, excluded INTEGER DEFAULT 0,
            excluded_reason TEXT, subcategory TEXT
        );
        CREATE TABLE project_events (
            id TEXT PRIMARY KEY, canonical_id TEXT, project_stage TEXT,
            capacity_mw REAL, capacity_mw_raw TEXT, pv_capacity_mw REAL,
            storage_power_mw REAL, storage_energy_mwh REAL, capacity_source TEXT,
            total_investment_usd REAL, total_investment_usd_raw TEXT,
            contract_amount_usd REAL, financing_amount_usd REAL,
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
    conn.commit()
    return conn


def test_pure_es_project_keeps_storage_clears_capacity(tmp_path):
    """纯储能项目：capacity_mw 清空（不进装机聚合）但 storage_* 保留（选值+清空双修复）。"""
    src, dst = tmp_path / "src.db", tmp_path / "dst.db"
    conn = _make_src_ext(str(src))
    conn.execute(
        "INSERT INTO canonical_projects (id, project_name, category) VALUES ('SAU-ES-0002','红海储能','储能')"
    )
    # 所有事件 capacity_mw 均为 NULL——修复前储能选值永不命中
    conn.execute(
        "INSERT INTO project_events (id, canonical_id, project_stage, storage_energy_mwh, publish_date) "
        "VALUES ('SAU-ES-0002-01','SAU-ES-0002',?,12500.0,'2023-05')",
        (_TERMINAL,),
    )
    conn.commit()
    conn.close()

    flat_builder.build_flat(src, dst, province_map={})
    conn = sqlite3.connect(dst)
    row = conn.execute(
        "SELECT capacity_mw, storage_power_mw, storage_energy_mwh FROM projects_flat"
    ).fetchone()
    conn.close()
    assert row[0] is None, "储能类 capacity_mw 应清空"
    assert row[2] == 12500.0, "储能能量必须保留到导出层"


def test_txmf_still_fully_cleared(tmp_path):
    """TX（无 storage_metrics 标志）仍全清 capacity 与 storage 字段。"""
    src, dst = tmp_path / "src.db", tmp_path / "dst.db"
    conn = _make_src_ext(str(src))
    conn.execute(
        "INSERT INTO canonical_projects (id, project_name, category) VALUES ('SAU-TX-0001','输电','输变电建设')"
    )
    conn.execute(
        "INSERT INTO project_events (id, canonical_id, project_stage, capacity_mw, storage_energy_mwh, publish_date) "
        "VALUES ('SAU-TX-0001-01','SAU-TX-0001',?,7000.0,50.0,'2023-05')",
        (_TERMINAL,),
    )
    conn.commit()
    conn.close()

    flat_builder.build_flat(src, dst, province_map={})
    conn = sqlite3.connect(dst)
    row = conn.execute(
        "SELECT capacity_mw, storage_power_mw, storage_energy_mwh, capacity_source FROM projects_flat"
    ).fetchone()
    conn.close()
    assert row == (None, None, None, None)


def test_subcategory_default_and_explicit(tmp_path):
    """子类：DB NULL 按父类默认渲染；显式值原样导出（经 enums 查表断言，不写标签字面量）。"""
    src, dst = tmp_path / "src.db", tmp_path / "dst.db"
    conn = _make_src_ext(str(src))
    conn.execute(
        "INSERT INTO canonical_projects (id, project_name, category, subcategory) "
        "VALUES ('VNM-PV-0001','光伏A','光伏',NULL)"
    )
    conn.execute(
        "INSERT INTO canonical_projects (id, project_name, category, subcategory) "
        "VALUES ('VNM-PV-0002','光伏B','光伏','分布式')"
    )
    conn.execute(
        "INSERT INTO canonical_projects (id, project_name, category) VALUES ('VNM-WD-0001','风电C','风电')"
    )
    for pid in ("VNM-PV-0001", "VNM-PV-0002", "VNM-WD-0001"):
        conn.execute(
            "INSERT INTO project_events (id, canonical_id, project_stage, capacity_mw, publish_date) "
            f"VALUES ('{pid}-01','{pid}',?,100.0,'2023-01')",
            (_TERMINAL,),
        )
    conn.commit()
    conn.close()

    flat_builder.build_flat(src, dst, province_map={})
    conn = sqlite3.connect(dst)
    got = dict(conn.execute("SELECT project_id, subcategory FROM projects_flat").fetchall())
    en = dict(conn.execute("SELECT project_id, subcategory_en FROM projects_flat").fetchall())
    conn.close()
    _default = bri_enums.subcategory_default("光伏")
    assert got["VNM-PV-0001"] == _default, "NULL 应渲染父类默认"
    assert en["VNM-PV-0001"] == bri_enums.subcategory_en("光伏", _default)
    assert got["VNM-PV-0002"] == "分布式"
    assert got["VNM-WD-0001"] == "", "无子类定义的类别两列为空"


def test_amounts_and_pv_capacity_exported(tmp_path):
    """金额两新列走代表事件选值；pv_capacity_mw 终态优先取非空事件。"""
    src, dst = tmp_path / "src.db", tmp_path / "dst.db"
    conn = _make_src_ext(str(src))
    conn.execute(
        "INSERT INTO canonical_projects (id, project_name, category) VALUES ('UZB-HB-0001','光储','综合能源')"
    )
    conn.execute(
        "INSERT INTO project_events (id, canonical_id, project_stage, capacity_mw, pv_capacity_mw, "
        "contract_amount_usd, financing_amount_usd, publish_date) "
        "VALUES ('UZB-HB-0001-01','UZB-HB-0001',?,150.0,100.0,167.0,486.0,'2023-05')",
        (_TERMINAL,),
    )
    conn.commit()
    conn.close()

    flat_builder.build_flat(src, dst, province_map={})
    conn = sqlite3.connect(dst)
    row = conn.execute(
        "SELECT pv_capacity_mw, contract_amount_usd, financing_amount_usd, subcategory FROM projects_flat"
    ).fetchone()
    conn.close()
    assert row[0] == 100.0
    assert row[1] == 167.0
    assert row[2] == 486.0
    assert row[3] == "", "综合能源旧组合子类不应再进入导出层"


def test_energy_taxonomy_fields_and_confirmed_storage_policy(tmp_path):
    """projects_flat 输出能源事实/双语标签，且非 confirmed 状态不携带储能数值。"""
    src, dst = tmp_path / "src.db", tmp_path / "dst.db"
    conn = _make_src_ext(str(src))
    conn.execute("ALTER TABLE canonical_projects ADD COLUMN technology_tags_json TEXT NOT NULL DEFAULT '[]'")
    conn.execute("ALTER TABLE project_events ADD COLUMN generation_sources_json TEXT")
    conn.execute("ALTER TABLE project_events ADD COLUMN storage_status TEXT")
    conn.execute(
        "INSERT INTO canonical_projects (id, project_name, category, technology_tags_json) "
            "VALUES ('VNM-PV-0003','光储项目','光伏','[\"光储\"]')"
    )
    conn.execute(
        "INSERT INTO project_events (id, canonical_id, project_stage, capacity_mw, "
        "storage_power_mw, storage_energy_mwh, generation_sources_json, storage_status, publish_date) "
        "VALUES ('VNM-PV-0003-01','VNM-PV-0003',?,100.0,20.0,80.0,'[\"光伏\"]','confirmed','2024-01')",
        (_TERMINAL,),
    )
    conn.execute(
        "INSERT INTO canonical_projects (id, project_name, category) "
        "VALUES ('VNM-WD-0004','拟配储风电','风电')"
    )
    conn.execute(
        "INSERT INTO project_events (id, canonical_id, project_stage, capacity_mw, "
        "storage_power_mw, storage_energy_mwh, generation_sources_json, storage_status, publish_date) "
        "VALUES ('VNM-WD-0004-01','VNM-WD-0004',?,50.0,10.0,40.0,'[\"风电\"]','planned','2024-01')",
        (_TERMINAL,),
    )
    conn.commit()
    conn.close()

    flat_builder.build_flat(src, dst, province_map={})
    conn = sqlite3.connect(dst)
    confirmed = conn.execute(
        "SELECT generation_sources, storage_status, technology_tags, technology_tags_en, "
        "storage_power_mw, storage_energy_mwh FROM projects_flat WHERE project_id='VNM-PV-0003'"
    ).fetchone()
    planned = conn.execute(
        "SELECT storage_status, storage_power_mw, storage_energy_mwh FROM projects_flat "
        "WHERE project_id='VNM-WD-0004'"
    ).fetchone()
    conn.close()

    assert confirmed == (
            '["光伏"]', "confirmed", '["光储"]',
            '["Solar + Storage"]', 20.0, 80.0,
    )
    assert planned == ("planned", None, None)
