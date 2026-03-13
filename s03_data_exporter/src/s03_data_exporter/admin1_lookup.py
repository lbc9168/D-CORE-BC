"""admin1_lookup.py
Natural Earth admin1 省份名称查找表（按 ISO3 分组）。

从 s04_web_visualization/scripts/_ne_admin1_cache.zip 读取 shapefile，
提取每个国家的省/州一级行政区英文名列表，以 {ISO3: [name_en, ...]} 形式返回。

特性：
- 模块级单例缓存（进程内只读一次 zip）
- 默认路径相对于模块文件位置自动解析
- zip 路径可由调用方传入覆盖（便于测试）
- zip 不存在或 pyshp 未安装时，返回空字典并打印警告（不抛异常），
  province_extractor 退化为原有无约束行为
"""

from __future__ import annotations

import io
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Optional

# ── 默认 zip 路径 ─────────────────────────────────────────────────────────────
# 从此模块文件位置上溯：
#   admin1_lookup.py  -> s03/src/s03_data_exporter/   (parent)
#   parents[0]        -> s03/src/s03_data_exporter/
#   parents[1]        -> s03/src/
#   parents[2]        -> s03/  (s03_data_exporter 子项目根目录)
#   parents[3]        -> workspace 根目录
_MODULE_DIR = Path(__file__).resolve().parent
_DEFAULT_ZIP = (
    _MODULE_DIR.parents[2]
    / "s04_web_visualization"
    / "scripts"
    / "_ne_admin1_cache.zip"
)

# ── 模块级单例缓存 ────────────────────────────────────────────────────────────
_cache: Optional[dict[str, list[str]]] = None


def build_admin1_dict(zip_path: Optional[Path] = None) -> dict[str, list[str]]:
    """
    返回 {ISO3: [province_english_name, ...]} 字典。

    第一次调用时读取 zip 并缓存于模块变量；后续调用直接返回缓存。

    Args:
        zip_path: 覆盖默认 zip 路径（None = 使用 _DEFAULT_ZIP）

    Returns:
        dict keyed by uppercase ISO3 code。
        国家不在 Natural Earth 数据中时键不存在（调用方应 .get() 处理）。
        zip 不存在或 pyshp 未安装时返回空字典。
    """
    global _cache
    if _cache is not None:
        return _cache

    resolved = Path(zip_path) if zip_path else _DEFAULT_ZIP

    if not resolved.exists():
        print(
            f"  [admin1_lookup] 警告：Natural Earth zip 未找到: {resolved}\n"
            "  省份名称验证将跳过（Gemini 自由生成，无列表约束）"
        )
        _cache = {}
        return _cache

    try:
        import shapefile  # pyshp
    except ImportError:
        print(
            "  [admin1_lookup] 警告：未安装 pyshp，省份验证将跳过。\n"
            "  请运行: uv add pyshp  （或在 pyproject.toml 中添加 pyshp>=2.3.0）"
        )
        _cache = {}
        return _cache

    result: dict[str, list[str]] = defaultdict(list)

    with zipfile.ZipFile(resolved) as z:
        names = z.namelist()
        shp_name = next((n for n in names if n.endswith(".shp")), None)
        dbf_name = next((n for n in names if n.endswith(".dbf")), None)
        shx_name = next((n for n in names if n.endswith(".shx")), None)

        if not shp_name or not dbf_name:
            print("  [admin1_lookup] 警告：zip 中未找到 .shp/.dbf 文件，省份验证将跳过")
            _cache = {}
            return _cache

        shp_data = io.BytesIO(z.read(shp_name))
        dbf_data = io.BytesIO(z.read(dbf_name))
        shx_data = io.BytesIO(z.read(shx_name)) if shx_name else None

        sf = shapefile.Reader(shp=shp_data, dbf=dbf_data, shx=shx_data)
        fields = [f[0] for f in sf.fields[1:]]  # 跳过 DeletionFlag

        for sr in sf.shapeRecords():
            props = dict(zip(fields, sr.record))
            iso3 = (props.get("adm0_a3") or "").strip().upper()
            # 优先使用 name_en，fallback 到 name
            name_en = (props.get("name_en") or props.get("name") or "").strip()
            if iso3 and iso3 != "-99" and name_en:
                result[iso3].append(name_en)

    # 每个国家的省份列表去重并排序（稳定输出，便于调试）
    _cache = {iso3: sorted(set(names)) for iso3, names in result.items()}
    total_provinces = sum(len(v) for v in _cache.values())
    print(
        f"  [admin1_lookup] 已加载 {len(_cache)} 个国家、"
        f"{total_provinces} 条省份名称"
    )
    return _cache
