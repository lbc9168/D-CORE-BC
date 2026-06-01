"""s01b: extracted_projects 应含 excluded/exclude_reason/exclude_source 三列（新库 + 旧库迁移）。"""
import sqlite3
from s01b_project_extractor.tools.project_extractor_tool import _init_projects_db


def _cols(conn) -> set:
    return {r[1] for r in conn.execute("PRAGMA table_info(extracted_projects)")}


def test_fresh_db_has_exclude_columns(tmp_path):
    """新建库直接含三列。"""
    db = tmp_path / "projects.db"
    conn = _init_projects_db(str(db))
    try:
        cols = _cols(conn)
        assert {"excluded", "exclude_reason", "exclude_source"} <= cols
    finally:
        conn.close()


def test_legacy_db_migrates_exclude_columns(tmp_path):
    """旧库（无三列）经 _init_projects_db 迁移后补齐三列，且幂等可重入。"""
    db = tmp_path / "projects.db"
    raw = sqlite3.connect(str(db))
    raw.execute("CREATE TABLE extracted_projects (id INTEGER PRIMARY KEY, project_name TEXT)")
    raw.commit(); raw.close()
    conn = _init_projects_db(str(db)); conn.close()
    conn2 = _init_projects_db(str(db))  # 二次调用应幂等不报错
    try:
        assert {"excluded", "exclude_reason", "exclude_source"} <= _cols(conn2)
    finally:
        conn2.close()


def test_excluded_defaults_zero(tmp_path):
    """excluded 列默认 0。"""
    db = tmp_path / "projects.db"
    conn = _init_projects_db(str(db))
    try:
        conn.execute("INSERT INTO extracted_projects (project_name) VALUES ('x')")
        conn.commit()
        val = conn.execute("SELECT excluded FROM extracted_projects WHERE project_name='x'").fetchone()[0]
        assert val == 0
    finally:
        conn.close()
