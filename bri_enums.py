"""枚举单一可信源的 Python 加载器：读根目录 enums_mapping.json，供 s01b/s02a/s03 共用。"""
import json
import pathlib
from functools import lru_cache

# chinese_unnamed 占位实体名（E.2）
UNNAMED_ENTITY_PLACEHOLDER = "未具名中企"


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


def has_storage_metrics(zh: str) -> bool:
    """该 category 是否以储能功率/能量为核心指标（导出时保留 storage_* 字段）。"""
    return _data()["category"].get(zh, {}).get("storage_metrics", False)


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


# ── MF 产能单位（阶段3 任务3.1） ──
def mf_production_units() -> list[str]:
    """MF（新能源制造业）production_capacity_unit 受控枚举取值列表。"""
    return list(_data()["mf_production_unit"]["values"])


# ── 子类（subcategory） ──
def _subcat_section(cat_zh: str) -> dict:
    """内部：某父类的 subcategory 定义（无定义返回空 dict）。"""
    sec = _data().get("subcategory", {}).get(cat_zh)
    return sec if isinstance(sec, dict) else {}


def subcategory_parents() -> list[str]:
    """有子类定义的父类中文键列表（如 光伏、综合能源）。"""
    return [k for k in _data().get("subcategory", {}) if not k.startswith("_")]


def subcategories(cat_zh: str) -> list[str]:
    """某父类的合法子类中文键列表（无子类定义返回 []）。"""
    return list(_subcat_section(cat_zh).get("values", {}).keys())


def subcategory_en(cat_zh: str, sub_zh: str) -> str | None:
    """子类中文键 → 英文标签。"""
    return _subcat_section(cat_zh).get("values", {}).get(sub_zh, {}).get("en")


def subcategory_default(cat_zh: str) -> str | None:
    """某父类子类的默认值（DB 为 NULL 时导出层渲染用）。"""
    return _subcat_section(cat_zh).get("_default")


def effective_subcategory(cat_zh: str, sub_zh: str | None) -> str | None:
    """DB 子类值 → 有效子类：非空原样返回，NULL/空按父类默认；无子类定义返回 None。"""
    return sub_zh if sub_zh else subcategory_default(cat_zh)
