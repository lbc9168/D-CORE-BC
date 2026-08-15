"""S1 枚举单一可信源一致性测试：保证 bri_enums 与 enums_mapping.json 自洽。"""
import json
import pathlib

import bri_enums as e

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = json.loads((ROOT / "enums_mapping.json").read_text(encoding="utf-8"))


def test_category_codes_unique_and_present():
    """每个 category 的 code 唯一且必填字段齐全。"""
    codes = [v["code"] for v in RAW["category"].values()]
    assert len(codes) == len(set(codes)), "category code 必须唯一"
    for zh, v in RAW["category"].items():
        for f in ("zh", "en", "code", "color", "no_capacity", "hidden", "excluded"):
            assert f in v, f"category {zh} 缺字段 {f}"


def test_stage_priorities_unique():
    """非 Unknown 的 stage priority 唯一。"""
    prios = [v["priority"] for k, v in RAW["project_stage"].items() if k != "Unknown"]
    assert len(prios) == len(set(prios)), "stage priority 必须唯一"


def test_loader_matches_json():
    """bri_enums 助手函数返回值与 JSON 一致。"""
    assert e.category_code("光伏") == "PV"
    assert e.category_en("风电") == "Wind Power"
    assert e.stage_priority("竣工") == 6
    assert e.is_no_capacity("储能") is True
    assert e.is_hidden_category("核电") is True
    assert e.is_excluded_category("火电") is False  # P2.1: 火电 excluded→false
    assert set(e.valid_categories()) == set(RAW["category"].keys())
    assert set(e.valid_stages()) == set(RAW["project_stage"].keys())


def test_thermal_power_not_excluded():
    """火电属发电项目，必须录入保留：excluded 应为 False（v2.1 P2.1 决策）。"""
    import bri_enums
    assert bri_enums.is_excluded_category("火电") is False, "火电不应再被基线排除"


def test_no_category_excluded_after_p21():
    """P2.1 后无任何 category 处于 excluded=true（火电是历史上唯一一个）。"""
    import bri_enums
    excluded = [c for c in bri_enums.valid_categories() if bri_enums.is_excluded_category(c)]
    assert excluded == [], f"不应有 excluded category，实际={excluded}"


def test_mf_production_unit_values():
    """mf_production_unit 恰好 4 值，且 bri_enums 访问器与 JSON 一致（阶段3 任务3.1）。"""
    assert RAW["mf_production_unit"]["values"] == ["GW/年", "GWh/年", "万吨/年", "Unknown"]
    assert e.mf_production_units() == RAW["mf_production_unit"]["values"]


def test_subcategory_parents_are_valid_categories():
    """subcategory 每个父类必须是合法 category 键，且 _default ∈ values。"""
    for parent in e.subcategory_parents():
        assert parent in RAW["category"], f"subcategory 父类 {parent} 不是合法 category"
        sec = RAW["subcategory"][parent]
        assert sec["_default"] in sec["values"], f"{parent} 的 _default 不在 values 中"


def test_subcategory_accessors_match_json():
    """subcategory 访问器与 JSON 一致；无子类定义的类别返回空。"""
    for parent in e.subcategory_parents():
        sec = RAW["subcategory"][parent]
        assert e.subcategories(parent) == list(sec["values"].keys())
        assert e.subcategory_default(parent) == sec["_default"]
        for sub, meta in sec["values"].items():
            assert e.subcategory_en(parent, sub) == meta["en"]
    assert e.subcategories("风电") == []
    assert e.subcategory_default("风电") is None


def test_effective_subcategory_default_rendering():
    """effective_subcategory：NULL 渲染父类默认，非空原样返回，无定义类别返回 None。"""
    for parent in e.subcategory_parents():
        assert e.effective_subcategory(parent, None) == e.subcategory_default(parent)
        for sub in e.subcategories(parent):
            assert e.effective_subcategory(parent, sub) == sub
    assert e.effective_subcategory("风电", None) is None


def test_storage_metrics_flag():
    """储能类别带 storage_metrics 标志（导出层保留 storage_* 的依据），其余类别为 False。"""
    assert e.has_storage_metrics("储能") is True
    assert e.has_storage_metrics("输变电建设") is False
    assert e.has_storage_metrics("新能源制造业") is False
    assert e.has_storage_metrics("光伏") is False
