"""对主库（bri_re_database.db）canonical_projects 原地回填确定性非能源判定：默认 dry-run，--apply 先备份再事务落库。

背景：projects.db 清扫（nonenergy_sweep）无主库回填机制——2026-07-02 治标 77 条是一次性
事务，未沉淀工具；此后护栏规则的所有演进（Rule 3 WtE 修正、民用基建词等）主库都吃不到，
基准案 SUR-XX-0001（灌溉排水修复）主库仍 excluded=0。用户原则：不重建 s02a，数据修正
原地回填主库。判定粒度：每 canonical 聚合其全部 events 的 summary/capacity_details/
capacity_mw（任一事件含发电语义即保护整个 canonical，偏保守）。
用法：uv run sweep_nonenergy_maindb [--db PATH] [--apply] [--out report.csv]
"""
import argparse
import csv
import logging
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from s01b_project_extractor.guardrails.nonenergy_check import check_nonenergy

logger = logging.getLogger(__name__)

_DEFAULT_DB = str(
    Path(__file__).resolve().parents[4]
    / "s02a_duplicate_merger" / "data" / "db" / "bri_re_database.db"
)


def sweep_maindb(db_path: str, apply: bool = False, out_csv: str | None = None) -> list[dict]:
    """扫描主库未排除 canonical（聚合 events 后判定），返回命中列表；apply=True 时先备份再事务批量置 excluded=1。"""
    # 第一阶段：只读扫描，完成后关闭连接（备份须在无连接打开时拍摄）
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT c.id, c.project_name, c.category,
                   COALESCE(GROUP_CONCAT(e.project_summary, ' '), '') AS summaries,
                   COALESCE(GROUP_CONCAT(e.capacity_details, ' '), '') AS details,
                   MAX(e.capacity_mw) AS cap_mw
            FROM canonical_projects c
            LEFT JOIN project_events e ON e.canonical_id = c.id
            WHERE c.excluded = 0
            GROUP BY c.id
            """
        ).fetchall()
    finally:
        conn.close()

    hits: list[dict] = []
    for r in rows:
        reason = check_nonenergy(
            r["project_name"], r["summaries"], r["category"], r["cap_mw"], r["details"]
        )
        if reason:
            hits.append({"id": r["id"], "project_name": r["project_name"],
                         "category": r["category"], "reason": reason})
    logger.info("扫描 %d 个未排除 canonical，命中 %d 个", len(rows), len(hits))

    if out_csv:
        with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["id", "project_name", "category", "reason"])
            w.writeheader()
            w.writerows(hits)
        logger.info("命中清单已写 %s", out_csv)

    if apply and hits:
        bak = db_path + datetime.now().strftime(".bak.%Y%m%d_%H%M%S")
        shutil.copy2(db_path, bak)
        logger.info("已备份 %s", bak)
        # 第二阶段：开新连接事务写库（主库无 exclude_source 列，理由串本身可溯源到护栏规则）
        conn2 = sqlite3.connect(db_path)
        try:
            conn2.executemany(
                "UPDATE canonical_projects SET excluded=1, excluded_reason=? WHERE id=?",
                [(h["reason"], h["id"]) for h in hits],
            )
            conn2.commit()
            logger.info("已落库 excluded=1：%d 个 canonical", len(hits))
        except sqlite3.Error as e:
            logger.error("落库失败（备份在 %s，库可能未完整更新）: %s", bak, e)
            raise
        finally:
            conn2.close()
    return hits


def main() -> None:
    """CLI 入口：解析参数并执行主库回填（默认 dry-run；日志同时写文件）。"""
    parser = argparse.ArgumentParser(description="主库 canonical_projects 非能源回填")
    parser.add_argument("--db", default=_DEFAULT_DB)
    parser.add_argument("--apply", action="store_true", help="落库（默认 dry-run）")
    parser.add_argument("--out", default=None, help="命中清单 CSV 输出路径")
    args = parser.parse_args()

    if not Path(args.db).exists():
        parser.error(f"数据库不存在: {args.db}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_csv = args.out or str(Path(args.db).parent / f"nonenergy_maindb_{ts}.csv")
    log_path = str(Path(out_csv).with_suffix(".log"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()],
    )
    hits = sweep_maindb(args.db, apply=args.apply, out_csv=out_csv)
    print(f">>> 命中 {len(hits)} 个 canonical（{'已落库' if args.apply else 'dry-run，未写库'}），清单: {out_csv}")


if __name__ == "__main__":
    main()
