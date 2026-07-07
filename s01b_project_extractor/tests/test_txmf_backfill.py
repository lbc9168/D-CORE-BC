"""TDD 测试：txmf_backfill —— TX/MF 专用指标存量回填 CLI（阶段3 任务3.2）。

覆盖：入口校验（缺列抛异常）、dry-run 不写库、apply 写入正确值、fill-if-null
不覆盖已有值、幂等（重跑 0 变更）、misses CSV 写出、excluded 行不参与扫描。
"""
import sqlite3

import pytest

from s01b_project_extractor.tools.txmf_backfill import run


def _make_db(tmp_path, rows: list[tuple]) -> str:
    """建最小 canonical_projects + project_events（含 3.1 五列）临时库。

    rows: (canonical_id, category, excluded, event_id, capacity_details,
           project_summary, capacity_mw_raw,
           voltage_kv, line_length_km, transmission_capacity_mw,
           production_capacity_value, production_capacity_unit)
    """
    db = str(tmp_path / "test_txmf.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE canonical_projects (
        id TEXT PRIMARY KEY, project_name TEXT, category TEXT,
        excluded INTEGER DEFAULT 0, excluded_reason TEXT)""")
    conn.execute("""CREATE TABLE project_events (
        id TEXT PRIMARY KEY, canonical_id TEXT,
        capacity_details TEXT, project_summary TEXT, capacity_mw_raw TEXT,
        voltage_kv REAL, line_length_km REAL, transmission_capacity_mw REAL,
        production_capacity_value REAL, production_capacity_unit TEXT)""")

    canon_seen: set[str] = set()
    for r in rows:
        cid, cat, excl = r[0], r[1], r[2]
        if cid not in canon_seen:
            conn.execute(
                "INSERT INTO canonical_projects (id, project_name, category, excluded) VALUES (?,?,?,?)",
                (cid, cid, cat, excl),
            )
            canon_seen.add(cid)
        event_id, details, summary, raw, voltage, length, tcap, value, unit = r[3:]
        conn.execute(
            """INSERT INTO project_events
               (id, canonical_id, capacity_details, project_summary, capacity_mw_raw,
                voltage_kv, line_length_km, transmission_capacity_mw,
                production_capacity_value, production_capacity_unit)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (event_id, cid, details, summary, raw, voltage, length, tcap, value, unit),
        )
    conn.commit()
    conn.close()
    return db


def _tx_row(cid, event_id, details, summary, raw="", excl=0,
            voltage=None, length=None, tcap=None):
    return (cid, "输变电建设", excl, event_id, details, summary, raw,
            voltage, length, tcap, None, None)


def _mf_row(cid, event_id, details, summary, raw="", excl=0,
            value=None, unit=None):
    return (cid, "新能源制造业", excl, event_id, details, summary, raw,
            None, None, None, value, unit)


# ── 入口验证 ──────────────────────────────────────────────────────────────

def test_nonexistent_db_path_raises(tmp_path):
    """db_path 不存在 → Pydantic 入口校验立即抛 ValueError（规则4）。"""
    with pytest.raises(ValueError, match="不存在"):
        run(str(tmp_path / "does_not_exist.db"), apply=False)


def test_missing_column_raises(tmp_path):
    """5 列缺失（模拟 3.1 未跑）→ 抛带描述异常。"""
    db = str(tmp_path / "old_schema.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE canonical_projects (
        id TEXT PRIMARY KEY, project_name TEXT, category TEXT,
        excluded INTEGER DEFAULT 0, excluded_reason TEXT)""")
    conn.execute("""CREATE TABLE project_events (
        id TEXT PRIMARY KEY, canonical_id TEXT, capacity_details TEXT,
        project_summary TEXT, capacity_mw_raw TEXT)""")
    conn.execute("INSERT INTO canonical_projects VALUES ('A-TX-0001','x','输变电建设',0,NULL)")
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="缺列"):
        run(db, apply=False)


def test_empty_db_raises(tmp_path):
    """canonical_projects 为空 → 抛带描述异常（规则8）。"""
    db = str(tmp_path / "empty.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE canonical_projects (
        id TEXT PRIMARY KEY, project_name TEXT, category TEXT,
        excluded INTEGER DEFAULT 0, excluded_reason TEXT)""")
    conn.execute("""CREATE TABLE project_events (
        id TEXT PRIMARY KEY, canonical_id TEXT, capacity_details TEXT,
        project_summary TEXT, capacity_mw_raw TEXT,
        voltage_kv REAL, line_length_km REAL, transmission_capacity_mw REAL,
        production_capacity_value REAL, production_capacity_unit TEXT)""")
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="canonical_projects"):
        run(db, apply=False)


# ── dry-run 不写库 ────────────────────────────────────────────────────────

def test_dry_run_does_not_write(tmp_path):
    db = _make_db(tmp_path, [
        _tx_row("SAU-TX-0005", "SAU-TX-0005-02", "总输送容量700万千瓦",
                "±500千伏柔性直流换流站", "7000000"),
    ])
    report = run(db, apply=False)
    assert report["would_change"]["transmission_capacity_mw"] >= 1
    assert report["would_change"]["voltage_kv"] >= 1

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT voltage_kv, transmission_capacity_mw FROM project_events WHERE id='SAU-TX-0005-02'"
    ).fetchone()
    conn.close()
    assert row == (None, None), "dry-run 不应写库"


# ── apply 写入正确值 ──────────────────────────────────────────────────────

def test_apply_writes_tx_values(tmp_path):
    db = _make_db(tmp_path, [
        _tx_row("SAU-TX-0005", "SAU-TX-0005-02", "总输送容量700万千瓦",
                "±500千伏柔性直流换流站", "7000000"),
    ])
    run(db, apply=True)
    conn = sqlite3.connect(db)
    voltage, tcap = conn.execute(
        "SELECT voltage_kv, transmission_capacity_mw FROM project_events WHERE id='SAU-TX-0005-02'"
    ).fetchone()
    conn.close()
    assert voltage == 500.0
    assert tcap == 7000.0


def test_apply_writes_mf_pair(tmp_path):
    db = _make_db(tmp_path, [
        _mf_row("EGY-MF-0002", "EGY-MF-0002-01", "年产能5吉瓦",
                "在埃及建设一座年产能5吉瓦的产业园区", "5000"),
    ])
    run(db, apply=True)
    conn = sqlite3.connect(db)
    value, unit = conn.execute(
        "SELECT production_capacity_value, production_capacity_unit FROM project_events WHERE id='EGY-MF-0002-01'"
    ).fetchone()
    conn.close()
    assert value == 5.0
    assert unit == "GW/年"


def test_apply_backs_up_db(tmp_path):
    db = _make_db(tmp_path, [
        _tx_row("PAK-TX-0014", "PAK-TX-0014-01", "输电容量4000兆瓦", "±660千伏直流工程"),
    ])
    run(db, apply=True)
    from pathlib import Path
    baks = list(Path(db).parent.glob("test_txmf.db.bak.*"))
    assert baks, "apply 应产生 WAL 备份"


# ── fill-if-null 不覆盖已有值 ─────────────────────────────────────────────

def test_fill_if_null_does_not_overwrite_existing(tmp_path):
    """已有非空 voltage_kv 值绝不被覆盖，即使解析结果不同。"""
    db = _make_db(tmp_path, [
        _tx_row("SAU-TX-0005", "SAU-TX-0005-02", "总输送容量700万千瓦",
                "±500千伏柔性直流换流站", "7000000", voltage=999.0),
    ])
    run(db, apply=True)
    conn = sqlite3.connect(db)
    voltage = conn.execute(
        "SELECT voltage_kv FROM project_events WHERE id='SAU-TX-0005-02'"
    ).fetchone()[0]
    conn.close()
    assert voltage == 999.0, "已有值不应被覆盖"


def test_mf_pair_not_overwritten_when_value_already_set(tmp_path):
    """MF 已有 production_capacity_value 非空 → 整对跳过，不覆盖。"""
    db = _make_db(tmp_path, [
        _mf_row("EGY-MF-0002", "EGY-MF-0002-01", "年产能5吉瓦",
                "在埃及建设一座年产能5吉瓦的产业园区", "5000", value=1.0, unit="万吨/年"),
    ])
    run(db, apply=True)
    conn = sqlite3.connect(db)
    value, unit = conn.execute(
        "SELECT production_capacity_value, production_capacity_unit FROM project_events WHERE id='EGY-MF-0002-01'"
    ).fetchone()
    conn.close()
    assert (value, unit) == (1.0, "万吨/年")


# ── 幂等：apply 后重跑应 0 变更 ───────────────────────────────────────────

def test_idempotent_rerun_zero_changes(tmp_path):
    db = _make_db(tmp_path, [
        _tx_row("BRA-TX-0004", "BRA-TX-0004-01", "额定输送容量500万千瓦",
                "±800千伏特高压直流输电项目，新建1468千米输电线路"),
        _mf_row("USA-MF-0001", "USA-MF-0001-01", "年产20万吨电解液",
                "旨在形成年产20万吨电解液产能"),
    ])
    run(db, apply=True)
    report2 = run(db, apply=False)
    assert all(v == 0 for v in report2["would_change"].values())


# ── excluded 行不参与扫描 ─────────────────────────────────────────────────

def test_excluded_canonical_skipped(tmp_path):
    db = _make_db(tmp_path, [
        _tx_row("XXX-TX-0001", "XXX-TX-0001-01", "总输送容量700万千瓦",
                "±500千伏", excl=1),
    ])
    run(db, apply=True)
    conn = sqlite3.connect(db)
    voltage = conn.execute(
        "SELECT voltage_kv FROM project_events WHERE id='XXX-TX-0001-01'"
    ).fetchone()[0]
    conn.close()
    assert voltage is None, "excluded canonical 不应被回填"


# ── misses CSV ────────────────────────────────────────────────────────────

def test_misses_csv_written_with_actionable_rows(tmp_path):
    db = _make_db(tmp_path, [
        _tx_row("XXX-TX-0002", "XXX-TX-0002-01", "变电站土建工程", "变电站土建工程已开工"),
    ])
    out_csv = str(tmp_path / "misses.csv")
    run(db, apply=False, out_csv=out_csv)
    from pathlib import Path
    text = Path(out_csv).read_text(encoding="utf-8-sig")
    assert "XXX-TX-0002-01" in text
    assert "reason" in text


def test_misses_csv_excludes_rows_with_no_text_at_all(tmp_path):
    """双列全空且事件无任何文本的不入 CSV（不可行动）。"""
    db = _make_db(tmp_path, [
        _tx_row("XXX-TX-0003", "XXX-TX-0003-01", "", ""),
    ])
    out_csv = str(tmp_path / "misses2.csv")
    run(db, apply=False, out_csv=out_csv)
    from pathlib import Path
    text = Path(out_csv).read_text(encoding="utf-8-sig")
    assert "XXX-TX-0003-01" not in text


# ── 摘要统计返回值 ────────────────────────────────────────────────────────

def test_run_returns_summary_dict(tmp_path):
    db = _make_db(tmp_path, [
        _tx_row("PAK-TX-0014", "PAK-TX-0014-01", "输电容量4000兆瓦", "±660千伏直流工程"),
        _mf_row("SAU-MF-0003", "SAU-MF-0003-01", "6GW高效异质结组件生产基地",
                "沙特建设一座6GW的高效异质结组件生产基地"),
    ])
    report = run(db, apply=False)
    assert "would_change" in report
    assert "misses" in report
    assert set(report["would_change"].keys()) >= {
        "voltage_kv", "line_length_km", "transmission_capacity_mw",
        "production_capacity_value",
    }
