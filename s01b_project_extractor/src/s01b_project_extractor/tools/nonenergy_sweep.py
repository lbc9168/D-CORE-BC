"""对 projects.db 存量行回溯执行确定性非能源兜底：默认 dry-run 报告，--apply 先备份再事务落库。

用途：治标只改了主库（bri_re_database.db），projects.db 里误分类行仍 excluded=0，
未来任何 s02a 重建会把它们带回 canonical（s02a 按组传播 excluded）。本工具让 projects.db
与主库口径一致。用法：uv run sweep_nonenergy [--db PATH] [--apply] [--out report.csv]
"""
import argparse
import csv
import logging
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from s01b_project_extractor.guardrails.nonenergy_check import (
    DETERMINISTIC_GUARD_SOURCE,
    check_nonenergy,
)

logger = logging.getLogger(__name__)

_DEFAULT_DB = str(Path(__file__).resolve().parents[3] / "data" / "projects.db")


def sweep(db_path: str, apply: bool = False, out_csv: str | None = None) -> list[dict]:
    """扫描 extracted_projects 未排除行，返回命中列表；apply=True 时先备份再事务批量置 excluded=1。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, project_name, project_summary, category, capacity_mw, capacity_details "
        "FROM extracted_projects WHERE COALESCE(excluded, 0) = 0"
    ).fetchall()
    hits: list[dict] = []
    for r in rows:
        reason = check_nonenergy(
            r["project_name"], r["project_summary"], r["category"],
            r["capacity_mw"], r["capacity_details"],
        )
        if reason:
            hits.append({"id": r["id"], "project_name": r["project_name"],
                         "category": r["category"], "reason": reason})
    logger.info("扫描 %d 行未排除记录，命中 %d 行", len(rows), len(hits))

    if out_csv:
        with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["id", "project_name", "category", "reason"])
            w.writeheader()
            w.writerows(hits)
        logger.info("dry-run 报告已写 %s", out_csv)

    if apply and hits:
        bak = db_path + datetime.now().strftime(".bak.%Y%m%d_%H%M%S")
        shutil.copy2(db_path, bak)
        logger.info("已备份 %s", bak)
        conn.executemany(
            "UPDATE extracted_projects SET excluded=1, exclude_reason=?, exclude_source=? WHERE id=?",
            [(h["reason"], DETERMINISTIC_GUARD_SOURCE, h["id"]) for h in hits],
        )
        conn.commit()
        logger.info("已落库 excluded=1：%d 行（source=%s）", len(hits), DETERMINISTIC_GUARD_SOURCE)
    conn.close()
    return hits


def main() -> None:
    """CLI 入口：解析参数并执行清扫（默认 dry-run；日志同时写文件）。"""
    parser = argparse.ArgumentParser(description="projects.db 非能源回溯清扫")
    parser.add_argument("--db", default=_DEFAULT_DB)
    parser.add_argument("--apply", action="store_true", help="落库（默认 dry-run）")
    parser.add_argument("--out", default=None, help="命中清单 CSV 输出路径")
    args = parser.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_csv = args.out or str(Path(args.db).parent / f"nonenergy_sweep_{ts}.csv")
    log_path = str(Path(out_csv).with_suffix(".log"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()],
    )
    hits = sweep(args.db, apply=args.apply, out_csv=out_csv)
    print(f">>> 命中 {len(hits)} 行（{'已落库' if args.apply else 'dry-run，未写库'}），清单: {out_csv}")


if __name__ == "__main__":
    main()
