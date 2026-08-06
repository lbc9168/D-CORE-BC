"""安全迁移事件能源事实列与 canonical 影子分类快照表。"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_EVENT_COLUMNS = (
    ("generation_sources_json", "TEXT"),
    ("storage_status", "TEXT"),
    ("storage_power_mw_raw", "TEXT"),
    ("storage_energy_mwh_raw", "TEXT"),
    ("energy_composition_evidence", "TEXT"),
)
_SHADOW_COLUMNS = (
    ("storage_power_mw", "REAL"),
    ("storage_energy_mwh", "REAL"),
)
_SHADOW_SQL = """
CREATE TABLE project_classification_shadow (
    canonical_id             TEXT PRIMARY KEY REFERENCES canonical_projects(id),
    generation_sources_json  TEXT NOT NULL DEFAULT '[]',
    storage_status           TEXT NOT NULL DEFAULT 'not_mentioned',
    storage_power_mw         REAL,
    storage_energy_mwh       REAL,
    proposed_category        TEXT,
    technology_tags_json     TEXT NOT NULL DEFAULT '[]',
    classification_version   TEXT NOT NULL,
    needs_review             INTEGER NOT NULL DEFAULT 0,
    review_reason            TEXT,
    classification_source    TEXT NOT NULL DEFAULT 'policy',
    manual_locked            INTEGER NOT NULL DEFAULT 0,
    updated_at               TEXT NOT NULL
)
"""


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """返回指定 SQLite 表的现有列名集合。"""
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _has_shadow_table(conn: sqlite3.Connection) -> bool:
    """判断 canonical 影子分类快照表是否已存在。"""
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_classification_shadow'"
    ).fetchone() is not None


def _inspect(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """检查待迁移对象并返回 existing/to_add 报告。"""
    event_columns = _columns(conn, "project_events")
    report: dict[str, list[str]] = {"existing": [], "to_add": []}
    for name, _decl in _EVENT_COLUMNS:
        key = f"project_events.{name}"
        report["existing" if name in event_columns else "to_add"].append(key)
    table_key = "table.project_classification_shadow"
    report["existing" if _has_shadow_table(conn) else "to_add"].append(table_key)
    if _has_shadow_table(conn):
        shadow_columns = _columns(conn, "project_classification_shadow")
        for name, _decl in _SHADOW_COLUMNS:
            key = f"project_classification_shadow.{name}"
            report["existing" if name in shadow_columns else "to_add"].append(key)
    return report


def _backup(db_path: Path) -> Path:
    """通过 sqlite3 backup 创建 WAL 安全备份并校验存在、非空和 canonical 行数。"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    destination = db_path.with_name(f"{db_path.name}.bak.{stamp}")
    source_conn = sqlite3.connect(str(db_path))
    backup_conn = sqlite3.connect(str(destination))
    try:
        source_conn.backup(backup_conn)
    finally:
        source_conn.close()
        backup_conn.close()
    if not destination.exists() or destination.stat().st_size == 0:
        raise RuntimeError(f"数据库备份失败：{destination}")
    with sqlite3.connect(str(db_path)) as source, sqlite3.connect(str(destination)) as backup:
        source_count = source.execute("SELECT COUNT(*) FROM canonical_projects").fetchone()[0]
        backup_count = backup.execute("SELECT COUNT(*) FROM canonical_projects").fetchone()[0]
    if source_count != backup_count:
        raise RuntimeError(f"备份项目行数不一致：{source_count} != {backup_count}")
    return destination


def dry_run(db_path: Path) -> dict[str, list[str]]:
    """以只读连接报告待添加列和表，不修改数据库。"""
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        return _inspect(conn)
    finally:
        conn.close()


def apply_migration(db_path: Path) -> dict[str, list[str]]:
    """备份后单事务添加缺失对象，并验证 ID 集合与表行数不变。"""
    _backup(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        before_projects = {row[0] for row in conn.execute("SELECT id FROM canonical_projects")}
        before_events = {row[0] for row in conn.execute("SELECT id FROM project_events")}
        report = _inspect(conn)
        with conn:
            existing = _columns(conn, "project_events")
            for name, decl in _EVENT_COLUMNS:
                if name not in existing:
                    conn.execute(f"ALTER TABLE project_events ADD COLUMN {name} {decl}")
            if not _has_shadow_table(conn):
                conn.execute(_SHADOW_SQL)
            else:
                shadow_columns = _columns(conn, "project_classification_shadow")
                for name, decl in _SHADOW_COLUMNS:
                    if name not in shadow_columns:
                        conn.execute(f"ALTER TABLE project_classification_shadow ADD COLUMN {name} {decl}")
        after_projects = {row[0] for row in conn.execute("SELECT id FROM canonical_projects")}
        after_events = {row[0] for row in conn.execute("SELECT id FROM project_events")}
        if before_projects != after_projects or before_events != after_events:
            raise RuntimeError("能源影子 schema 迁移改变了 canonical/event ID 集合")
        missing = [name for name, _ in _EVENT_COLUMNS if name not in _columns(conn, "project_events")]
        missing_shadow = [
            name for name, _ in _SHADOW_COLUMNS
            if name not in _columns(conn, "project_classification_shadow")
        ]
        if missing or missing_shadow or not _has_shadow_table(conn):
            raise RuntimeError(f"能源影子 schema 迁移后仍缺对象：{missing}")
        return report
    finally:
        conn.close()


def main() -> None:
    """解析 CLI 参数，默认 dry-run，显式 --apply 才执行迁移。"""
    parser = argparse.ArgumentParser(description="添加能源事实列与影子分类表")
    parser.add_argument("--db", required=True, help="目标 bri_re_database.db")
    parser.add_argument("--apply", action="store_true", help="备份后应用迁移")
    args = parser.parse_args()
    path = Path(args.db)
    report = apply_migration(path) if args.apply else dry_run(path)
    print(report)


if __name__ == "__main__":
    main()
