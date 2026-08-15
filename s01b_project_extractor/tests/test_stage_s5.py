"""S5 阶段测试：摘要写入与高置信纠偏应用（含 involvement_type）。"""
from s01b_project_extractor.stages.s5_summary import apply_s5


def test_apply_s5_sets_summary():
    """S5 摘要应写入 project["project_summary"]。"""
    p = {"common_info": {"Project Stage": "在建"}}
    apply_s5(p, {"project_summary": "中国能建在肯尼亚建100MW光伏，在建。", "corrections": []})
    assert "肯尼亚" in p["project_summary"]


def test_apply_s5_applies_high_conf_valid_correction():
    """高置信度(≥0.8)且枚举合法的纠偏应应用到 common_info。"""
    p = {"common_info": {"Project Stage": "投入运营"}}
    apply_s5(p, {"project_summary": "x", "corrections": [{"field": "project_stage", "suggested_value": "竣工", "confidence": 0.95}]})
    assert p["common_info"]["Project Stage"] == "竣工"


def test_apply_s5_ignores_low_conf_or_bad_enum():
    """低置信度或枚举非法的纠偏应忽略（保持原值）。"""
    p = {"common_info": {"Project Stage": "投入运营"}}
    apply_s5(p, {"corrections": [{"field": "project_stage", "suggested_value": "竣工", "confidence": 0.5}]})
    assert p["common_info"]["Project Stage"] == "投入运营"
    apply_s5(p, {"corrections": [{"field": "project_stage", "suggested_value": "乱填", "confidence": 0.99}]})
    assert p["common_info"]["Project Stage"] == "投入运营"


def test_apply_s5_never_overrides_deterministic_category():
    """S5 即使给出高置信合法类别也不得覆盖 energy_taxonomy_v2 的结果。"""
    p = {"common_info": {"Category": "光伏"}}
    apply_s5(p, {"corrections": [
        {"field": "category", "suggested_value": "综合能源", "confidence": 0.99},
    ]})
    assert p["common_info"]["Category"] == "光伏"


def test_apply_s5_ignores_non_numeric_confidence():
    """confidence 为非数值字符串时应跳过纠偏（不崩溃）。"""
    p = {"common_info": {"Project Stage": "在建"}}
    apply_s5(p, {"corrections": [{"field": "project_stage", "suggested_value": "竣工", "confidence": "high"}]})
    assert p["common_info"]["Project Stage"] == "在建"


def test_apply_s5_empty_s5_out():
    """S5 返回空 dict 时不修改 project（不崩溃）。"""
    p = {"common_info": {"Project Stage": "签约"}}
    apply_s5(p, {})
    assert p["common_info"]["Project Stage"] == "签约"
    assert p.get("project_summary", "") == ""


def test_apply_s5_no_summary_when_empty_string():
    """project_summary 为空字符串时不覆写现有值。"""
    p = {"common_info": {}, "project_summary": "已有摘要"}
    apply_s5(p, {"project_summary": "   ", "corrections": []})
    assert p["project_summary"] == "已有摘要"


# ── involvement_type 纠偏用例 ──

def test_apply_s5_applies_high_conf_valid_involvement():
    """高置信度(≥0.8)且合法的 involvement_type 纠偏应应用到所有 chinese_entities。"""
    p = {
        "common_info": {},
        "chinese_entities": [
            {"Implementing Chinese Entity": "中国电建", "Type of involvement of Chinese Entities": "EPC总承包"},
            {"Implementing Chinese Entity": "国家开发银行", "Type of involvement of Chinese Entities": "EPC总承包"},
        ],
    }
    apply_s5(p, {
        "project_summary": "x",
        "corrections": [
            {"field": "involvement_type", "suggested_value": "融资方", "confidence": 0.9},
        ],
    })
    for ent in p["chinese_entities"]:
        assert ent["Type of involvement of Chinese Entities"] == "融资方"


def test_apply_s5_ignores_low_conf_involvement():
    """低置信度(< 0.8)的 involvement_type 纠偏应忽略。"""
    p = {
        "common_info": {},
        "chinese_entities": [
            {"Implementing Chinese Entity": "中国电建", "Type of involvement of Chinese Entities": "EPC总承包"},
        ],
    }
    apply_s5(p, {
        "corrections": [
            {"field": "involvement_type", "suggested_value": "融资方", "confidence": 0.7},
        ],
    })
    assert p["chinese_entities"][0]["Type of involvement of Chinese Entities"] == "EPC总承包"


def test_apply_s5_ignores_invalid_involvement_enum():
    """非法枚举值的 involvement_type 纠偏应忽略（保持原值）。"""
    p = {
        "common_info": {},
        "chinese_entities": [
            {"Implementing Chinese Entity": "中国电建", "Type of involvement of Chinese Entities": "EPC总承包"},
        ],
    }
    apply_s5(p, {
        "corrections": [
            {"field": "involvement_type", "suggested_value": "非法参与方式", "confidence": 0.99},
        ],
    })
    assert p["chinese_entities"][0]["Type of involvement of Chinese Entities"] == "EPC总承包"


def test_apply_s5_involvement_no_entities():
    """无 chinese_entities 时 involvement_type 纠偏不崩溃。"""
    p = {"common_info": {}}
    apply_s5(p, {
        "corrections": [
            {"field": "involvement_type", "suggested_value": "融资方", "confidence": 0.9},
        ],
    })
    assert p.get("chinese_entities", []) == []
