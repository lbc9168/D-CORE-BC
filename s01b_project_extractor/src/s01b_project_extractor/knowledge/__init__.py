"""s01b 知识表加载器：主要设备清单（A.5）与容量系数（A.3）；从本包内 JSON 读取，lru_cache。"""
import json
import pathlib
from functools import lru_cache

_DIR = pathlib.Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def _equipment() -> dict:
    """读 main_equipment.json。"""
    return json.loads((_DIR / "main_equipment.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _factors() -> dict:
    """读 capacity_factors.json（含 _note 注释键）。"""
    return json.loads((_DIR / "capacity_factors.json").read_text(encoding="utf-8"))


def main_equipment(category_zh: str) -> list[str]:
    """某项目类型的主要设备清单；未知类型返回空表。"""
    return list(_equipment().get(category_zh, []))


def capacity_factor(category_zh: str) -> float | None:
    """某项目类型的容量系数；未知返回 None。"""
    v = _factors().get(category_zh)
    return float(v) if isinstance(v, (int, float)) else None


def derive_capacity_mw(annual_generation_mwh: float, category_zh: str) -> float | None:
    """A.3：由年发电量(MWh)反推装机容量(MW)= gen/(8760*factor)；无系数返回 None。"""
    f = capacity_factor(category_zh)
    if not f or annual_generation_mwh <= 0:
        return None
    return round(annual_generation_mwh / (8760.0 * f), 1)
