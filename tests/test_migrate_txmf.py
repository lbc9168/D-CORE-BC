"""测试 scripts/migrate_add_txmf_columns：dry-run 报告、apply 落库、幂等重跑、备份三重断言。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.migrate_add_txmf_columns import (
    _NEW_COLUMNS,
    _existing_columns,
    apply_migration,
    dry_run,
)


def _make_db(path: Path) -> None:
    """建立最小 project_events / canonical_projects 测试库（无 TX/MF 列）。"""
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE canonical_projects (
                id TEXT PRIMARY KEY,
                project_name TEXT
            );
            CREATE TABLE project_events (
                id TEXT PRIMARY KEY,
                canonical_id TEXT NOT NULL
            );
            """
        )
        conn.execute("INSERT INTO canonical_projects VALUES ('C-0001', 'demo')")
        conn.execute("INSERT INTO project_events VALUES ('C-0001-01', 'C-0001')")


def test_dry_run_reports_all_columns_missing(tmp_path):
    """全新库上 dry-run 应报告全部 5 列待添加、0 列已存在。"""
    db_path = tmp_path / "t.db"
    _make_db(db_path)

    report = dry_run(db_path)

    assert set(report["to_add"]) == {c for c, _ in _NEW_COLUMNS}
    assert report["existing"] == []


def test_apply_adds_columns_and_preserves_rows(tmp_path):
    """apply 后 5 列齐全，且 canonical_projects/project_events 行数不变。"""
    db_path = tmp_path / "t.db"
    _make_db(db_path)

    report = apply_migration(db_path)
    assert set(report["to_add"]) == {c for c, _ in _NEW_COLUMNS}

    conn = sqlite3.connect(db_path)
    try:
        cols = _existing_columns(conn, "project_events")
        for col, _decl in _NEW_COLUMNS:
            assert col in cols
        assert conn.execute("SELECT COUNT(*) FROM canonical_projects").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM project_events").fetchone()[0] == 1
    finally:
        conn.close()


def test_apply_creates_backup_with_matching_row_count(tmp_path):
    """apply 应产生 .bak.<timestamp> 备份文件，且 canonical_projects 行数与源库一致。"""
    db_path = tmp_path / "t.db"
    _make_db(db_path)

    apply_migration(db_path)

    backups = list(tmp_path.glob("t.db.bak.*"))
    assert len(backups) == 1
    bak = backups[0]
    assert bak.stat().st_size > 0

    with sqlite3.connect(bak) as conn:
        assert conn.execute("SELECT COUNT(*) FROM canonical_projects").fetchone()[0] == 1


def test_apply_is_idempotent_second_run_zero_new_columns(tmp_path):
    """第二次 apply 应报告 9 列已存在、0 变更（幂等），行数依旧不变。"""
    db_path = tmp_path / "t.db"
    _make_db(db_path)

    first = apply_migration(db_path)
    assert len(first["to_add"]) == 9

    second = apply_migration(db_path)
    assert second["to_add"] == []
    assert set(second["existing"]) == {c for c, _ in _NEW_COLUMNS}

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM canonical_projects").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM project_events").fetchone()[0] == 1
    finally:
        conn.close()


def test_dry_run_after_apply_reports_all_existing(tmp_path):
    """apply 后再跑 dry-run（只读连接）应报告全部 9 列已存在。"""
    db_path = tmp_path / "t.db"
    _make_db(db_path)
    apply_migration(db_path)

    report = dry_run(db_path)
    assert report["to_add"] == []
    assert set(report["existing"]) == {c for c, _ in _NEW_COLUMNS}
