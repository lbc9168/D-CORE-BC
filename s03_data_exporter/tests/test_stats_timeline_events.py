"""事件级时间统计：一个项目在它经历的每个阶段-年份各计一次（含 country_iso 维度）。"""
import sqlite3

from s03_data_exporter.stats_builder import build_stats


def _mk_src(p):
    """构造源库：单项目 2024 签约 + 2026 竣工，country_iso=IND。"""
    c = sqlite3.connect(p)
    c.executescript(
        """
        CREATE TABLE canonical_projects(
            id TEXT PRIMARY KEY, category TEXT, country_iso TEXT,
            excluded INTEGER DEFAULT 0
        );
        CREATE TABLE project_events(
            id TEXT, canonical_id TEXT, project_stage TEXT,
            publish_date TEXT, investment_date TEXT, capacity_mw REAL
        );
        """
    )
    c.execute("INSERT INTO canonical_projects VALUES('IND-PV-0001','光伏','IND',0)")
    c.execute(
        "INSERT INTO project_events "
        "VALUES('IND-PV-0001-01','IND-PV-0001','签约','2024-03-01',NULL,50)"
    )
    c.execute(
        "INSERT INTO project_events "
        "VALUES('IND-PV-0001-02','IND-PV-0001','竣工','2026-09-01',NULL,50)"
    )
    # excluded 项目不应进事件级表
    c.execute("INSERT INTO canonical_projects VALUES('IND-PV-0002','光伏','IND',1)")
    c.execute(
        "INSERT INTO project_events "
        "VALUES('IND-PV-0002-01','IND-PV-0002','签约','2024-05-01',NULL,30)"
    )
    c.commit()
    c.close()


def _mk_empty_flat(dst):
    """旧三表读 projects_flat，造一个空表让 build_stats 能跑完。"""
    d = sqlite3.connect(dst)
    d.execute(
        "CREATE TABLE projects_flat(project_id TEXT, category TEXT, event_year INTEGER, "
        "latest_stage TEXT, capacity_mw REAL, primary_entity TEXT, "
        "primary_entity_en TEXT, country_iso TEXT)"
    )
    d.commit()
    d.close()


def test_event_level_counts_each_milestone_year(tmp_path):
    src = tmp_path / "src.db"
    dst = tmp_path / "dst.db"
    _mk_src(src)
    _mk_empty_flat(dst)

    build_stats(dst, src)

    con = sqlite3.connect(dst)
    rows = con.execute(
        "SELECT year, stage, project_count FROM stats_timeline_events ORDER BY year"
    ).fetchall()
    con.close()

    # 同一项目跨阶段，2024（签约）与 2026（竣工）各计一次
    assert (2024, "签约", 1) in rows
    assert (2026, "竣工", 1) in rows


def test_event_level_has_country_and_month_dims(tmp_path):
    src = tmp_path / "src.db"
    dst = tmp_path / "dst.db"
    _mk_src(src)
    _mk_empty_flat(dst)

    build_stats(dst, src)

    con = sqlite3.connect(dst)
    row = con.execute(
        "SELECT country_iso, month, capacity_mw FROM stats_timeline_events "
        "WHERE year=2024 AND stage='签约'"
    ).fetchone()
    con.close()

    assert row[0] == "IND"
    assert row[1] == "2024-03"
    assert row[2] == 50.0


def test_event_level_excludes_excluded_projects(tmp_path):
    src = tmp_path / "src.db"
    dst = tmp_path / "dst.db"
    _mk_src(src)
    _mk_empty_flat(dst)

    build_stats(dst, src)

    con = sqlite3.connect(dst)
    # IND-PV-0002 excluded，其 2024-05 签约不应计入
    total_2024_qian = con.execute(
        "SELECT SUM(project_count) FROM stats_timeline_events "
        "WHERE year=2024 AND stage='签约'"
    ).fetchone()[0]
    con.close()

    assert total_2024_qian == 1
