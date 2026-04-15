"""step1_xlsx_to_s01b.py
将 external-link-adder/projects_by_source_filled.xlsx 中的
official_website / non_official_website 回写到 s01b extracted_projects。

匹配键：xlsx.source_link == s01b.extracted_projects.source_link（精确相等）
覆盖策略：以 xlsx 值为准（xlsx 是 AI 填写的最终版本）

运行方式（项目根目录）：
    python scripts/step1_xlsx_to_s01b.py
依赖：pandas, openpyxl（s01b venv 或系统环境均可）
"""

import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

XLSX_PATH = ROOT / "external-tools" / "external-link-adder" / "projects_by_source_filled.xlsx"
S01B_DB   = ROOT / "s01b_project_extractor" / "data" / "projects.db"


def main() -> None:
    print(f"读取 xlsx: {XLSX_PATH}")
    df = pd.read_excel(XLSX_PATH, dtype=str)
    df = df.fillna("")

    # 找到匹配列
    if "source_link" not in df.columns:
        raise ValueError(f"xlsx 中未找到 source_link 列，实际列名：{df.columns.tolist()}")
    if "official_website" not in df.columns or "non_official_website" not in df.columns:
        raise ValueError("xlsx 中未找到 official_website / non_official_website 列")

    print(f"xlsx 共 {len(df)} 行")

    # 筛选有 website 数据且 source_link 非空的行
    has_data = df[
        (df["source_link"].str.startswith("http")) &
        (
            (df["official_website"].str.startswith("http", na=False)) |
            (df["non_official_website"].str.startswith("http", na=False))
        )
    ].copy()
    print(f"有效 website 行（source_link + 至少一个 http 链接）：{len(has_data)}")

    conn = sqlite3.connect(S01B_DB)

    updated = 0
    not_found = 0

    for _, row in has_data.iterrows():
        src_link = row["source_link"].strip()
        official   = row["official_website"].strip()
        non_official = row["non_official_website"].strip()

        # 只保留有效 URL
        official     = official     if official.startswith("http")     else ""
        non_official = non_official if non_official.startswith("http") else ""

        result = conn.execute(
            "SELECT id FROM extracted_projects WHERE source_link = ?",
            (src_link,),
        ).fetchone()

        if result is None:
            not_found += 1
            continue

        conn.execute(
            """UPDATE extracted_projects
               SET official_website = ?, non_official_website = ?
               WHERE source_link = ?""",
            (official, non_official, src_link),
        )
        updated += 1

    conn.commit()
    conn.close()

    print(f"\n[完成] 已更新 s01b 记录：{updated} 行")
    print(f"       xlsx 中无对应 s01b 记录：{not_found} 行")


if __name__ == "__main__":
    main()
