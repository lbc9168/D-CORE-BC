#!/usr/bin/env python3
"""
export_by_source.py
从 projects.db 按 source_link 去重，输出 Excel。

去重规则：
  - 以 source_link 为 key 分组
  - 每列取"第一个有效值"（非 NULL / 非空 / 非 Unknown 等占位符）
  - 若同一 source_link 下某列多行值不同，保留最先出现的有效值

输出列：
  project_name, country, location, category, capacity_mw,
  implementing_entity, total_investment_usd, financing_model,
  source_link, official_website, non_official_website

用法：
  python export_by_source.py
  python export_by_source.py --output my_output.xlsx
"""

import argparse
import math
import sqlite3
from pathlib import Path

import pandas as pd

# ── 路径 ─────────────────────────────────────────────────────────────────────
_HERE    = Path(__file__).resolve().parent
_DB_PATH = _HERE.parents[1] / "s01b_project_extractor" / "data" / "projects.db"

# ── 输出列（顺序即 Excel 列序）────────────────────────────────────────────────
OUTPUT_COLS = [
    "project_name",
    "country",
    "location",
    "category",
    "capacity_mw",
    "implementing_entity",
    "total_investment_usd",
    "financing_model",
    "source_link",
    "official_website",
    "non_official_website",
]

# ── 空值占位符集合（不区分大小写）─────────────────────────────────────────────
_EMPTY_TOKENS = {"", "null", "none", "n/a", "na", "unknown", "-", "–", "nan"}


def is_empty(val) -> bool:
    """判断值是否视为"无记录"。"""
    if val is None:
        return True
    if isinstance(val, float) and math.isnan(val):
        return True
    return str(val).strip().lower() in _EMPTY_TOKENS


def first_valid(series: pd.Series):
    """返回 Series 中第一个有效值；全为空则返回 None。"""
    for v in series:
        if not is_empty(v):
            return v
    return None


def main(output_path: Path) -> None:
    if not _DB_PATH.exists():
        raise FileNotFoundError(f"数据库不存在: {_DB_PATH}")

    # 读取所需列（+ id 用于保持原始行序）
    fetch_cols = list(dict.fromkeys(["id"] + OUTPUT_COLS))  # 去重保序
    conn = sqlite3.connect(_DB_PATH)
    df = pd.read_sql_query(
        f"SELECT {', '.join(fetch_cols)} FROM extracted_projects ORDER BY id",
        conn,
    )
    conn.close()
    print(f"[读取] {len(df)} 行，来自 {_DB_PATH.name}")

    # 将空占位符统一为 None，方便后续处理
    def normalize(val):
        if is_empty(val):
            return None
        return val

    for col in OUTPUT_COLS:
        df[col] = df[col].map(normalize)

    # 按 source_link 分组，每列取第一个有效值
    # source_link 为 None 的行单独处理（各自独立，不合并）
    df_has_link  = df[df["source_link"].notna()].copy()
    df_no_link   = df[df["source_link"].isna()].copy()

    # 有 source_link 的行：按 source_link 聚合
    agg_dict = {col: first_valid for col in OUTPUT_COLS}
    grouped = (
        df_has_link
        .groupby("source_link", sort=False)
        .agg(agg_dict)
        .reset_index(drop=True)
    )

    # 无 source_link 的行：直接保留（各行独立）
    no_link_out = df_no_link[OUTPUT_COLS].reset_index(drop=True)

    result = pd.concat([grouped, no_link_out], ignore_index=True)

    # 写 Excel
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        result.to_excel(writer, index=False, sheet_name="projects_by_source")

        # 自动调整列宽（上限 60）
        ws = writer.sheets["projects_by_source"]
        for col_cells in ws.columns:
            max_len = max(
                (len(str(c.value)) if c.value is not None else 0)
                for c in col_cells
            )
            ws.column_dimensions[col_cells[0].column_letter].width = min(max_len + 2, 60)

    print(f"[输出] {len(result)} 行 → {output_path}")
    print(f"  其中：有 source_link 去重后 {len(grouped)} 行，无 source_link {len(no_link_out)} 行")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="按 source_link 去重导出 Excel")
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=_HERE / "projects_by_source.xlsx",
        help="输出 Excel 路径（默认：同目录下 projects_by_source.xlsx）",
    )
    args = parser.parse_args()
    main(args.output)
