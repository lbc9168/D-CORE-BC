"""en_backfill.py — 用修复后的 company_name_cache.json 确定性重算英文名列（主库 + projects.db）。

口径（用户拍板「缩写为主」）：en_abbr 优先、否则 en；缓存值含中文视为 miss。
hit 且与现值不同 → update；miss/partial_miss → 只进报告（=分层核实 campaign 的候选清单）；
ascii 名与 Unknown → 跳过。dry-run 默认；--apply 每库先 .bak 再事务 executemany。

用法：uv run backfill_entity_en [--db 主库] [--projects-db projects.db] [--cache PATH] [--apply] [--out CSV]
"""
import argparse
import csv
import json
import logging
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

if sys.stdout.encoding and sys.stdout.encoding.lower() in ("gbk", "cp936", "cp950"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from s02b_entity_normalizer.developer_normalizer import DEV_SEP_RE, split_developer
from s02b_entity_normalizer.entity_normalizer import DEFAULT_DB

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CACHE = str(_ROOT / "supporting_docs" / "company_name_cache.json")
_CJK_RE = re.compile(r"[一-鿿]")

TARGETS_MAIN = [
    ("chinese_entities", "implementing_entity", "implementing_entity_en"),
    ("chinese_entities", "subsidiary_name", "subsidiary_name_en"),
    ("project_events", "project_developer", "project_developer_en"),
]
TARGETS_PROJECTS = [
    ("extracted_projects", "implementing_entity", "implementing_entity_en"),
    ("extracted_projects", "subsidiary_name", "subsidiary_name_en"),
    ("extracted_projects", "project_developer", "project_developer_en"),
]


def _cache_en(cache: Dict[str, dict], cn: str) -> str:
    """en_abbr 优先、否则 en（缩写为主口径）；条目缺失/值含中文返回空串。"""
    v = cache.get(cn)
    if not isinstance(v, dict):
        return ""
    en = str(v.get("en_abbr") or v.get("en") or "").strip()
    return "" if (not en or _CJK_RE.search(en)) else en


def resolve_en(cache: Dict[str, dict], cn) -> Tuple[Optional[str], str]:
    """中文名 → (新英文名, status)。多公司串须逐段全命中才拼替换（'; ' 口径与 s01b 拆串一致）。"""
    s = str(cn or "").strip()
    if not s or s.lower() == "unknown":
        return None, "skip"
    if not _CJK_RE.search(s):
        return None, "ascii_skip"
    parts = split_developer(s)
    if len(parts) <= 1:
        en = _cache_en(cache, s)
        return (en, "hit") if en else (None, "miss")
    ens = [_cache_en(cache, p) for p in parts]
    if all(ens):
        return "; ".join(ens), "hit_multi"
    return None, "partial_miss"


def plan_db(conn, targets: List[Tuple[str, str, str]], cache: Dict[str, dict]) -> List[dict]:
    """逐 (表, 中文列, 英文列) 扫描 distinct 中文值 → update / report_only 计划行。"""
    plan: List[dict] = []
    for table, cn_col, en_col in targets:
        rows = conn.execute(
            f"SELECT {cn_col}, COUNT(*) as n, MIN(COALESCE({en_col}, '')) as sample_en "
            f"FROM {table} WHERE {cn_col} IS NOT NULL AND TRIM({cn_col}) <> '' "
            f"GROUP BY {cn_col}"
        ).fetchall()
        for cn, n, sample_en in rows:
            new_en, status = resolve_en(cache, cn)
            if status in ("hit", "hit_multi"):
                # 该 cn 名下任一行英文与 new_en 不同才需要 update
                diff = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {cn_col} = ? "
                    f"AND COALESCE({en_col}, '') <> ?",
                    (cn, new_en),
                ).fetchone()[0]
                if diff:
                    plan.append({"table": table, "cn_col": cn_col, "cn": cn, "rows": diff,
                                 "current_en": sample_en, "new_en": new_en,
                                 "status": status, "action": "update"})
            elif status in ("miss", "partial_miss"):
                plan.append({"table": table, "cn_col": cn_col, "cn": cn, "rows": n,
                             "current_en": sample_en, "new_en": "",
                             "status": status, "action": "report_only"})
    return plan


def apply_db(db_path: str, plan_rows: List[dict]) -> int:
    """先备份 .bak，再按 (表, 列) 分组事务 executemany 更新 action=update 的行；返回总行数。"""
    updates = [p for p in plan_rows if p["action"] == "update"]
    if not updates:
        return 0
    bak = db_path + datetime.now().strftime(".bak.%Y%m%d_%H%M%S")
    shutil.copy2(db_path, bak)
    logger.info("库已备份 %s", bak)
    conn = sqlite3.connect(db_path)
    total = 0
    try:
        groups: Dict[Tuple[str, str], List[dict]] = {}
        for p in updates:
            en_col = next(e for t, c, e in TARGETS_MAIN + TARGETS_PROJECTS
                          if t == p["table"] and c == p["cn_col"])
            groups.setdefault((p["table"], p["cn_col"], en_col), []).append(p)
        for (table, cn_col, en_col), items in groups.items():
            cur = conn.executemany(
                f"UPDATE {table} SET {en_col} = ? WHERE {cn_col} = ?",
                [(p["new_en"], p["cn"]) for p in items],
            )
            total += cur.rowcount
        conn.commit()
        logger.info("英文名回填落库 %d 行（%d 个值级更新）", total, len(updates))
        return total
    except sqlite3.Error as e:
        logger.error("回填失败（备份在 %s）: %s", bak, e)
        raise
    finally:
        conn.close()


def main() -> None:
    """CLI：默认 dry-run 产计划 CSV（含 miss=campaign 候选）；--apply 时逐库备份后落库。"""
    parser = argparse.ArgumentParser(description="公司英文名确定性回填（缓存 en_abbr 优先口径）")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="主库路径")
    parser.add_argument("--projects-db", default=None, help="s01b projects.db 路径（可选）")
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    for path, label in [(args.db, "主库"), (args.cache, "缓存")]:
        if not Path(path).exists():
            parser.error(f"{label}不存在: {path}")
    if args.projects_db and not Path(args.projects_db).exists():
        parser.error(f"projects.db 不存在: {args.projects_db}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_csv = args.out or str(Path(args.db).parent / f"en_backfill_{ts}.csv")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[logging.FileHandler(str(Path(out_csv).with_suffix(".log")), encoding="utf-8"),
                  logging.StreamHandler()],
    )

    raw = json.loads(Path(args.cache).read_text(encoding="utf-8"))
    cache = {k: v for k, v in raw.items() if not k.startswith("_")}

    jobs = [("main", args.db, TARGETS_MAIN)]
    if args.projects_db:
        jobs.append(("projects", args.projects_db, TARGETS_PROJECTS))

    all_plans: List[Tuple[str, str, List[dict]]] = []
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["db", "table", "cn_col", "cn", "rows", "current_en", "new_en", "status", "action"])
        for label, db_path, targets in jobs:
            conn = sqlite3.connect(db_path)
            try:
                plan = plan_db(conn, targets, cache)
            finally:
                conn.close()
            all_plans.append((label, db_path, plan))
            for p in plan:
                w.writerow([label, p["table"], p["cn_col"], p["cn"], p["rows"],
                            p["current_en"], p["new_en"], p["status"], p["action"]])
            n_up = sum(1 for p in plan if p["action"] == "update")
            n_miss = len(plan) - n_up
            print(f">>> [{label}] update 候选 {n_up} 值 / miss 报告 {n_miss} 值")
    print(f">>> 计划明细: {out_csv}")

    if args.apply:
        for label, db_path, plan in all_plans:
            n = apply_db(db_path, plan)
            print(f">>> [{label}] 已回填 {n} 行")
    else:
        print(">>> dry-run 完成（未写库）。复看 CSV 后加 --apply 执行。")


if __name__ == "__main__":
    main()
