"""download_geo.py
下载 Natural Earth 省级边界数据，按国家拆分存入 public/geo/provinces/{ISO3}.geojson

使用方式（在 s04_web_visualization/ 目录下运行）：
    python scripts/download_geo.py

来源：Natural Earth ne_50m_admin_1_states_provinces（公共域数据集）
数据约 3MB，拆分后每国文件几KB到几百KB不等。
"""

import json
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

# ── 配置 ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR  = Path(__file__).resolve().parent
S04_ROOT    = SCRIPT_DIR.parent
OUTPUT_DIR  = S04_ROOT / "public" / "geo" / "provinces"

# Natural Earth 50m admin1 GeoJSON（来自官方 GitHub release）
SOURCE_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
    "master/geojson/ne_50m_admin_1_states_provinces.geojson"
)

# 缓存原始文件，避免重复下载
CACHE_FILE = SCRIPT_DIR / "_ne_admin1_cache.geojson"


def download_source() -> dict:
    if CACHE_FILE.exists():
        print(f"  使用缓存文件: {CACHE_FILE.name}")
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)

    print(f"  下载中: {SOURCE_URL}")
    print("  （文件约 3MB，请稍候…）")
    try:
        with urllib.request.urlopen(SOURCE_URL, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
    except Exception as e:
        print(f"\n❌ 下载失败: {e}")
        print("请检查网络连接，或手动下载后保存为 scripts/_ne_admin1_cache.geojson")
        sys.exit(1)

    data = json.loads(raw)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"  已缓存到: {CACHE_FILE.name}")
    return data


def split_by_country(data: dict) -> dict[str, list]:
    """按 adm0_a3（国家 ISO3）分组省级 feature"""
    groups: dict[str, list] = defaultdict(list)
    for feature in data.get("features", []):
        props = feature.get("properties") or {}
        iso3 = (props.get("adm0_a3") or props.get("iso_a3") or "").strip().upper()
        if iso3 and iso3 != "-99":
            groups[iso3].append(feature)
    return dict(groups)


def write_country_files(groups: dict[str, list]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    for iso3, features in sorted(groups.items()):
        geojson = {
            "type": "FeatureCollection",
            "features": features,
        }
        out_path = OUTPUT_DIR / f"{iso3}.geojson"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(geojson, f, ensure_ascii=False, separators=(",", ":"))
        count += 1

    print(f"  ✓ 写入 {count} 个国家的省级 GeoJSON → {OUTPUT_DIR}")


def main() -> None:
    print("\n[1/3] 获取 Natural Earth admin1 数据...")
    data = download_source()
    total_features = len(data.get("features", []))
    print(f"  共 {total_features} 个省/州 feature")

    print("\n[2/3] 按国家拆分...")
    groups = split_by_country(data)
    print(f"  共 {len(groups)} 个国家")

    print("\n[3/3] 写入文件...")
    write_country_files(groups)

    print(f"\n✅ 完成！请重启 vite dev server 或重新 build 以使前端可访问省级地图。")
    print(f"   文件位置: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
