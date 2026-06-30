"""S4.1：events_export.csv 一行一个 project_event，含 event_id + 4 大类映射列。"""
import csv
import sqlite3

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


def test_baseline_excludes_missing_capacity_for_capacity_category():
    # 光伏 应有容量，capacity 缺失 → 排除
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
