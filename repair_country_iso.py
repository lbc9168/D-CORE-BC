#!/usr/bin/env python3
"""
一次性修复脚本：补全所有 BRI_RE_Final_Database Excel 文件中缺失的
Country ISO / BRI country / LDCs 字段，将 Archived 文件移回 processed/，
并删除旧数据库。完成后运行 s02a 即可重建干净的数据库。

用法：
    cd e:/Projects/BRI_RE_Database_v2
    python repair_country_iso.py
"""
import json
import shutil
from pathlib import Path

import pandas as pd

ROOT         = Path(__file__).resolve().parent
S01_ROOT     = ROOT / "s01_data_collector_filter"
COUNTRY_DB   = S01_ROOT / "WorldCountryList.json"
PROCESSED    = S01_ROOT / "data" / "processed"
ARCHIVED     = PROCESSED / "Archived"
DB_PATH      = ROOT / "s02a_duplicate_merger" / "data" / "db" / "bri_re_database.db"


# ── 国家查找（与 project_extractor_tool 逻辑一致）─────────────────────────────

def _load_db() -> dict:
    with open(COUNTRY_DB, encoding="utf-8") as f:
        countries = json.load(f)
    db: dict = {}
    for c in countries:
        record = {
            "iso":     c["iso"],
            "bri":     c["bri"],
            "ldc":     c["ldc"],
            "name_en": c.get("name_en", ""),
        }
        db[c["name_cn"]] = record
        for alias in c.get("aliases_cn", []):
            db[alias] = record
    return db, len(countries)


def _lookup(country_name, db: dict) -> dict | None:
    """返回 {iso, bri, ldc}，找不到返回 None。"""
    if not country_name or pd.isna(country_name):
        return None
    name = str(country_name).strip()
    if not name or name in ("nan", "NA", "N/A"):
        return None
    # 1. 精确中文匹配
    if name in db:
        return db[name]
    # 2. 中文子串互含
    for k, v in db.items():
        if k in name or name in k:
            return v
    # 3. 英文名匹配
    for k, v in db.items():
        en = v.get("name_en", "")
        if en and (en.lower() in name.lower() or name.lower() in en.lower()):
            return v
    return None


# ── Excel 修复 ─────────────────────────────────────────────────────────────────

def _fix_excel(path: Path, db: dict) -> tuple[int, int]:
    """修复单个 Excel 文件，就地保存。返回 (总行数, 修复行数)。"""
    df = pd.read_excel(path)
    # 确保这三列是 object 类型，防止全 NaN 列被推断为 float64 无法写入字符串
    for col in ("Country ISO", "BRI country", "LDCs"):
        if col in df.columns:
            df[col] = df[col].astype(object)
    fixed = 0
    for idx, row in df.iterrows():
        iso_val = row.get("Country ISO", None)
        needs_fix = pd.isna(iso_val) or str(iso_val).strip() in ("", "nan", "NA", "N/A")
        if not needs_fix:
            continue
        meta = _lookup(row.get("Country", ""), db)
        if meta:
            df.at[idx, "Country ISO"]  = meta["iso"]
            df.at[idx, "BRI country"]  = meta["bri"]
            df.at[idx, "LDCs"]         = meta["ldc"]
            fixed += 1
    df.to_excel(path, index=False)
    return len(df), fixed


# ── 主流程 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 62)
    print("  BRI RE Database — Country ISO 批量修复")
    print("=" * 62)

    # ── Step 1: 加载国家数据库 ─────────────────────────────────────────────────
    print("\n[1/4] 加载 WorldCountryList.json ...")
    db, n_countries = _load_db()
    print(f"      {n_countries} 个国家，{len(db)} 个名称/别名键")

    # ── Step 2: 修复所有 Excel 文件 ────────────────────────────────────────────
    print("\n[2/4] 修复 Excel 文件 ...")

    targets = sorted(ARCHIVED.glob("BRI_RE_Final_Database_*.xlsx")) + \
              sorted(PROCESSED.glob("BRI_RE_Final_Database_*.xlsx"))

    if not targets:
        print("      未找到任何 BRI_RE_Final_Database_*.xlsx 文件，退出。")
        return

    total_fixed = 0
    for path in targets:
        tag = "(Archived)" if path.parent == ARCHIVED else "(processed)"
        n_rows, n_fixed = _fix_excel(path, db)
        status = f"[修复 {n_fixed} 行]" if n_fixed else "[无需修复]"
        print(f"      {path.name} {tag}: {n_rows} 行  {status}")
        total_fixed += n_fixed
    print(f"      合计修复 {total_fixed} 行")

    # ── Step 3: 将 Archived 文件复制回 processed/ ──────────────────────────────
    print("\n[3/4] 将 Archived 文件复制回 processed/ ...")
    copied = 0
    for src in sorted(ARCHIVED.glob("BRI_RE_Final_Database_*.xlsx")):
        dst = PROCESSED / src.name
        shutil.copy2(src, dst)
        print(f"      已复制: {src.name}")
        copied += 1
    print(f"      共复制 {copied} 个文件")

    # ── Step 4: 删除旧数据库 ───────────────────────────────────────────────────
    print("\n[4/4] 删除旧数据库 ...")
    if DB_PATH.exists():
        DB_PATH.unlink()
        print(f"      已删除: {DB_PATH.name}")
    else:
        print("      数据库不存在，跳过")

    # ── 完成提示 ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 62)
    print("  修复完成！下一步：重建数据库")
    print()
    print("  PowerShell（在项目根目录）：")
    print("    uv run --directory s02a_duplicate_merger s02a_duplicate_merger")
    print()
    print("  之后重新导出 s03：")
    print("    uv run --directory s03_data_exporter run_export")
    print("=" * 62)


if __name__ == "__main__":
    main()
