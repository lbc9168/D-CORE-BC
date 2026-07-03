"""projects.db 回溯清扫：dry-run 不写库；--apply 先备份再事务批量置 excluded=1。"""
import sqlite3
from pathlib import Path
from s01b_project_extractor.tools.nonenergy_sweep import sweep


def _make_db(tmp_path) -> str:
    db = str(tmp_path / "projects.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE extracted_projects (
        id INTEGER PRIMARY KEY, project_name TEXT, project_summary TEXT,
        category TEXT, capacity_mw REAL, capacity_details TEXT,
        excluded INTEGER DEFAULT 0, exclude_reason TEXT, exclude_source TEXT)""")
    conn.executemany(
        "INSERT INTO extracted_projects (project_name, project_summary, category, capacity_mw, capacity_details, excluded) VALUES (?,?,?,?,?,?)",
        [
            ("加拿大盐湖钾肥矿业并购项目", "钾盐矿380万吨/年", "综合能源", 3800000, "380万吨/年", 0),
            ("赞比亚乔瓦矿山光储微网项目", "", "综合能源", 30, "PV 20MW + BESS 10MWh", 0),
            ("伊拉克SCP水泥生产线项目", "", "综合能源", None, "", 1),  # 已排除的跳过
        ])
    conn.commit(); conn.close()
    return db


def test_dry_run_reports_without_writing(tmp_path):
    db = _make_db(tmp_path)
    hits = sweep(db, apply=False)
    assert [h["project_name"] for h in hits] == ["加拿大盐湖钾肥矿业并购项目"]
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT excluded FROM extracted_projects WHERE id=1").fetchone()[0] == 0
    conn.close()


def test_apply_writes_with_backup(tmp_path):
    db = _make_db(tmp_path)
    hits = sweep(db, apply=True)
    assert len(hits) == 1
    conn = sqlite3.connect(db)
    excl, src = conn.execute(
        "SELECT excluded, exclude_source FROM extracted_projects WHERE id=1").fetchone()
    legit = conn.execute("SELECT excluded FROM extracted_projects WHERE id=2").fetchone()[0]
    conn.close()
    assert (excl, src) == (1, "deterministic_guard")
    assert legit == 0, "legit 行不许被动"
    assert list(Path(db).parent.glob("projects.db.bak.*")), "apply 前应有备份"


def test_out_csv_written(tmp_path):
    db = _make_db(tmp_path)
    out = str(tmp_path / "report.csv")
    sweep(db, apply=False, out_csv=out)
    text = Path(out).read_text(encoding="utf-8-sig")
    assert "钾肥" in text and "reason" in text
