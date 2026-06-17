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
