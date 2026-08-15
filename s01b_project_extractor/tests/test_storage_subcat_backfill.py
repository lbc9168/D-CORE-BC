"""测试 storage_subcat_backfill：入口校验、dry-run 不写库、apply 正确回填、
fill-if-null/manual 不覆盖、幂等、excluded 跳过、HB 结构化储能路径、misses CSV。"""
import sqlite3
from pathlib import Path

import pytest

from s01b_project_extractor.tools.storage_subcat_backfill import run


def _make_db(tmp_path, canonicals: list[tuple], events: list[tuple]) -> str:
    """建最小 canonical_projects + project_events（含本轮新列）临时库。

    canonicals: (id, project_name, category, excluded, subcategory)
    events: (id, canonical_id, capacity_details, project_summary, additional_remarks,
             storage_power_mw, storage_energy_mwh, pv_capacity_mw)
    """
    db = str(tmp_path / "test_ssb.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE canonical_projects (
        id TEXT PRIMARY KEY, project_name TEXT, category TEXT,
        excluded INTEGER DEFAULT 0, subcategory TEXT)""")
    conn.execute("""CREATE TABLE project_events (
        id TEXT PRIMARY KEY, canonical_id TEXT,
        capacity_details TEXT, project_summary TEXT, additional_remarks TEXT,
        storage_power_mw REAL, storage_energy_mwh REAL, pv_capacity_mw REAL)""")
    conn.executemany("INSERT INTO canonical_projects VALUES (?,?,?,?,?)", canonicals)
    conn.executemany("INSERT INTO project_events VALUES (?,?,?,?,?,?,?,?)", events)
    conn.commit()
    conn.close()
    return db


# ── 入口验证 ──────────────────────────────────────────────────────────────

def test_nonexistent_db_path_raises(tmp_path):
    with pytest.raises(ValueError, match="不存在"):
        run(str(tmp_path / "does_not_exist.db"), apply=False)


def test_missing_column_raises(tmp_path):
    """新列缺失（模拟迁移未跑）→ 抛带描述异常。"""
    db = str(tmp_path / "old_schema.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE canonical_projects (id TEXT PRIMARY KEY, project_name TEXT, category TEXT, excluded INTEGER)")
    conn.execute("CREATE TABLE project_events (id TEXT PRIMARY KEY, canonical_id TEXT, capacity_details TEXT)")
    conn.execute("INSERT INTO canonical_projects VALUES ('A-ES-0001','x','储能',0)")
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="缺列"):
        run(db, apply=False)


def test_empty_db_raises(tmp_path):
    db = _make_db(tmp_path, [], [])
    with pytest.raises(RuntimeError, match="canonical_projects"):
        run(db, apply=False)


# ── dry-run 不写库 ────────────────────────────────────────────────────────

def test_dry_run_does_not_write(tmp_path):
    db = _make_db(
        tmp_path,
        [("SAU-ES-0002", "红海储能", "储能", 0, None)],
        [("SAU-ES-0002-01", "SAU-ES-0002", "储能规模100MW/200MWh", "", "", None, None, None)],
    )
    report = run(db, apply=False)
    assert report["would_change"]["storage_power_mw"] == 1
    assert report["would_change"]["storage_energy_mwh"] == 1

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT storage_power_mw, storage_energy_mwh FROM project_events WHERE id='SAU-ES-0002-01'"
    ).fetchone()
    conn.close()
    assert row == (None, None), "dry-run 不应写库"


# ── apply 正确回填 ────────────────────────────────────────────────────────

def test_apply_fills_storage_for_es_event(tmp_path):
    db = _make_db(
        tmp_path,
        [("SAU-ES-0002", "红海储能", "储能", 0, None)],
        [("SAU-ES-0002-01", "SAU-ES-0002", "储能规模100MW/200MWh", "", "", None, None, None)],
    )
    run(db, apply=True)
    conn = sqlite3.connect(db)
    pwr, eng = conn.execute(
        "SELECT storage_power_mw, storage_energy_mwh FROM project_events WHERE id='SAU-ES-0002-01'"
    ).fetchone()
    conn.close()
    assert (pwr, eng) == (100.0, 200.0)


def test_apply_fills_pv_capacity_only_for_hb(tmp_path):
    """pv_capacity_mw 仅综合能源(HB)项目回填；纯光伏项目同样文本不填。"""
    db = _make_db(
        tmp_path,
        [("UZB-HB-0001", "光储一体化", "综合能源", 0, None),
         ("VNM-PV-0001", "越南光伏", "光伏", 0, None)],
        [("UZB-HB-0001-01", "UZB-HB-0001", "PV: 100MW, BESS: 50MW", "", "", None, None, None),
         ("VNM-PV-0001-01", "VNM-PV-0001", "PV: 100MW, BESS: 50MW", "", "", None, None, None)],
    )
    run(db, apply=True)
    conn = sqlite3.connect(db)
    hb_pv, hb_storage = conn.execute(
        "SELECT pv_capacity_mw, storage_power_mw FROM project_events WHERE id='UZB-HB-0001-01'"
    ).fetchone()
    pv_pv = conn.execute(
        "SELECT pv_capacity_mw FROM project_events WHERE id='VNM-PV-0001-01'"
    ).fetchone()[0]
    conn.close()
    assert hb_pv == 100.0
    assert hb_storage == 50.0, "储能回填不限类别"
    assert pv_pv is None, "纯光伏项目不填 pv_capacity_mw"


def test_apply_sets_pv_subcategory_on_evidence(tmp_path):
    """有分布式证据的光伏项目写子类；无证据保持 NULL（导出层默认集中式）。"""
    db = _make_db(
        tmp_path,
        [("VNM-PV-0001", "越南屋顶光伏", "光伏", 0, None),
         ("VNM-PV-0002", "越南光伏电站", "光伏", 0, None)],
        [("VNM-PV-0001-01", "VNM-PV-0001", "", "工商业屋顶分布式光伏", "", None, None, None),
         ("VNM-PV-0002-01", "VNM-PV-0002", "", "100MW地面光伏电站", "", None, None, None)],
    )
    run(db, apply=True)
    conn = sqlite3.connect(db)
    s1 = conn.execute("SELECT subcategory FROM canonical_projects WHERE id='VNM-PV-0001'").fetchone()[0]
    s2 = conn.execute("SELECT subcategory FROM canonical_projects WHERE id='VNM-PV-0002'").fetchone()[0]
    conn.close()
    assert s1 == "分布式"
    assert s2 is None, "无证据不写库（导出层渲染默认）"


def test_hb_light_storage_subcategory_is_deprecated_and_battery_only_is_unclear(tmp_path):
    """2B-7 不再新增 HB/光储，且只有 battery+MWh 的含糊文本不得回填 confirmed 储能。"""
    db = _make_db(
        tmp_path,
        [("UZB-HB-0002", "综合能源项目", "综合能源", 0, None)],
        [("UZB-HB-0002-01", "UZB-HB-0002", "光伏200兆瓦，电池容量 400MWh", "", "", None, None, None)],
    )
    run(db, apply=True)
    conn = sqlite3.connect(db)
    sub = conn.execute("SELECT subcategory FROM canonical_projects WHERE id='UZB-HB-0002'").fetchone()[0]
    eng = conn.execute("SELECT storage_energy_mwh FROM project_events WHERE id='UZB-HB-0002-01'").fetchone()[0]
    pv = conn.execute("SELECT pv_capacity_mw FROM project_events WHERE id='UZB-HB-0002-01'").fetchone()[0]
    conn.close()
    assert eng is None
    assert pv == 200.0
    assert sub is None


def test_hb_wind_solar_storage_stays_null(tmp_path):
    """风光储项目非纯光储 → subcategory 保持 NULL（=其他）。"""
    db = _make_db(
        tmp_path,
        [("KAZ-HB-0001", "风光储一体化", "综合能源", 0, None)],
        [("KAZ-HB-0001-01", "KAZ-HB-0001", "风电50MW，光伏100MW，储能20MW", "", "", None, None, None)],
    )
    run(db, apply=True)
    conn = sqlite3.connect(db)
    sub = conn.execute("SELECT subcategory FROM canonical_projects WHERE id='KAZ-HB-0001'").fetchone()[0]
    conn.close()
    assert sub is None


# ── fill-if-null / manual 不覆盖 ─────────────────────────────────────────

def test_existing_storage_value_not_overwritten(tmp_path):
    db = _make_db(
        tmp_path,
        [("SAU-ES-0003", "储能项目", "储能", 0, None)],
        [("SAU-ES-0003-01", "SAU-ES-0003", "储能规模100MW/200MWh", "", "", 999.0, None, None)],
    )
    run(db, apply=True)
    conn = sqlite3.connect(db)
    pwr, eng = conn.execute(
        "SELECT storage_power_mw, storage_energy_mwh FROM project_events WHERE id='SAU-ES-0003-01'"
    ).fetchone()
    conn.close()
    assert pwr == 999.0, "已有值不应被覆盖"
    assert eng == 200.0, "NULL 列仍应回填"


def test_manual_subcategory_not_overwritten(tmp_path):
    """人工写入的子类（含与规则相反的值）绝不被覆盖。"""
    db = _make_db(
        tmp_path,
        [("VNM-PV-0003", "屋顶光伏", "光伏", 0, "集中式")],
        [("VNM-PV-0003-01", "VNM-PV-0003", "", "屋顶分布式光伏", "", None, None, None)],
    )
    run(db, apply=True)
    conn = sqlite3.connect(db)
    sub = conn.execute("SELECT subcategory FROM canonical_projects WHERE id='VNM-PV-0003'").fetchone()[0]
    conn.close()
    assert sub == "集中式"


# ── 幂等 / excluded ──────────────────────────────────────────────────────

def test_idempotent_rerun_zero_changes(tmp_path):
    db = _make_db(
        tmp_path,
        [("SAU-ES-0002", "红海储能", "储能", 0, None),
         ("VNM-PV-0001", "越南屋顶光伏", "光伏", 0, None)],
        [("SAU-ES-0002-01", "SAU-ES-0002", "储能规模100MW/200MWh", "", "", None, None, None),
         ("VNM-PV-0001-01", "VNM-PV-0001", "", "屋顶分布式光伏", "", None, None, None)],
    )
    run(db, apply=True)
    report2 = run(db, apply=False)
    assert all(v == 0 for v in report2["would_change"].values())


def test_excluded_canonical_skipped(tmp_path):
    db = _make_db(
        tmp_path,
        [("XXX-ES-0001", "已排除储能", "储能", 1, None)],
        [("XXX-ES-0001-01", "XXX-ES-0001", "储能规模100MW/200MWh", "", "", None, None, None)],
    )
    run(db, apply=True)
    conn = sqlite3.connect(db)
    pwr = conn.execute(
        "SELECT storage_power_mw FROM project_events WHERE id='XXX-ES-0001-01'"
    ).fetchone()[0]
    sub = conn.execute("SELECT subcategory FROM canonical_projects WHERE id='XXX-ES-0001'").fetchone()[0]
    conn.close()
    assert pwr is None
    assert sub is None


# ── misses CSV ────────────────────────────────────────────────────────────

def test_misses_csv_contains_es_and_hb_misses(tmp_path):
    db = _make_db(
        tmp_path,
        [("SAU-ES-0004", "储能项目", "储能", 0, None),
         ("UZB-HB-0003", "光储项目", "综合能源", 0, None)],
        [("SAU-ES-0004-01", "SAU-ES-0004", "电站配套设施工程", "", "", None, None, None),
         ("UZB-HB-0003-01", "UZB-HB-0003", "光伏与储能设施若干", "", "", None, None, None)],
    )
    out_csv = str(tmp_path / "misses.csv")
    run(db, apply=False, out_csv=out_csv)
    text = Path(out_csv).read_text(encoding="utf-8-sig")
    assert "es_no_storage_signal" in text
    assert "hb_no_pv_capacity" in text


def test_run_returns_summary_dict(tmp_path):
    db = _make_db(
        tmp_path,
        [("SAU-ES-0002", "红海储能", "储能", 0, None)],
        [("SAU-ES-0002-01", "SAU-ES-0002", "储能规模100MW/200MWh", "", "", None, None, None)],
    )
    report = run(db, apply=False)
    assert set(report["would_change"].keys()) == {
        "storage_power_mw", "storage_energy_mwh", "pv_capacity_mw", "subcategory",
    }
    assert "misses" in report
