"""tag_review_queue_severity.py — 一次性：给 review.db 的 review_queue 加 severity 列 + 回填 current_value。

severity 规则单一可信源 = scripts/oneoff/output/tag_severity.py 的 severity_of（与 confidence 正交）。
按 reason_type 分流：
  low_confidence_field → severity_of(行)（高/中/低，依字段+方向）
  exclude_suggested    → 高（影响是否收录）
  unnamed_entity       → NULL（P2 待补名流，不属"字段正确性"severity 体系，单列）
current_value 在 review_queue 里本为空 → join projects.db 的 extracted_projects[field] 回填，使队列自洽
（severity_of 算 capacity/project_name 时需要 current 值）。

只写 review.db（gitignored 元数据表），projects.db 只读。幂等可复跑。

用法（默认指向 worktree 两库）：
    uv --project s01b_project_extractor run python scripts/oneoff/tag_review_queue_severity.py --dry-run
    uv --project s01b_project_extractor run python scripts/oneoff/tag_review_queue_severity.py
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from collections import Counter
from pathlib import Path

_root = next(p for p in Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
# severity 规则单一可信源
_oneoff_output = Path(__file__).resolve().parent / "output"
if str(_oneoff_output) not in sys.path:
    sys.path.insert(0, str(_oneoff_output))
from tag_severity import severity_of  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_REVIEW_DB = _root / "s01b_project_extractor" / "data" / "review.db"
DEFAULT_PROJECTS_DB = _root / "s01b_project_extractor" / "data" / "projects.db"


def _load_extracted(projects_db: str) -> tuple[dict, set]:
    """读 projects.db 的 extracted_projects 为 {id: Row} + 列名集合（只读）。"""
    conn = sqlite3.connect(str(projects_db))
    conn.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(extracted_projects)")}
        rows = {r["id"]: r for r in conn.execute("SELECT * FROM extracted_projects")}
    finally:
        conn.close()
    return rows, cols


def _current_value(qrow: sqlite3.Row, extracted: dict, cols: set) -> str:
    """按 canonical_id+field 从 extracted_projects 取该字段现值，回填为字符串（无则空串）。"""
    cid, field = qrow["canonical_id"], qrow["field"]
    pr = extracted.get(int(cid)) if cid and str(cid).isdigit() else None
    if pr is None or not field or field not in cols or pr[field] is None:
        return ""
    return str(pr[field])


def _severity_for(qrow: sqlite3.Row, current_value: str):
    """按 reason_type 分流算 severity；low_confidence_field 委托 severity_of。"""
    rt = qrow["reason_type"]
    if rt == "exclude_suggested":
        return "高"
    if rt == "unnamed_entity":
        return None  # 待补名流，不属字段正确性 severity 体系
    return severity_of({
        "field": qrow["field"],
        "current_value": current_value,
        "suggested_value": qrow["suggested_value"],
    })


def backfill_and_tag(review_db: str, projects_db: str, *, dry_run: bool = False) -> Counter:
    """给 review_queue 加 severity 列 + 回填 current_value；返回 (reason_type, severity) 计数。"""
    extracted, cols = _load_extracted(projects_db)
    conn = sqlite3.connect(str(review_db))
    try:
        try:
            conn.execute("ALTER TABLE review_queue ADD COLUMN severity TEXT")
        except sqlite3.OperationalError:
            pass  # 列已存在（已知无害边界，按 CLAUDE.md 规则1 沉默）
        conn.row_factory = sqlite3.Row
        qrows = list(conn.execute("SELECT * FROM review_queue"))

        updates: list[tuple] = []
        counts: Counter = Counter()
        for q in qrows:
            cur = _current_value(q, extracted, cols)
            sev = _severity_for(q, cur)
            updates.append((cur, sev, q["id"]))
            counts[(q["reason_type"], sev)] += 1

        if not dry_run:
            with conn:  # 单事务 executemany（CLAUDE.md 规则3）
                conn.executemany(
                    "UPDATE review_queue SET current_value=?, severity=? WHERE id=?",
                    updates,
                )
        logger.info("review_queue 打标完成 dry_run=%s 共 %d 行", dry_run, len(qrows))
        return counts
    finally:
        conn.close()


def main() -> None:
    """CLI：给 worktree review.db 的 review_queue 打 severity + 回填 current_value，打印分布。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])
    parser = argparse.ArgumentParser(description="review_queue severity 打标 + current_value 回填（一次性）")
    parser.add_argument("--review-db", default=str(DEFAULT_REVIEW_DB))
    parser.add_argument("--projects-db", default=str(DEFAULT_PROJECTS_DB))
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()

    counts = backfill_and_tag(args.review_db, args.projects_db, dry_run=args.dry_run)

    print(f"\n=== review_queue severity 分布 (dry_run={args.dry_run}) ===")
    # 按 reason_type 汇总
    by_reason: dict[str, Counter] = {}
    for (rt, sev), n in counts.items():
        by_reason.setdefault(rt, Counter())[sev] += n
    def _sevkey(s) -> int:
        return ("高", "中", "低").index(s) if s in ("高", "中", "低") else 9
    for rt in ("low_confidence_field", "exclude_suggested", "unnamed_entity"):
        if rt in by_reason:
            dist = {k: v for k, v in sorted(by_reason[rt].items(), key=lambda x: _sevkey(x[0]))}
            print(f"  {rt}: {dist}")
    total_scope = sum(n for (rt, sev), n in counts.items() if sev in ("高", "中"))
    print(f"  → 最后一遍复核范围(高+中) = {total_scope} 条")


if __name__ == "__main__":
    main()
