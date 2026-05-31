"""S2 阶段测试：枚举校验与中文名校验。"""
from s01b_project_extractor.stages.s2_basic import validate_s2


def test_validate_s2_flags_bad_category():
    errs = validate_s2([{"common_info": {"Category": "太阳能", "Project Stage": "在建", "Project Name": "肯尼亚光伏项目"}}])
    assert any("分类不合法" in m for m in errs)


def test_validate_s2_flags_non_chinese_name():
    errs = validate_s2([{"common_info": {"Category": "光伏", "Project Stage": "在建", "Project Name": "Kenya Solar"}}])
    assert any("名称未含中文" in m for m in errs)


def test_validate_s2_accepts_good():
    assert validate_s2([{"common_info": {"Category": "光伏", "Project Stage": "在建", "Project Name": "肯尼亚100MW光伏项目"}}]) == []
