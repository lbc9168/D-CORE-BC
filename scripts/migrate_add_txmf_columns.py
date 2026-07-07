"""scripts/migrate_add_txmf_columns.py —— 给 project_events 补 TX/MF 稀疏专用列（阶段3 任务3.1）。

新增 5 列（均可空，供 3.2 回填器写入）：
    voltage_kv REAL                    -- TX：电压等级（千伏）
    line_length_km REAL                -- TX：线路长度（公里）
    transmission_capacity_mw REAL      -- TX：输送容量（MW）
    production_capacity_value REAL     -- MF：产能数值
    production_capacity_unit TEXT      -- MF：产能单位（受控枚举，见 bri_enums.mf_production_units）

安全范式抄 codex_review/apply_actions.py：
    dry-run（默认）：mode=ro 只读连接，仅报告每列已存在/将添加，不接触真库文件。
    --apply：WAL 安全备份（sqlite3.backup API）→ 三重断言 → busy_timeout=30s
             → 单事务 ALTER → 提交后自检列齐全 + 行数不变。
幂等：已存在的列跳过（ALTER TABLE ADD COLUMN 报 duplicate column 时静默，按 CLAUDE.md 规则1
      属已知无害边界）；重跑应报告 5 列已存在、0 变更。

用法：
    uv run python scripts/migrate_add_txmf_columns.py --db <path>            # dry-run
    uv run python scripts/migrate_add_txmf_columns.py --db <path> --apply    # 落库
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, FilePath

logger = logging.getLogger("migrate_add_txmf_columns")

# 真库默认绝对路径（环境事实，见任务简报）
_DEFAULT_DB = (
    Path(__file__).resolve().parents[1]
    / "s02a_duplicate_merger" / "data" / "db" / "bri_re_database.db"
)

_TABLE = "project_events"
_NEW_COLUMNS: list[tuple[str, str]] = [
    ("voltage_kv", "REAL"),
    ("line_length_km", "REAL"),
    ("transmission_capacity_mw", "REAL"),
    ("production_capacity_value", "REAL"),
    ("production_capacity_unit", "TEXT"),
]


class MigrateParams(BaseModel):
    """入口参数校验（规则4）：db 路径必须存在。"""

    db_path: FilePath
    apply: bool = False


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """返回某表当前已有列名集合。"""
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _backup_db(db_path: Path) -> Path:
    """WAL 安全备份（sqlite3 原生 backup API，非 shutil）；三重断言防 0 字节/行数不一致事故。"""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dst = db_path.with_name(f"{db_path.name}.bak.{timestamp}")

    src_conn = sqlite3.connect(str(db_path))
    dst_conn = sqlite3.connect(str(dst))
    try:
        src_conn.backup(dst_conn)
    finally:
        src_conn.close()
        dst_conn.close()

    if not dst.exists():
        raise RuntimeError(f"备份文件不存在（断言1 失败）：{dst}")
    if dst.stat().st_size == 0:
        raise RuntimeError(f"备份文件为 0 字节（断言2 失败）：{dst}")
    with sqlite3.connect(str(db_path)) as s, sqlite3.connect(str(dst)) as d:
        (src_count,) = s.execute("SELECT COUNT(*) FROM canonical_projects").fetchone()
        (dst_count,) = d.execute("SELECT COUNT(*) FROM canonical_projects").fetchone()
    if src_count != dst_count:
        raise RuntimeError(
            f"备份 canonical_projects 行数不一致（断言3 失败）：源={src_count}，备份={dst_count}"
        )

    logger.info("备份完成：%s（canonical_projects=%d 行）", dst, src_count)
    return dst


def dry_run(db_path: Path) -> dict[str, list[str]]:
    """只读连接检查 5 列现状，返回 {"existing": [...], "to_add": [...]}。"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        existing = _existing_columns(conn, _TABLE)
    finally:
        conn.close()

    report = {"existing": [], "to_add": []}
    for col, _decl in _NEW_COLUMNS:
        if col in existing:
            report["existing"].append(col)
        else:
            report["to_add"].append(col)

    logger.info(
        "DRY-RUN：已存在 %d 列 %s；将添加 %d 列 %s",
        len(report["existing"]), report["existing"],
        len(report["to_add"]), report["to_add"],
    )
    return report


def apply_migration(db_path: Path) -> dict[str, list[str]]:
    """WAL 备份 → busy_timeout → 单事务 ALTER → 提交后自检；返回同 dry_run 形状的报告。"""
    _backup_db(db_path)

    before_counts = {}
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA busy_timeout = 30000")  # 并行会话可能在写这个库
        for t in ("canonical_projects", _TABLE):
            before_counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]

        existing = _existing_columns(conn, _TABLE)
        report: dict[str, list[str]] = {"existing": [], "to_add": []}

        with conn:  # 单事务（规则3）
            for col, decl in _NEW_COLUMNS:
                if col in existing:
                    report["existing"].append(col)
                    continue
                try:
                    conn.execute(f"ALTER TABLE {_TABLE} ADD COLUMN {col} {decl}")
                    report["to_add"].append(col)
                except sqlite3.OperationalError as e:
                    # 已知无害边界：并发场景下列可能已被其他连接加上（规则1）
                    logger.error("ALTER TABLE 添加列 %s 失败（视为已存在）：%s", col, e)
                    report["existing"].append(col)

        # 提交后自检：5 列齐全 + 行数与迁移前一致
        after_cols = _existing_columns(conn, _TABLE)
        missing = [c for c, _ in _NEW_COLUMNS if c not in after_cols]
        if missing:
            raise RuntimeError(f"自检失败：ALTER 后仍缺列 {missing}")

        for t in ("canonical_projects", _TABLE):
            after = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            if after != before_counts[t]:
                raise RuntimeError(
                    f"自检失败：{t} 行数变化 {before_counts[t]} -> {after}（应不变）"
                )
    finally:
        conn.close()

    logger.info(
        "APPLY 完成：新增 %d 列 %s；已存在 %d 列 %s；行数不变 %s",
        len(report["to_add"]), report["to_add"],
        len(report["existing"]), report["existing"],
        before_counts,
    )
    return report


def _setup_logging(db_path: Path) -> None:
    """初始化 logging：console + 落文件（CLAUDE.md 规则2）。"""
    log_path = db_path.resolve().parent / "migrate_add_txmf_columns.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()],
        force=True,
    )
    logger.info("日志文件：%s", log_path)


def main() -> None:
    """CLI 主入口：解析参数、跑 dry-run 或 apply。"""
    parser = argparse.ArgumentParser(description="给 project_events 补 TX/MF 稀疏专用列")
    parser.add_argument("--db", default=str(_DEFAULT_DB), help="目标数据库路径")
    parser.add_argument("--apply", action="store_true", help="落库（默认 dry-run）")
    args = parser.parse_args()

    try:
        cfg = MigrateParams(db_path=args.db, apply=args.apply)
    except ValueError as e:
        logger.error("入口参数校验失败：%s", e)
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    _setup_logging(Path(cfg.db_path))
    logger.info("=== migrate_add_txmf_columns %s | db=%s ===",
                "APPLY" if cfg.apply else "DRY-RUN", cfg.db_path)

    if cfg.apply:
        apply_migration(Path(cfg.db_path))
    else:
        dry_run(Path(cfg.db_path))


if __name__ == "__main__":
    main()
