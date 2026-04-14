#!/usr/bin/env python3
"""
step1_match_simple.py
将 2025-BRI RE-10to12_Intern_checked.xlsx 中的信息补充到 projects.db。

匹配方式：Excel[Source] == DB[source_link]（精确字符串匹配，trim后比较）
填充规则：只在 DB 字段为 NULL / 空字符串 / "Unknown" 时填入，不覆盖已有值，不新增行。

用法：
  uv run --with pandas --with openpyxl step1_match_simple.py
  uv run --with pandas --with openpyxl step1_match_simple.py --dry-run

涉及字段映射：
  Excel "Official Website link"                  → DB official_website
  Excel "Non-official Website Link"              → DB non_official_website
  Excel "Total Project Investment (Million USD)" → DB total_investment_usd
  Excel "Subcontract Amount (Million USD)"       → DB subcontract_amount_usd
  Excel "Investment (or Financial Close) Date"   → DB investment_date
  Excel "Financing Model"                        → DB financing_model
  Excel "Additional Information"                 → DB additional_remarks
"""

import argparse
import csv
import math
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

# ── 路径 ─────────────────────────────────────────────────────────────────────
_HERE       = Path(__file__).resolve().parent           # external-tools/data-combination/
_REPO_ROOT  = _HERE.parents[1]                          # 项目根目录
_DB_PATH    = _REPO_ROOT / "s01b_project_extractor" / "data" / "projects.db"
_EXCEL_PATH = _HERE / "2025-BRI RE-10to12_Intern_checked.xlsx"
_LOG_DIR    = _HERE / "logs"

# ── 字段映射：Excel 列名 → DB 列名 ───────────────────────────────────────────
FIELD_MAP: dict[str, str] = {
    "Official Website link"                  : "official_website",
    "Non-official Website Link"              : "non_official_website",
    "Total Project Investment (Million USD)" : "total_investment_usd",
    "Subcontract Amount (Million USD)"       : "subcontract_amount_usd",
    "Investment (or Financial Close) Date"   : "investment_date",
    "Financing Model"                        : "financing_model",
    "Additional Information"                 : "additional_remarks",
}
MATCH_KEY = "Source"   # Excel 匹配列名


# ── 工具函数 ──────────────────────────────────────────────────────────────────
def is_empty(val) -> bool:
    """判断值是否视为"空"（NULL / 空字符串 / Unknown / NaN 等）。"""
    if val is None:
        return True
    if isinstance(val, float) and math.isnan(val):
        return True
    s = str(val).strip()
    return s == "" or s.lower() in {"unknown", "nan", "none", "n/a", "na", "-", "–"}


def safe_str(val) -> str | None:
    """将值转为字符串并 strip；若为空则返回 None。"""
    if is_empty(val):
        return None
    return str(val).strip()


def backup_db(db_path: Path) -> None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = db_path.with_suffix(f".db.bak_{ts}")
    shutil.copy2(db_path, bak)
    print(f"[备份] {bak}")


# ── 主流程 ────────────────────────────────────────────────────────────────────
def main(dry_run: bool = False) -> None:
    if not _DB_PATH.exists():
        raise FileNotFoundError(f"数据库不存在: {_DB_PATH}")
    if not _EXCEL_PATH.exists():
        raise FileNotFoundError(f"Excel 文件不存在: {_EXCEL_PATH}")

    if not dry_run:
        backup_db(_DB_PATH)

    _LOG_DIR.mkdir(exist_ok=True)
    log_path = _LOG_DIR / "step1_log.csv"

    # 读取 Excel（全部按字符串读，避免日期/数值自动转换）
    df = pd.read_excel(_EXCEL_PATH, dtype=str)
    print(f"[读取] Excel 共 {len(df)} 行，{len(df.columns)} 列")

    # 确认所有需要的列存在
    missing = [c for c in ([MATCH_KEY] + list(FIELD_MAP.keys())) if c not in df.columns]
    if missing:
        raise ValueError(f"Excel 缺少以下列: {missing}")

    # 读取 DB：只取 id + source_link + 所有目标字段
    db_select_cols = ["id", "source_link"] + list(FIELD_MAP.values())
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(f"SELECT {', '.join(db_select_cols)} FROM extracted_projects")
    db_rows = cursor.fetchall()
    print(f"[读取] DB 共 {len(db_rows)} 行")

    # 构建 source_link → [row, ...] 索引（同一链接可对应多条 DB 记录）
    link_index: dict[str, list[sqlite3.Row]] = {}
    for row in db_rows:
        link = (row["source_link"] or "").strip()
        if link:
            link_index.setdefault(link, []).append(row)

    # 统计
    cnt_matched = cnt_no_match = cnt_updated = 0
    log_records: list[dict] = []

    for i, excel_row in df.iterrows():
        row_num = int(i) + 2  # Excel 行号（1=header，从 2 开始）

        source = safe_str(excel_row.get(MATCH_KEY))
        if source is None:
            log_records.append({
                "excel_row": row_num,
                "source": "",
                "db_ids": "",
                "match": "N",
                "note": "Excel Source 为空",
                "fields_updated": "",
            })
            cnt_no_match += 1
            continue

        db_matches = link_index.get(source, [])
        if not db_matches:
            log_records.append({
                "excel_row": row_num,
                "source": source[:100],
                "db_ids": "",
                "match": "N",
                "note": "未找到匹配的 source_link",
                "fields_updated": "",
            })
            cnt_no_match += 1
            continue

        # 对每个匹配的 DB 行执行更新
        all_db_ids = [r["id"] for r in db_matches]
        per_row_updates: list[str] = []

        for db_row in db_matches:
            db_id = db_row["id"]
            updates: dict[str, str] = {}

            for excel_col, db_col in FIELD_MAP.items():
                excel_val = safe_str(excel_row.get(excel_col))
                if excel_val is None:
                    continue
                db_val = db_row[db_col]
                if is_empty(db_val):
                    updates[db_col] = excel_val

            if updates:
                per_row_updates.append(f"id={db_id}:[{','.join(updates.keys())}]")
                if not dry_run:
                    set_clause = ", ".join(f"{k} = ?" for k in updates)
                    cursor.execute(
                        f"UPDATE extracted_projects SET {set_clause} WHERE id = ?",
                        list(updates.values()) + [db_id],
                    )
                    cnt_updated += 1

        cnt_matched += 1
        prefix = "[DRY-RUN] " if dry_run else ""
        log_records.append({
            "excel_row": row_num,
            "source": source[:100],
            "db_ids": "|".join(str(x) for x in all_db_ids),
            "match": "Y",
            "note": f"{prefix}匹配到 {len(db_matches)} 条 DB 记录",
            "fields_updated": " ; ".join(per_row_updates) if per_row_updates else "无需更新（目标字段均已有值）",
        })

    if not dry_run:
        conn.commit()
    conn.close()

    # 写审计日志
    fieldnames = ["excel_row", "source", "db_ids", "match", "note", "fields_updated"]
    with open(log_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(log_records)

    prefix = "[DRY-RUN] " if dry_run else ""
    print(f"\n{prefix}完成：")
    print(f"  Excel 总行数:     {len(df)}")
    print(f"  匹配成功:         {cnt_matched}")
    print(f"  未找到匹配:       {cnt_no_match}")
    print(f"  DB 行已更新:      {cnt_updated if not dry_run else '(dry-run，未实际写入)'}")
    print(f"  审计日志:         {log_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Step 1: 按 source_link 精确匹配并补充字段")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="不实际修改数据库，只打印将要执行的操作"
    )
    args = parser.parse_args()
    main(dry_run=args.dry_run)
