"""主库（bri_re_database.db）非能源回填：dry-run 不写库；--apply 先备份再事务置 excluded=1。

背景：2026-07-04 发现 projects.db 清扫无主库回填机制（治标 77 条是一次性事务），
基准案 SUR-XX-0001（灌溉排水修复）主库仍 excluded=0。用户原则：不重建 s02a，原地回填。
"""
import sqlite3
from pathlib import Path
from s01b_project_extractor.tools.nonenergy_sweep_maindb import sweep_maindb


def _make_db(tmp_path) -> str:
    db = str(tmp_path / "bri_re_database.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE canonical_projects (
        id TEXT PRIMARY KEY, project_name TEXT, category TEXT,
        excluded INTEGER DEFAULT 0, excluded_reason TEXT)""")
    conn.execute("""CREATE TABLE project_events (
        id TEXT PRIMARY KEY, canonical_id TEXT, project_summary TEXT,
        capacity_details TEXT, capacity_mw REAL)""")
    conn.executemany(
        "INSERT INTO canonical_projects (id, project_name, category, excluded) VALUES (?,?,?,?)",
        [
            ("SUR-XX-0001", "苏里南尼克里区灌溉和排水系统设施修复项目", "Unknown", 0),
            ("ZMB-HB-0001", "赞比亚乔瓦矿山光储微网项目", "综合能源", 0),
            ("IRQ-TH-0001", "伊拉克SCP水泥生产线项目", "综合能源", 1),  # 已排除的跳过
            ("COD-TX-0004", "刚果（金）TFM矿区供电项目", "输变电建设", 0),  # 输变电保护词
        ])
    conn.executemany(
        "INSERT INTO project_events (id, canonical_id, project_summary, capacity_details, capacity_mw) VALUES (?,?,?,?,?)",
        [
            ("SUR-XX-0001-01", "SUR-XX-0001", "灌溉和排水系统设施修复，支持农业生产。", "清理沟渠290公里", None),
            # 多事件聚合：任一事件的容量明细含发电语义即保护整个 canonical
            ("ZMB-HB-0001-01", "ZMB-HB-0001", "", "", None),
            ("ZMB-HB-0001-02", "ZMB-HB-0001", "", "PV 20MW + BESS 10MWh", 30),
            ("COD-TX-0004-01", "COD-TX-0004", "输变电领域最大的EPC总承包项目。", "", None),
        ])
    conn.commit(); conn.close()
    return db


def test_dry_run_reports_without_writing(tmp_path):
    db = _make_db(tmp_path)
    hits = sweep_maindb(db, apply=False)
    assert [h["id"] for h in hits] == ["SUR-XX-0001"]
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT excluded FROM canonical_projects WHERE id='SUR-XX-0001'").fetchone()[0] == 0
    conn.close()


def test_apply_writes_with_backup(tmp_path):
    db = _make_db(tmp_path)
    hits = sweep_maindb(db, apply=True)
    assert len(hits) == 1
    conn = sqlite3.connect(db)
    excl, reason = conn.execute(
        "SELECT excluded, excluded_reason FROM canonical_projects WHERE id='SUR-XX-0001'").fetchone()
    legit = conn.execute(
        "SELECT excluded FROM canonical_projects WHERE id='ZMB-HB-0001'").fetchone()[0]
    grid = conn.execute(
        "SELECT excluded FROM canonical_projects WHERE id='COD-TX-0004'").fetchone()[0]
    conn.close()
    assert excl == 1 and "灌溉" in reason
    assert legit == 0, "光储微网不许被动"
    assert grid == 0, "输变电工程不许被动"
    bak_files = list(Path(db).parent.glob("bri_re_database.db.bak.*"))
    assert bak_files, "apply 前应有备份"
    # 备份须捕获写前状态
    bak_conn = sqlite3.connect(str(bak_files[0]))
    bak_excl = bak_conn.execute(
        "SELECT excluded FROM canonical_projects WHERE id='SUR-XX-0001'").fetchone()[0]
    bak_conn.close()
    assert bak_excl == 0, "备份应捕获写前状态"


def test_out_csv_written(tmp_path):
    db = _make_db(tmp_path)
    out = str(tmp_path / "report.csv")
    sweep_maindb(db, apply=False, out_csv=out)
    text = Path(out).read_text(encoding="utf-8-sig")
    assert "灌溉" in text and "reason" in text
