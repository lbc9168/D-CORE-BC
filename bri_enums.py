"""枚举单一可信源的 Python 加载器：读根目录 enums_mapping.json，供 s01b/s02a/s03 共用。"""
import json
import pathlib
from functools import lru_cache


@lru_cache(maxsize=1)
def _data() -> dict:
    """从本文件所在目录读取 enums_mapping.json，避免脆弱的 parents[N] 路径。"""
    path = pathlib.Path(__file__).resolve().parent / "enums_mapping.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ── 集合 ──
def valid_categories() -> list[str]:
    """所有规范 category 中文键。"""
    return list(_data()["category"].keys())


def valid_stages() -> list[str]:
    """所有规范 project_stage 中文键。"""
    return list(_data()["project_stage"].keys())


def valid_financing() -> list[str]:
    """所有规范 financing_model 中文键。"""
    return list(_data()["financing_model"].keys())


def valid_involvement() -> list[str]:
    """所有规范 involvement_type 中文键。"""
    return list(_data()["involvement_type"].keys())


# ── category 助手 ──
def category_code(zh: str) -> str | None:
    """category 中文键 → code（如 光伏→PV）。"""
    return _data()["category"].get(zh, {}).get("code")


def category_en(zh: str) -> str | None:
    """category 中文键 → 英文标签。"""
    return _data()["category"].get(zh, {}).get("en")


def is_no_capacity(zh: str) -> bool:
    """该 category 是否无装机容量概念。"""
    return _data()["category"].get(zh, {}).get("no_capacity", False)


def is_hidden_category(zh: str) -> bool:
    """该 category 是否前端默认隐藏。"""
    return _data()["category"].get(zh, {}).get("hidden", False)


def is_excluded_category(zh: str) -> bool:
    """该 category 是否被基线策略剔除。"""
    return _data()["category"].get(zh, {}).get("excluded", False)


# ── stage 助手 ──
def stage_priority(zh: str) -> int:
    """project_stage 中文键 → 终态优先级（越大越终态，未知 -1）。"""
    return _data()["project_stage"].get(zh, {}).get("priority", -1)


def stage_en(zh: str) -> str | None:
    """project_stage 中文键 → 英文标签。"""
    return _data()["project_stage"].get(zh, {}).get("en")


def viz_group(zh: str) -> str | None:
    """project_stage 中文键 → 4 可视化大类 key（前期/在建/完工/收购）；隐藏 stage 返回 None。"""
    return _data()["project_stage"].get(zh, {}).get("viz_group")


def viz_groups() -> dict:
    """4 可视化大类的元信息字典（key → {zh, en, color, order}）。"""
    return _data()["viz_group"]


# ── 通用 en ──
def financing_en(zh: str) -> str | None:
    """financing_model 中文键 → 英文标签。"""
    return _data()["financing_model"].get(zh, {}).get("en")


def involvement_en(zh: str) -> str | None:
    """involvement_type 中文键 → 英文标签。"""
    return _data()["involvement_type"].get(zh, {}).get("en")


def prompt_value_list(enum: str) -> str:
    """给 s01b prompt 用：返回某枚举所有中文键的逗号串。"""
    return ", ".join(_data()[enum].keys())
