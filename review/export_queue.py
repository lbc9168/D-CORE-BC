"""export_queue：把 review_queue 的 pending 项导出为 CSV（join projects.db 补项目名/类别/原值），供人工三角；只读、不改任何库。"""
from __future__ import annotations

import argparse
import csv
import logging
import pathlib
import sqlite3
import sys

# ── 仓库根上溯（含 bri_enums.py）────────────────────────────────────────────
_root = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from review import queue  # noqa: E402

logger = logging.getLogger(__name__)

# CSV 列顺序（人工三角用）
_FIELDS = [
    "reason_type", "canonical_id", "project_name", "category", "field",
    "current_value", "suggested_value", "critic_confidence", "evidence",
    "source_link", "created_at", "state",
]
# 排序：status_challenge（该不该收录，最高价值）在前，再 low_confidence_field，再其它
_REASON_ORDER = {"status_challenge": 0, "low_confidence_field": 1, "unnamed_entity": 2}


def export_pending(review_db, projects_db, out_csv) -> int:
    """读 review_db 的 pending 项，join projects_db 补 project_name/category/字段原值，写 CSV（utf-8-sig 供 Excel）；返回导出条数。"""
    rows = queue.pending(review_db)

    # 读 projects.db 建查找表（只读）
    pconn = sqlite3.connect(str(projects_db))
    pconn.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in pconn.execute("PRAGMA table_info(extracted_projects)")}
        prows = {r["id"]: r for r in pconn.execute("SELECT * FROM extracted_projects")}
    except sqlite3.Error as e:
        logger.error("读取 projects_db 失败 path=%s: %s", projects_db, e)
        pconn.close()
        raise
    pconn.close()

    def _enrich(r: dict) -> dict:
        """把一条 queue 行补上项目名/类别/该字段在库中的原值。"""
        cid = r.get("canonical_id")
        pr = prows.get(int(cid)) if cid and str(cid).isdigit() else None
        project_name = category = current_value = ""
        if pr is not None:
            project_name = pr["project_name"] if "project_name" in cols else ""
            category = pr["category"] if "category" in cols else ""
            fld = r.get("field")
            if fld and fld in cols:
                current_value = pr[fld]
        return {
            "reason_type": r.get("reason_type"), "canonical_id": cid,
            "project_name": project_name, "category": category, "field": r.get("field"),
            "current_value": current_value, "suggested_value": r.get("suggested_value"),
            "critic_confidence": r.get("critic_confidence"), "evidence": r.get("evidence"),
            "source_link": r.get("source_link"), "created_at": r.get("created_at"),
            "state": r.get("state"),
        }

    enriched = [_enrich(r) for r in rows]

    def _sort_key(d: dict):
        cid = d.get("canonical_id")
        cid_num = int(cid) if cid and str(cid).isdigit() else 1 << 30
        return (_REASON_ORDER.get(d.get("reason_type"), 9), cid_num)

    enriched.sort(key=_sort_key)

    # utf-8-sig：带 BOM，避免 Excel 打开中文乱码
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDS)
        writer.writeheader()
        for d in enriched:
            writer.writerow(d)
    logger.info("导出 %d 条 pending 复核项 → %s", len(enriched), out_csv)
    return len(enriched)


def main() -> None:
    """CLI：把 review_queue pending 导出为 CSV。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="review_queue pending → CSV")
    parser.add_argument("--review-db", default=str(_root / "s01b_project_extractor" / "data" / "review.db"))
    parser.add_argument("--projects-db", default=str(_root / "s01b_project_extractor" / "data" / "projects.db"))
    parser.add_argument("--out", required=True, help="输出 CSV 路径")
    args = parser.parse_args()
    n = export_pending(args.review_db, args.projects_db, args.out)
    print(f"已导出 {n} 条 pending 复核项 → {args.out}")


if __name__ == "__main__":
    main()
