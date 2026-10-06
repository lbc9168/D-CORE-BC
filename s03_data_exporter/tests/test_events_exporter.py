"""S4.1：events_export.csv 一行一个 project_event，含 event_id + 4 大类映射列。"""
import csv
import sqlite3
import pytest

from s03_data_exporter import events_exporter

# ── 基线政策纯函数（对齐前端 shouldExcludeByPolicy）──────────────────────────────

_OK = {
    "project_id": "IND-PV-0001", "country_iso": "IND",
    "first_event_year": 2022, "last_event_year": 2024,
    "category": "光伏", "primary_entity": "中国电建", "capacity_mw": 50.0,
}


def test_baseline_accepts_normal_project():
    assert events_exporter.passes_baseline(dict(_OK)) is True


def test_baseline_excludes_china_iso():
    assert events_exporter.passes_baseline({**_OK, "country_iso": "CHN"}) is False
    assert events_exporter.passes_baseline({**_OK, "country_iso": "twn"}) is False


def test_baseline_excludes_pre_2022_last_event():
    assert events_exporter.passes_baseline({**_OK, "last_event_year": 2021}) is False


def test_baseline_excludes_null_event_years():
    assert events_exporter.passes_baseline({**_OK, "first_event_year": None}) is False
    assert events_exporter.passes_baseline({**_OK, "last_event_year": None}) is False


def test_baseline_excludes_hidden_category():
    # 核电 = hidden
    assert events_exporter.passes_baseline({**_OK, "category": "核电"}) is False


def test_baseline_excludes_unknown_primary_entity():
    assert events_exporter.passes_baseline({**_OK, "primary_entity": "未知"}) is False
    assert events_exporter.passes_baseline({**_OK, "primary_entity": ""}) is False


def test_baseline_excludes_missing_generation_capacity():
    assert events_exporter.passes_baseline({**_OK, "capacity_mw": None}) is False


def test_baseline_allows_no_capacity_category_without_capacity():
    # 储能 = no_capacity，缺容量也通过
    assert events_exporter.passes_baseline(
        {**_OK, "category": "储能", "capacity_mw": None}
    ) is True


# ── 实体级集成测试 ──────────────────────────────────────────────────────────────

def _mk(src, flat):
    """构造 s02a 三表（src）与 projects_flat（flat）测试库。"""
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
            investment_date TEXT, financing_model TEXT, verification_status TEXT,
            project_developer TEXT, project_developer_en TEXT
        );
        CREATE TABLE chinese_entities(
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT,
            implementing_entity TEXT, implementing_entity_en TEXT,
            subsidiary_name TEXT, subsidiary_name_en TEXT,
            involvement_type TEXT, is_central_soe INTEGER
        );
        """
    )
    # 项目 A：合格；E01 两家公司、E02 无公司、E03 为 2021（应被 year 过滤）
    c.execute("INSERT INTO canonical_projects(id, project_name, project_name_en, country_iso, category, excluded) "
              "VALUES('IND-PV-0001','光伏A','Solar A','IND','光伏',0)")
    c.executemany(
        "INSERT INTO project_events(id, canonical_id, project_stage, capacity_mw, publish_date) VALUES(?,?,?,?,?)",
        [
            ("IND-PV-0001-01", "IND-PV-0001", "签约", 50.0, "2023-01-15"),
            ("IND-PV-0001-02", "IND-PV-0001", "竣工", 50.0, "2025-10-01"),
            ("IND-PV-0001-03", "IND-PV-0001", "签约", 50.0, "2021-05-01"),
        ],
    )
    # 业主中英文（per-event）：E01 有，E02 无
    c.execute("UPDATE project_events SET project_developer='印尼PLN', project_developer_en='PLN' "
              "WHERE id='IND-PV-0001-01'")
    c.executemany(
        "INSERT INTO chinese_entities(event_id, implementing_entity, implementing_entity_en, "
        "subsidiary_name, subsidiary_name_en, involvement_type, is_central_soe) VALUES(?,?,?,?,?,?,?)",
        [
            ("IND-PV-0001-01", "中国电建", "PowerChina", "山东电建", "Shandong Electric", "EPC总承包", 1),
            ("IND-PV-0001-01", "中国能建", "Energy China", "", "", "投资", 1),
        ],
    )
    # 项目 B：中国境内（基线应排除，但 flat_db=None 时保留）
    c.execute("INSERT INTO canonical_projects(id, project_name_en, country_iso, category, excluded) "
              "VALUES('CHN-PV-0002','Solar B','CHN','光伏',0)")
    c.execute("INSERT INTO project_events(id, canonical_id, project_stage, capacity_mw, publish_date) "
              "VALUES('CHN-PV-0002-01','CHN-PV-0002','签约',10.0,'2023-03-01')")
    c.execute("INSERT INTO chinese_entities(event_id, implementing_entity, is_central_soe) "
              "VALUES('CHN-PV-0002-01','某公司',0)")
    # 项目 C：excluded=1，永远排除
    c.execute("INSERT INTO canonical_projects(id, project_name_en, country_iso, category, excluded) "
              "VALUES('X','X','IND','光伏',1)")
    c.execute("INSERT INTO project_events(id, canonical_id, project_stage, publish_date) "
              "VALUES('X-01','X','签约','2024-01')")
    c.commit()
    c.close()

    # projects_flat（仅含基线判定所需列）：A 合格、B 因 CHN 不合格
    f = sqlite3.connect(flat)
    f.execute(
        "CREATE TABLE projects_flat(project_id TEXT PRIMARY KEY, country_iso TEXT, "
        "first_event_year INTEGER, last_event_year INTEGER, category TEXT, "
        "primary_entity TEXT, capacity_mw REAL)"
    )
    f.executemany(
        "INSERT INTO projects_flat VALUES(?,?,?,?,?,?,?)",
        [
            ("IND-PV-0001", "IND", 2023, 2025, "光伏", "中国电建", 50.0),
            ("CHN-PV-0002", "CHN", 2023, 2023, "光伏", "某公司", 10.0),
        ],
    )
    f.commit()
    f.close()


def test_entity_level_rows_with_baseline(tmp_path):
    src, flat, out = tmp_path / "s.db", tmp_path / "f.db", tmp_path / "events_export.csv"
    _mk(str(src), str(flat))

    n = events_exporter.export_events_csv(
        src, [out], flat_db=flat, iso3_to_en={"IND": "India"})

    with open(out, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
        header = rows[0].keys() if rows else []

    # A: E01 两家公司(2 行) + E02 无公司(1 行) = 3；E03(2021) 被过滤；B(CHN) 基线排除；C excluded
    assert n == 3 and len(rows) == 3

    e01 = [r for r in rows if r["event_id"] == "IND-PV-0001-01"]
    assert len(e01) == 2
    pc = next(r for r in e01 if r["entity_cn"] == "中国电建")
    assert pc["entity_en"] == "PowerChina"
    assert pc["subsidiary_cn"] == "山东电建" and pc["subsidiary_en"] == "Shandong Electric"
    # 真实枚举键 → 走查表
    assert pc["involvement_type_cn"] == "EPC总承包"
    assert pc["involvement_type_en"] == "EPC General Contractor"
    assert pc["is_central_soe"] == "1"
    # 非枚举键 → involvement_type_en 兜底回原文
    ec = next(r for r in e01 if r["entity_cn"] == "中国能建")
    assert ec["involvement_type_cn"] == "投资" and ec["involvement_type_en"] == "投资"
    assert pc["stage_cn"] == "签约" and pc["stage_en"] == "Signed"
    assert pc["stage_group_cn"] == "前期" and pc["stage_group_en"] == "Pre-construction"
    assert pc["year"] == "2023" and pc["month"] == "2023-01" and pc["country_en"] == "India"

    # E02 无公司：实体列全空
    e02 = next(r for r in rows if r["event_id"] == "IND-PV-0001-02")
    assert e02["entity_cn"] == "" and e02["entity_en"] == "" and e02["is_central_soe"] == ""
    assert e02["stage_group_cn"] == "完工"

    # 删除的旧列不存在，新列存在
    assert "entities" not in header
    assert "entity_cn" in header and "involvement_type_en" in header
    # 开发商中英文：在 entity 列之前；verification_status 已删
    hdr = list(header)
    assert "verification_status" not in hdr
    assert "developer_cn" in hdr and "developer_en" in hdr
    assert hdr.index("developer_cn") < hdr.index("entity_cn")
    assert hdr.index("developer_en") < hdr.index("entity_cn")
    assert pc["developer_cn"] == "印尼PLN" and pc["developer_en"] == "PLN"
    assert e02["developer_cn"] == "" and e02["developer_en"] == ""
    # 被过滤/排除的项目均不出现
    ids = {r["canonical_id"] for r in rows}
    assert ids == {"IND-PV-0001"}


def test_no_flat_db_skips_baseline_only_excluded(tmp_path):
    """flat_db=None：不做基线，仅排 excluded=1（B 的 CHN 项目会保留）。"""
    src, flat, out = tmp_path / "s.db", tmp_path / "f.db", tmp_path / "events_export.csv"
    _mk(str(src), str(flat))

    n = events_exporter.export_events_csv(src, [out], iso3_to_en={"IND": "India"})

    with open(out, encoding="utf-8-sig", newline="") as fh:
        ids = {r["canonical_id"] for r in csv.DictReader(fh)}
    # A(3 行) + B(1 行) = 4；C 仍排除；E03(2021) 仍按 year 过滤
    assert n == 4
    assert ids == {"IND-PV-0001", "CHN-PV-0002"}


# ── 指标扩展 2026-W30：CSV 新 8 列 ────────────────────────────────────────────

def test_new_columns_in_csv(tmp_path):
    """CSV 含子类/pv/储能/金额 8 新列：子类项目级同口径（默认渲染），数值列取事件自身值。"""
    import bri_enums
    src, flat, out = tmp_path / "s.db", tmp_path / "f.db", tmp_path / "events_export.csv"
    _mk(str(src), str(flat))

    # 给库补新列并造值（IND-PV-0001 子类留 NULL → CSV 应渲染父类默认）
    c = sqlite3.connect(str(src))
    c.execute("ALTER TABLE canonical_projects ADD COLUMN subcategory TEXT")
    c.execute("ALTER TABLE canonical_projects ADD COLUMN technology_tags_json TEXT NOT NULL DEFAULT '[]'")
    for col in ("pv_capacity_mw", "storage_power_mw", "storage_energy_mwh",
                "contract_amount_usd", "financing_amount_usd"):
        c.execute(f"ALTER TABLE project_events ADD COLUMN {col} REAL")
    c.execute("ALTER TABLE project_events ADD COLUMN generation_sources_json TEXT")
    c.execute("ALTER TABLE project_events ADD COLUMN storage_status TEXT")
    c.execute(
        "UPDATE canonical_projects SET technology_tags_json='[\"光储\"]' "
        "WHERE id='IND-PV-0001'"
    )
    c.execute(
        "UPDATE project_events SET storage_power_mw=20.0, storage_energy_mwh=80.0, "
        "contract_amount_usd=167.0, financing_amount_usd=486.0, "
        "generation_sources_json='[\"光伏\"]', storage_status='confirmed' "
        "WHERE id='IND-PV-0001-01'"
    )
    c.commit()
    c.close()

    events_exporter.export_events_csv(src, [out], flat_db=flat, iso3_to_en={"IND": "India"})

    with open(out, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
        header = list(rows[0].keys())

    for col in ("subcategory_cn", "subcategory_en", "pv_capacity_mw", "storage_power_mw",
                "storage_energy_mwh", "contract_amount_usd", "financing_amount_usd",
                "generation_sources", "storage_status", "technology_tags_cn", "technology_tags_en"):
        assert col in header, f"CSV 缺列 {col}"

    e01 = next(r for r in rows if r["event_id"] == "IND-PV-0001-01")
    _default = bri_enums.subcategory_default("光伏")
    assert e01["subcategory_cn"] == _default, "子类 NULL 应渲染父类默认"
    assert e01["subcategory_en"] == bri_enums.subcategory_en("光伏", _default)
    assert e01["storage_power_mw"] == "20.0"
    assert e01["contract_amount_usd"] == "167.0"
    assert e01["financing_amount_usd"] == "486.0"
    assert e01["pv_capacity_mw"] == "", "无值数值列为空串"
    assert e01["generation_sources"] == "光伏"
    assert e01["storage_status"] == "confirmed"
    assert e01["technology_tags_cn"] == "光储"
    assert e01["technology_tags_en"] == "Solar + Storage"

    # E02 无金额/储能值 → 空串
    e02 = next(r for r in rows if r["event_id"] == "IND-PV-0001-02")
    assert e02["storage_power_mw"] == "" and e02["contract_amount_usd"] == ""


# ── W30b：TX/MF 并入 CSV（专表基线 + 5 新列 + 分组排序）───────────────────────

def test_txmf_baseline_pure():
    """TX/MF 专表基线：仅排中港澳台 + 终态事件年 < 2022。"""
    ok = {"country_iso": "AGO", "event_year": 2023}
    assert events_exporter.passes_txmf_baseline(dict(ok)) is True
    assert events_exporter.passes_txmf_baseline({**ok, "country_iso": "CHN"}) is False
    assert events_exporter.passes_txmf_baseline({**ok, "event_year": 2021}) is False
    assert events_exporter.passes_txmf_baseline({**ok, "event_year": None}) is False


@pytest.mark.parametrize("source_value, source_unit, public_value, public_unit", [
    (5.0, "GW/年", "5.0", "GW/year"),
    (12.0, "GWh/年", "12.0", "GWh/year"),
    (3.0, "万吨/年", "30000.0", "t/year"),
])
def test_txmf_included_sorted_with_new_columns(tmp_path, source_value, source_unit, public_value, public_unit):
    """TX/MF 经 tx_flat/mf_flat 基线并入；5 新列有值；行序 主表→TX→MF。"""
    src, flat, out = tmp_path / "s.db", tmp_path / "f.db", tmp_path / "events_export.csv"
    _mk(str(src), str(flat))

    c = sqlite3.connect(str(src))
    for col in ("voltage_kv REAL", "line_length_km REAL", "transmission_capacity_mw REAL",
                "production_capacity_value REAL", "production_capacity_unit TEXT"):
        c.execute(f"ALTER TABLE project_events ADD COLUMN {col}")
    # TX 项目 D：2023 签约（应并入）；TX 项目 E：终态 2019（专表基线应排除）
    c.execute("INSERT INTO canonical_projects(id, project_name_en, country_iso, category, excluded) "
              "VALUES('AGO-TX-0001','Grid D','AGO','输变电建设',0)")
    c.execute("INSERT INTO project_events(id, canonical_id, project_stage, publish_date, voltage_kv, line_length_km) "
              "VALUES('AGO-TX-0001-01','AGO-TX-0001','签约','2023-05-01',400.0,270.0)")
    c.execute("INSERT INTO chinese_entities(event_id, implementing_entity, implementing_entity_en, is_central_soe) "
              "VALUES('AGO-TX-0001-01','中国电建','PowerChina',1)")
    c.execute("INSERT INTO canonical_projects(id, project_name_en, country_iso, category, excluded) "
              "VALUES('AGO-TX-0002','Grid E','AGO','输变电建设',0)")
    c.execute("INSERT INTO project_events(id, canonical_id, project_stage, publish_date, voltage_kv) "
              "VALUES('AGO-TX-0002-01','AGO-TX-0002','竣工','2019-06-01',220.0)")
    # MF 项目 F：2024（应并入，排在 TX 之后）
    c.execute("INSERT INTO canonical_projects(id, project_name_en, country_iso, category, excluded) "
              "VALUES('EGY-MF-0001','Factory F','EGY','新能源制造业',0)")
    c.execute("INSERT INTO project_events(id, canonical_id, project_stage, publish_date, "
              "production_capacity_value, production_capacity_unit) "
              "VALUES('EGY-MF-0001-01','EGY-MF-0001','签约','2024-02-01',?,?)", (source_value, source_unit))
    c.commit()
    c.close()

    # 专表基线源：tx_flat / mf_flat（终态事件年含全部 stage）
    f = sqlite3.connect(str(flat))
    f.execute("CREATE TABLE tx_flat(project_id TEXT PRIMARY KEY, country_iso TEXT, event_year INTEGER)")
    f.executemany("INSERT INTO tx_flat VALUES(?,?,?)",
                  [("AGO-TX-0001", "AGO", 2023), ("AGO-TX-0002", "AGO", 2019)])
    f.execute("CREATE TABLE mf_flat(project_id TEXT PRIMARY KEY, country_iso TEXT, event_year INTEGER)")
    f.execute("INSERT INTO mf_flat VALUES('EGY-MF-0001','EGY',2024)")
    f.commit()
    f.close()

    events_exporter.export_events_csv(src, [out], flat_db=flat, iso3_to_en={"IND": "India", "AGO": "Angola", "EGY": "Egypt"})

    with open(out, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
        header = list(rows[0].keys())

    for col in ("voltage_kv", "line_length_km", "transmission_capacity_mw",
                "production_capacity_value", "production_capacity_unit"):
        assert col in header, f"CSV 缺列 {col}"

    ids = [r["canonical_id"] for r in rows]
    assert "AGO-TX-0001" in ids and "EGY-MF-0001" in ids
    assert "AGO-TX-0002" not in ids, "终态 2019 的 TX 项目应被专表基线排除"

    # 分组排：主表(IND-PV) 全部在前，其后 TX，再后 MF
    groups = [0 if i.startswith("IND") else 1 if "-TX-" in i else 2 for i in ids]
    assert groups == sorted(groups), f"行序应为 主表→TX→MF，实际 {ids}"

    tx = next(r for r in rows if r["canonical_id"] == "AGO-TX-0001")
    assert tx["voltage_kv"] == "400.0" and tx["line_length_km"] == "270.0"
    assert tx["entity_en"] == "PowerChina" and tx["category_en"] == "Transmission & Distribution"
    mf = next(r for r in rows if r["canonical_id"] == "EGY-MF-0001")
    assert mf["production_capacity_value"] == public_value and mf["production_capacity_unit"] == public_unit
    # 主表行的 TX/MF 专属列为空串
    pv = next(r for r in rows if r["canonical_id"] == "IND-PV-0001")
    assert pv["voltage_kv"] == "" and pv["production_capacity_unit"] == ""

@pytest.mark.parametrize('status', ['excluded', 'pending_review'])
def test_reviewed_event_does_not_hide_valid_sibling_or_leak(tmp_path, status):
    from s03_data_exporter.flat_builder import build_flat
    src, flat, out = tmp_path / 'source.db', tmp_path / 'oldflat.db', tmp_path / 'events.csv'
    _mk(str(src), str(flat))
    with sqlite3.connect(src) as c:
        c.execute('ALTER TABLE project_events ADD COLUMN publication_status TEXT')
        c.execute('UPDATE project_events SET publication_status=? WHERE id=?', (status, 'IND-PV-0001-02'))
    # Newer operating event is withheld; legacy NULL signed event remains usable.
    rebuilt = tmp_path / 'rebuilt.db'
    build_flat(src, rebuilt, {})
    with sqlite3.connect(rebuilt) as c:
        row = c.execute("SELECT latest_stage, all_entities FROM projects_flat WHERE project_id='IND-PV-0001'").fetchone()
    assert row[0] == '签约'
    assert '中国电建' in row[1]
    events_exporter.export_events_csv(src, [out], flat_db=rebuilt)
    with out.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    assert {r['event_id'] for r in rows} == {'IND-PV-0001-01'}
    assert len(rows) == 2


def test_relation_hold_preserves_valid_sibling(tmp_path):
    from s03_data_exporter.flat_builder import build_flat
    src, oldflat, out = tmp_path / "s.db", tmp_path / "old.db", tmp_path / "e.csv"
    _mk(str(src), str(oldflat))
    with sqlite3.connect(src) as c:
        c.execute("ALTER TABLE chinese_entities ADD COLUMN publication_status TEXT")
        c.execute("UPDATE chinese_entities SET publication_status='excluded' WHERE implementing_entity='中国能建'")
    flat = tmp_path / "new.db"
    build_flat(src, flat, {})
    with sqlite3.connect(flat) as c:
        entities = c.execute("SELECT all_entities FROM projects_flat WHERE project_id='IND-PV-0001'").fetchone()[0]
    assert '中国电建' in entities and '中国能建' not in entities
    events_exporter.export_events_csv(src, [out], flat_db=oldflat)
    with out.open(encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    assert any(r['entity_cn'] == '中国电建' for r in rows)
    assert all(r['entity_cn'] != '中国能建' for r in rows)
