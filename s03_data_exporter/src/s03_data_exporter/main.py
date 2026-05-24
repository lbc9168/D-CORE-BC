"""main.py — s03_data_exporter 入口

运行方式（在 s03_data_exporter/ 目录下）：
    uv run s03_data_exporter
    uv run run_export

执行步骤：
    1. 从 s02a 数据库提取项目列表，AI 提取省份（带本地缓存）
    2. 构建 projects_flat 扁平表，写入 bri_re_export.db
    3. 构建三张聚合统计表（stats_country / stats_entity / stats_timeline）
    4. 导出 projects_flat.xlsx 和 projects_flat.csv
    5. 导出静态 JSON 文件（供 s04 前端）
    6. 导出 events_export.csv（按事件拆条明细，供前端下载）
"""

import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

_S03_ROOT = Path(__file__).resolve().parents[2]

# 优先加载子项目根目录的 .env，找不到则向上查找（自动找到 workspace 根目录的 .env）
load_dotenv(_S03_ROOT / ".env")
load_dotenv()  # fallback：搜索父目录

from s03_data_exporter.province_extractor import extract_provinces  # noqa: E402
from s03_data_exporter.flat_builder import build_flat               # noqa: E402
from s03_data_exporter.stats_builder import build_stats             # noqa: E402
from s03_data_exporter.json_exporter import export_json             # noqa: E402
from s03_data_exporter import flat_builder                          # noqa: E402
from s03_data_exporter import events_exporter                       # noqa: E402

import pandas as pd  # noqa: E402

# ── 路径配置 ──────────────────────────────────────────────────────────────────

_WORKSPACE = _S03_ROOT.parent

SRC_DB = _WORKSPACE / "s02a_duplicate_merger" / "data" / "db" / "bri_re_database.db"
DST_DIR    = _S03_ROOT / "data" / "output"
DST_DB     = DST_DIR / "bri_re_export.db"
CACHE_PATH = _S03_ROOT / "data" / "province_cache.json"

# s04 前端静态数据目录（JSON 同时写入此处，省去手动复制）
S04_DATA_DIR = _WORKSPACE / "s04_web_visualization" / "public" / "data"


def run() -> None:
    """完整导出流程：省份提取 → 扁平表 → 聚合统计 → Excel/CSV 导出"""

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("未找到 GEMINI_API_KEY，请在 .env 文件中配置")

    if not SRC_DB.exists():
        raise FileNotFoundError(
            f"源数据库不存在: {SRC_DB}\n"
            "请先运行 s02a_duplicate_merger 生成数据库。"
        )

    DST_DIR.mkdir(parents=True, exist_ok=True)

    # ── Step 1: 省份提取 ──────────────────────────────────────────────────────
    print("\n[1/6] 提取省份信息（使用 Gemini + 本地缓存）...")

    conn = sqlite3.connect(SRC_DB)
    conn.row_factory = sqlite3.Row
    raw_projects = [
        dict(r)
        for r in conn.execute(
            "SELECT id, location, country, country_iso FROM canonical_projects"
        ).fetchall()
    ]
    conn.close()

    records = [
        {
            "project_id":  r["id"],
            "location":    r.get("location") or "",
            "country":     r.get("country") or "",
            "country_iso": r.get("country_iso") or "",
        }
        for r in raw_projects
    ]

    province_map = extract_provinces(records, CACHE_PATH, api_key)
    non_null = sum(1 for v in province_map.values() if v)
    print(f"  提取完成：{non_null}/{len(province_map)} 个项目有省级信息")

    # ── Step 2: 构建 projects_flat ────────────────────────────────────────────
    print("\n[2/6] 构建 projects_flat...")
    n_rows = build_flat(SRC_DB, DST_DB, province_map)
    print(f"  ✓ projects_flat: {n_rows} 个项目")

    # ── Step 3: 构建聚合统计表 ────────────────────────────────────────────────
    print("\n[3/6] 构建聚合统计表...")
    build_stats(DST_DB)

    # ── Step 4: 导出 Excel / CSV ──────────────────────────────────────────────
    print("\n[4/6] 导出 Excel / CSV...")

    conn = sqlite3.connect(DST_DB)
    df = pd.read_sql("SELECT * FROM projects_flat", conn)
    conn.close()

    xlsx_path = DST_DIR / "projects_flat.xlsx"
    csv_path  = DST_DIR / "projects_flat.csv"

    try:
        df.to_excel(xlsx_path, index=False)
        print(f"  ✓ Excel : {xlsx_path.name}  ({len(df)} 行)")
    except PermissionError:
        print(f"  ⚠ Excel 写入跳过（文件可能已在 Excel 中打开）: {xlsx_path.name}")

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"  ✓ CSV   : {csv_path.name}")

    # ── Step 5: 导出静态 JSON（供 s04 前端使用）──────────────────────────────
    print("\n[5/6] 导出静态 JSON 文件（供前端可视化）...")
    export_json(DST_DB, DST_DIR, src_db=SRC_DB)

    # 同时写入 s04 public/data/（自动同步，无需手动复制）
    if S04_DATA_DIR.parent.exists():
        S04_DATA_DIR.mkdir(parents=True, exist_ok=True)
        export_json(DST_DB, S04_DATA_DIR, src_db=SRC_DB)
        print(f"  ✓ 已同步到 s04: {S04_DATA_DIR}")
    else:
        print("  ℹ s04_web_visualization 目录不存在，跳过同步")

    # ── Step 6: 导出按事件拆条明细 CSV（供前端下载，结构锁定）──────────────────
    print("\n[6/6] 导出 events_export.csv（按事件拆条）...")
    n_ev = events_exporter.export_events_csv(
        SRC_DB,
        [DST_DIR / "events_export.csv", S04_DATA_DIR / "events_export.csv"],
        iso3_to_en=flat_builder.ISO3_TO_EN_NAME,
        province_map=province_map,
    )
    print(f"  ✓ events_export.csv: {n_ev} 行（→ s03 output + s04 public/data）")

    print(f"\n✅ 导出完成 → {DST_DIR}")
    print(f"   主数据库  : {DST_DB.name}")
    print(f"   省份缓存  : {CACHE_PATH.name}（下次运行不重复调用 API）")
