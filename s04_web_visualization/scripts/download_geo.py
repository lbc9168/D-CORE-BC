"""download_geo.py
下载 Natural Earth 省级边界数据，按国家拆分存入 public/geo/provinces/{ISO3}.geojson

使用方式（在 s04_web_visualization/ 目录下运行）：
    python scripts/download_geo.py

来源：Natural Earth ne_10m_admin_1_states_provinces（公共域数据集）
注意：50m 版本仅含 9 个大国省级数据，10m 版才有全球完整覆盖（~4500+ feature）。
zip 包约 10-15MB，拆分后每国文件几KB到几百KB不等。

依赖：pyshp（shapefile 解析）
    pip install pyshp
"""

import io
import json
import sys
import tempfile
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

# ── 配置 ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
S04_ROOT   = SCRIPT_DIR.parent
OUTPUT_DIR = S04_ROOT / "public" / "geo" / "provinces"

# Natural Earth 10m admin1（50m 仅含9个大国，10m 才有全球完整省级数据）
# zip 约 10-15 MB，含全球 ~4500+ 省级 feature
SOURCE_URLS = [
    # NACI CDN（Natural Earth 官方镜像）
    "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_1_states_provinces.zip",
    # Amazon S3（Natural Earth 官方存储，无 LFS 限制）
    "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_1_states_provinces.zip",
    
]
# 完整 10m shapefile 应有 >4000 个省级 feature；低于此视为下载不完整
MIN_EXPECTED_FEATURES = 4000
CACHE_FILE  = SCRIPT_DIR / "_ne_admin1_cache.zip"


# ── 下载 ──────────────────────────────────────────────────────────────────────

def _is_valid_zip(path: Path) -> bool:
    """检查 zip 文件结构是否完整。"""
    try:
        with zipfile.ZipFile(path) as z:
            z.testzip()
        return True
    except Exception:
        return False


def download_zip() -> Path:
    """逐一尝试各源下载 zip，跳过已有有效缓存（且 shp 足够大）。"""
    if CACHE_FILE.exists() and _is_valid_zip(CACHE_FILE):
        size_kb = CACHE_FILE.stat().st_size // 1024
        # 完整 zip 应大于 2 MB；小于此说明是之前截断的缓存
        if size_kb >= 2000:
            print(f"  使用缓存文件: {CACHE_FILE.name}（{size_kb} KB）")
            return CACHE_FILE
        else:
            print(f"  缓存文件过小（{size_kb} KB），可能不完整，删除并重新下载...")
            CACHE_FILE.unlink()

    for url in SOURCE_URLS:
        print(f"  尝试下载: {url}")
        try:
            urllib.request.urlretrieve(url, CACHE_FILE)
            size_kb = CACHE_FILE.stat().st_size // 1024
            print(f"  下载完成: {size_kb} KB")
            if size_kb < 2000:
                print(f"  [警告] 文件过小（{size_kb} KB），可能被截断，尝试下一个源...")
                CACHE_FILE.unlink()
                continue
            if not _is_valid_zip(CACHE_FILE):
                print("  [警告] zip 文件损坏，尝试下一个源...")
                CACHE_FILE.unlink()
                continue
            print(f"  已缓存到: {CACHE_FILE.name}（{size_kb} KB）")
            return CACHE_FILE
        except Exception as e:
            print(f"  下载失败: {e}，尝试下一个源...")
            if CACHE_FILE.exists():
                CACHE_FILE.unlink()

    print("\n  [错误] 所有下载源均失败。")
    print("  可能原因：网络受限（如 GFW）。建议通过 VPN 后重试，")
    print("  或手动下载以下文件并保存为 scripts/_ne_admin1_cache.zip：")
    for url in SOURCE_URLS:
        print(f"    {url}")
    sys.exit(1)


# ── 解析 Shapefile ─────────────────────────────────────────────────────────────

def _load_shapefile(zip_path: Path) -> list[dict]:
    """从 zip 中解析 shapefile，转为 GeoJSON feature 列表。"""
    try:
        import shapefile  # pyshp  # type: ignore[import-untyped]
    except ImportError:
        print("\n  [错误] 缺少依赖 pyshp，请先安装：")
        print("      pip install pyshp")
        sys.exit(1)

    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        shp_name = next((n for n in names if n.endswith(".shp")), None)
        dbf_name = next((n for n in names if n.endswith(".dbf")), None)
        shx_name = next((n for n in names if n.endswith(".shx")), None)
        if not shp_name:
            print("  [错误] zip 中未找到 .shp 文件，下载可能不完整")
            sys.exit(1)

        shp_data = io.BytesIO(z.read(shp_name))
        dbf_data = io.BytesIO(z.read(dbf_name))
        shx_data = io.BytesIO(z.read(shx_name)) if shx_name else None

        sf = shapefile.Reader(shp=shp_data, dbf=dbf_data, shx=shx_data)
        fields = [f[0] for f in sf.fields[1:]]  # 跳过 DeletionFlag

        features = []
        for sr in sf.shapeRecords():
            props = {k: v for k, v in zip(fields, sr.record)}
            try:
                geo = sr.shape.__geo_interface__
            except Exception:
                continue
            features.append({
                "type": "Feature",
                "properties": props,
                "geometry": geo,
            })

    return features


# ── 拆分与写入 ─────────────────────────────────────────────────────────────────

def split_by_country(features: list[dict]) -> dict[str, list]:
    """按 adm0_a3（国家 ISO3）分组省级 feature。"""
    groups: dict[str, list] = defaultdict(list)
    for feature in features:
        props = feature.get("properties") or {}
        iso3 = (props.get("adm0_a3") or props.get("iso_a3") or "").strip().upper()
        if iso3 and iso3 != "-99":
            groups[iso3].append(feature)
    return dict(groups)


def write_country_files(groups: dict[str, list]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    count = 0
    for iso3, features in sorted(groups.items()):
        geojson = {"type": "FeatureCollection", "features": features}
        out_path = OUTPUT_DIR / f"{iso3}.geojson"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(geojson, f, ensure_ascii=False, separators=(",", ":"))
        count += 1
    print(f"  写入 {count} 个国家的省级 GeoJSON -> {OUTPUT_DIR}")


# ── 主流程 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n[1/3] 获取 Natural Earth admin1 数据（zip）...")
    zip_path = download_zip()

    print("\n[2/3] 解析 shapefile，按国家拆分...")
    features = _load_shapefile(zip_path)
    print(f"  共 {len(features)} 个省/州 feature")
    if len(features) < MIN_EXPECTED_FEATURES:
        print(f"\n  [错误] 数据不完整（期望 >{MIN_EXPECTED_FEATURES}，实际 {len(features)}）。")
        print("  请删除缓存文件后通过 VPN 重试：")
        print(f"    del scripts\\_ne_admin1_cache.zip")
        sys.exit(1)
    groups = split_by_country(features)
    print(f"  共 {len(groups)} 个国家")

    print("\n[3/3] 写入文件...")
    write_country_files(groups)

    print("\n完成！请重启 vite dev server 使前端可访问省级地图。")
    print(f"   文件位置: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
