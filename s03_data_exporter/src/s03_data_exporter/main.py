"""main.py — s03_data_exporter 入口

运行方式（在 s03_data_exporter/ 目录下）：
    uv run s03_data_exporter
    uv run run_export

执行步骤：
    1. 从 s02a 数据库提取项目列表，AI 提取省份（带本地缓存）
    2. 构建 projects_flat 扁平表，写入 bri_re_export.db
    3. 构建 tx_flat / mf_flat 专用扁平表（输变电建设 / 新能源制造业）
    4. 构建三张聚合统计表（stats_country / stats_entity / stats_timeline）
    5. 导出 projects_flat.xlsx 和 projects_flat.csv
    6. 导出静态 JSON 文件（供 s04 前端）
    7. 导出 events_export.csv（按事件拆条明细，供前端下载）
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

# Windows GBK 控制台下强制 UTF-8 输出，避免 ✓/中文等字符触发 UnicodeEncodeError（与 s02b CLI 一致）
if sys.stdout.encoding and sys.stdout.encoding.lower() in ("gbk", "cp936", "cp950"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from dotenv import load_dotenv

_S03_ROOT = Path(__file__).resolve().parents[2]

# 优先加载子项目根目录的 .env，找不到则向上查找（自动找到 workspace 根目录的 .env）
load_dotenv(_S03_ROOT / ".env")
load_dotenv()  # fallback：搜索父目录

from s03_data_exporter.province_extractor import extract_provinces  # noqa: E402
from s03_data_exporter.flat_builder import build_flat               # noqa: E402
from s03_data_exporter.flat_builder import build_tx_flat, build_mf_flat  # noqa: E402
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


def run(*, external_references: Path | None = None,
        src_db: Path | str | None = None, dst_dir: Path | str | None = None,
        web_data_dir: Path | str | None = None, province_cache: Path | str | None = None,
        allow_model_calls: bool = True) -> dict:
    """完整导出流程：省份提取 → 扁平表 → 聚合统计 → Excel/CSV 导出"""

    source = Path(src_db if src_db is not None else SRC_DB).resolve()
    if source != SRC_DB.resolve() and (dst_dir is None or web_data_dir is None):
        raise ValueError("自定义源库导出须显式指定dst_dir和web_data_dir，防止写入生产导出")
    destination = Path(dst_dir if dst_dir is not None else DST_DIR).resolve()
    web = Path(web_data_dir if web_data_dir is not None else S04_DATA_DIR).resolve()
    exported = destination / "bri_re_export.db"
    cache = Path(province_cache).resolve() if province_cache is not None else (
        CACHE_PATH if source == SRC_DB.resolve() else destination / "province_cache.json")
    if source in {exported, cache.resolve()}:
        raise ValueError("源库、导出库、省份缓存必须分离")
    warnings = []
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if allow_model_calls and not api_key:
        raise RuntimeError("未找到 GEMINI_API_KEY，请在 .env 文件中配置")

    if not source.exists():
        raise FileNotFoundError(
            f"源数据库不存在: {source}\n"
            "请先运行 s02a_duplicate_merger 生成数据库。"
        )

    destination.mkdir(parents=True, exist_ok=True)
    cache.parent.mkdir(parents=True, exist_ok=True)

    # ── Step 1: 省份提取 ──────────────────────────────────────────────────────
    print("\n[1/6] 提取省份信息（使用 Gemini + 本地缓存）...")

    conn = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
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

    province_diagnostics = {}
    province_map = extract_provinces(records, cache, api_key, allow_model_calls=allow_model_calls,
                                    diagnostics=province_diagnostics)
    if province_diagnostics.get("failed_ids"):
        warnings.append(f"省份提取失败{len(province_diagnostics['failed_ids'])}项，可重试；本次以未知省份导出")
    non_null = sum(1 for v in province_map.values() if v)
    print(f"  提取完成：{non_null}/{len(province_map)} 个项目有省级信息")

    # ── Step 2: 构建 projects_flat ────────────────────────────────────────────
    print("\n[2/7] 构建 projects_flat...")
    n_rows = build_flat(source, exported, province_map)
    print(f"  ✓ projects_flat: {n_rows} 个项目")

    # ── Step 3: 构建 tx_flat / mf_flat（任务 3.4：TX/MF 专用扁平表）───────────
    print("\n[3/7] 构建 tx_flat / mf_flat...")
    n_tx = build_tx_flat(source, exported, province_map)
    n_mf = build_mf_flat(source, exported, province_map)
    print(f"  ✓ tx_flat: {n_tx} 个项目, mf_flat: {n_mf} 个项目")

    # ── Step 4: 构建聚合统计表 ────────────────────────────────────────────────
    print("\n[4/7] 构建聚合统计表...")
    build_stats(exported)

    # ── Step 5: 导出 Excel / CSV ──────────────────────────────────────────────
    print("\n[5/7] 导出 Excel / CSV...")

    conn = sqlite3.connect(exported)
    df = pd.read_sql("SELECT * FROM projects_flat", conn)
    conn.close()

    xlsx_path = destination / "projects_flat.xlsx"
    csv_path  = destination / "projects_flat.csv"

    try:
        df.to_excel(xlsx_path, index=False)
        print(f"  ✓ Excel : {xlsx_path.name}  ({len(df)} 行)")
    except PermissionError:
        warnings.append("Excel文件被占用，未更新")
        print(f"  ⚠ Excel 写入跳过（文件可能已在 Excel 中打开）: {xlsx_path.name}")

    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"  ✓ CSV   : {csv_path.name}")

    # ── Step 6: 导出静态 JSON（供 s04 前端使用）──────────────────────────────
    print("\n[6/7] 导出静态 JSON 文件（供前端可视化）...")
    export_json(exported, destination, src_db=source, external_references=external_references)

    # 同时写入 s04 public/data/（自动同步，无需手动复制）
    if web_data_dir is not None or web.parent.exists():
        web.mkdir(parents=True, exist_ok=True)
        export_json(exported, web, src_db=source, external_references=external_references)
        print(f"  ✓ 已同步到 s04: {web}")
    else:
        print("  ℹ s04_web_visualization 目录不存在，跳过同步")

    # ── Step 7: 导出按事件拆条明细 CSV（供前端下载，结构锁定）──────────────────
    print("\n[7/7] 导出 events_export.csv（按事件拆条）...")
    n_ev = events_exporter.export_events_csv(
        source,
        [destination / "events_export.csv", web / "events_export.csv"],
        flat_db=exported,
        iso3_to_en=flat_builder.ISO3_TO_EN_NAME,
        province_map=province_map,
    )
    print(f"  ✓ events_export.csv: {n_ev} 行（→ s03 output + s04 public/data）")

    print(f"\n✅ 导出完成 → {destination}")
    print(f"   主数据库  : {exported.name}")
    print(f"   省份缓存  : {cache.name}（下次运行不重复调用 API）")

    return {"status": "partial" if warnings else "completed", "source_db": str(source),
            "output_db": str(exported), "output_dir": str(destination), "web_data_dir": str(web),
            "project_rows": n_rows, "event_rows": n_ev, "warnings": warnings,
            "allow_model_calls": allow_model_calls, "province_diagnostics": province_diagnostics}


def main() -> None:
    """Keep CLI arguments separate from pipeline callers of run()."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--external-references", type=Path, help="Optional s02e metadata snapshot; never triggers reference API calls")
    args = parser.parse_args()
    run(external_references=args.external_references)


if __name__ == "__main__":
    main()
