"""将主库技术标签收敛到 v2.1 四标签契约，并更新 809 条分类 shadow 版本。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
import bri_enums as enums


DEFAULT_DB = PROJECT_ROOT / "s02a_duplicate_merger/data/db/bri_re_database.db"
POLICY_VERSION = "energy_taxonomy_v2.1"


def _sha256(path: Path) -> str:
    """流式计算数据库文件 SHA-256。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _backup(db_path: Path) -> Path:
    """用 SQLite backup API 创建 WAL 一致备份并验证核心表行数。"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    target = db_path.with_name(f"{db_path.name}.bak.energy-taxonomy-v21.{stamp}")
    source_conn = sqlite3.connect(str(db_path))
    target_conn = sqlite3.connect(str(target))
    try:
        source_conn.backup(target_conn)
    finally:
        source_conn.close()
        target_conn.close()
    if not target.exists() or target.stat().st_size == 0:
        raise RuntimeError("备份创建失败或为 0 字节")
    with sqlite3.connect(str(db_path)) as source, sqlite3.connect(str(target)) as copied:
        for table in ("canonical_projects", "project_events", "chinese_entities"):
            if source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] != copied.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]:
                raise RuntimeError(f"备份表 {table} 行数不一致")
    return target


def _normalize_tags(category: str, raw: str | None) -> str:
    """仅保留受控且符合父类门禁的标签，保持稳定顺序并输出 JSON。"""
    try:
        values = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        values = []
    if not isinstance(values, list):
        values = []
    normalized = [
        tag for tag in enums.valid_technology_tags()
        if tag in values and enums.technology_tag_allowed(category, tag)
    ]
    return json.dumps(normalized, ensure_ascii=False)


def _snapshot(conn: sqlite3.Connection) -> dict:
    """读取迁移门禁所需行数、ID 集合、外键与待变更统计。"""
    canonical_rows = conn.execute(
        "SELECT id, category, technology_tags_json FROM canonical_projects ORDER BY id"
    ).fetchall()
    shadow_rows = conn.execute(
        "SELECT canonical_id, proposed_category, technology_tags_json, classification_version "
        "FROM project_classification_shadow ORDER BY canonical_id"
    ).fetchall()
    canonical_changes = [
        row[0] for row in canonical_rows
        if _normalize_tags(row[1], row[2]) != (row[2] or "[]")
    ]
    shadow_tag_changes = [
        row[0] for row in shadow_rows
        if _normalize_tags(row[1] or "", row[2]) != (row[2] or "[]")
    ]
    return {
        "counts": {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("canonical_projects", "project_events", "chinese_entities")
        },
        "canonical_ids": [row[0] for row in canonical_rows],
        "event_ids": [row[0] for row in conn.execute("SELECT id FROM project_events ORDER BY id")],
        "entity_ids": [row[0] for row in conn.execute("SELECT id FROM chinese_entities ORDER BY id")],
        "shadow_rows": len(shadow_rows),
        "canonical_tag_changes": canonical_changes,
        "shadow_tag_changes": shadow_tag_changes,
        "shadow_version_changes": sum(row[3] != POLICY_VERSION for row in shadow_rows),
        "foreign_key_errors": conn.execute("PRAGMA foreign_key_check").fetchall(),
    }


def dry_run(db_path: Path) -> dict:
    """只读计算 v2.1 的精确变更范围，不触碰数据库。"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return _snapshot(conn)
    finally:
        conn.close()


def apply(db_path: Path) -> dict:
    """备份后单事务更新标签与 shadow 版本，并验证 ID、行数、外键和幂等。"""
    before_hash = _sha256(db_path)
    backup = _backup(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        before = _snapshot(conn)
        with conn:
            canonical_updates = [
                (_normalize_tags(category, tags), project_id)
                for project_id, category, tags in conn.execute(
                    "SELECT id,category,technology_tags_json FROM canonical_projects"
                )
                if _normalize_tags(category, tags) != (tags or "[]")
            ]
            shadow_updates = [
                (_normalize_tags(category or "", tags), POLICY_VERSION, canonical_id)
                for canonical_id, category, tags in conn.execute(
                    "SELECT canonical_id,proposed_category,technology_tags_json "
                    "FROM project_classification_shadow"
                )
            ]
            conn.executemany(
                "UPDATE canonical_projects SET technology_tags_json=? WHERE id=?",
                canonical_updates,
            )
            conn.executemany(
                "UPDATE project_classification_shadow SET technology_tags_json=?, "
                "classification_version=? WHERE canonical_id=?",
                shadow_updates,
            )
        after = _snapshot(conn)
        for key in ("counts", "canonical_ids", "event_ids", "entity_ids", "shadow_rows"):
            if before[key] != after[key]:
                raise RuntimeError(f"迁移门禁失败：{key} 发生变化")
        if after["foreign_key_errors"]:
            raise RuntimeError(f"迁移后外键错误：{after['foreign_key_errors'][:3]}")
        if after["canonical_tag_changes"] or after["shadow_tag_changes"] or after["shadow_version_changes"]:
            raise RuntimeError("迁移后幂等检查失败，仍存在待变更记录")
    finally:
        conn.close()
    return {
        "before_sha256": before_hash,
        "after_sha256": _sha256(db_path),
        "backup": str(backup),
        "backup_sha256": _sha256(backup),
        "changed_canonical_tags": len(before["canonical_tag_changes"]),
        "changed_shadow_tags": len(before["shadow_tag_changes"]),
        "changed_shadow_versions": before["shadow_version_changes"],
        "counts": before["counts"],
    }


def main() -> None:
    """解析 CLI 参数并打印可归档 JSON 报告。"""
    parser = argparse.ArgumentParser(description="迁移 energy taxonomy v2.1 技术标签契约")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.db.exists():
        raise SystemExit(f"数据库不存在：{args.db}")
    report = apply(args.db) if args.apply else dry_run(args.db)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
