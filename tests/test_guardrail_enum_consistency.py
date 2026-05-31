"""守护测试：guardrail 合法枚举集 == enums_mapping.json 对应枚举键集，杜绝再漂移。"""
import bri_enums as e
from s01b_project_extractor.guardrails import format_checks as fc
from s01b_project_extractor.guardrails import semantic_checks as sc


def test_format_checks_enums_match_bri_enums():
    assert fc.valid_categories_set() == set(e.valid_categories())
    assert fc.valid_stages_set() == set(e.valid_stages())
    assert fc.valid_involvement_set() == set(e.valid_involvement())
    assert fc.valid_financing_set() == set(e.valid_financing())


def test_semantic_checks_enums_match_bri_enums():
    assert sc.valid_stages_set() == set(e.valid_stages())
    assert sc.valid_categories_set() == set(e.valid_categories())
    assert sc.valid_involvement_set() == set(e.valid_involvement())


def test_new_enum_value_is_accepted():
    """'一体化方案提供商' 必须被 format 校验视为合法（回归 P0.1 漂移 bug）。"""
    rec = {"id": 1, "project_name": "测试项目", "country": "肯尼亚", "category": "光伏",
           "implementing_entity": "X", "involvement_type": "一体化方案提供商"}
    errors = fc.run_format_checks([rec])
    assert not any("参与方式不合法" in msg for msg in errors)
