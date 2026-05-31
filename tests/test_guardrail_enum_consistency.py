"""守护测试：guardrail 合法枚举集 == enums_mapping.json 对应枚举键集（直接读 JSON，全链漂移守护）。"""
import json
import pathlib

from s01b_project_extractor.guardrails import format_checks as fc
from s01b_project_extractor.guardrails import semantic_checks as sc

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_RAW = json.loads((_ROOT / "enums_mapping.json").read_text(encoding="utf-8"))


def test_format_checks_enums_match_json():
    assert fc.valid_categories_set() == set(_RAW["category"].keys())
    assert fc.valid_stages_set() == set(_RAW["project_stage"].keys())
    assert fc.valid_involvement_set() == set(_RAW["involvement_type"].keys())
    assert fc.valid_financing_set() == set(_RAW["financing_model"].keys())


def test_semantic_checks_enums_match_json():
    assert sc.valid_stages_set() == set(_RAW["project_stage"].keys())
    assert sc.valid_categories_set() == set(_RAW["category"].keys())
    assert sc.valid_involvement_set() == set(_RAW["involvement_type"].keys())


def test_new_enum_value_is_accepted():
    """'一体化方案提供商' 必须被 format 校验视为合法（回归 P0.1 漂移 bug）。"""
    rec = {"id": 1, "project_name": "测试项目", "country": "肯尼亚", "category": "光伏",
           "implementing_entity": "X", "involvement_type": "一体化方案提供商"}
    errors = fc.run_format_checks([rec])
    assert not any("参与方式不合法" in msg for msg in errors)
