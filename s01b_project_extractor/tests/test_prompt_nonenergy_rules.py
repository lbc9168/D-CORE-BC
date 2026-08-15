"""S1/S2 prompt 非能源收紧规则的存在性锚定（防止后续改 prompt 时误删）。"""
from s01b_project_extractor.prompts.s1_relevance import build_s1_prompt
from s01b_project_extractor.prompts.s2_basic import build_s2_prompt


def test_s1_manufacturing_scope_limited_to_ne_equipment():
    p = build_s1_prompt("t", "c", "2026-01-01")
    assert "仅限**新能源装备/材料**产能" in p
    assert "钾肥、锂盐、氧化铝、甲醇、水泥、钢铁" in p


def test_s1_mineral_products_excluded():
    p = build_s1_prompt("t", "c", "2026-01-01")
    assert "矿产/化肥/化工/冶金/建材产品的开采、生产与加工本身" in p


def test_s1_hydrogen_rule():
    p = build_s1_prompt("t", "c", "2026-01-01")
    assert "只提「绿氢/电解」而无明确能源种类" in p


def test_s2_hybrid_requires_generation_subitems():
    p = build_s2_prompt("t", "c", "2026-01-01")
    assert "储能不是发电源" in p
    assert "只有两种及以上非备用发电源组合才归「综合能源」" in p


def test_s2_hydrogen_classified_by_source():
    p = build_s2_prompt("t", "c", "2026-01-01")
    assert "如光伏制氢→归「光伏」" in p
    assert "不要**仅因带「绿氢」就归「综合能源」" in p


def test_s2_out_of_scope_category_unknown():
    p = build_s2_prompt("t", "c", "2026-01-01")
    assert "in_scope=false 时 Category 一律填 \"Unknown\"" in p


def test_validate_s2_accepts_unknown_category():
    from s01b_project_extractor.stages.s2_basic import validate_s2
    errs = validate_s2([{"common_info": {"Category": "Unknown", "Project Stage": "在建",
                                         "Project Name": "某油气项目"}}])
    assert errs == []
