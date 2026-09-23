"""S5 阶段测试：摘要写入与阶段纠偏，角色建议不得绕过证据门控。"""
from s01b_project_extractor.stages.s5_summary import apply_s5


def test_apply_s5_sets_summary():
    """S5 摘要应写入 project["project_summary"]。"""
    p = {"common_info": {"Project Stage": "在建"}}
    apply_s5(p, {"project_summary": "中国能建在肯尼亚建100MW光伏，在建。", "corrections": []})
    assert "肯尼亚" in p["project_summary"]


def test_apply_s5_preserves_stage_even_with_high_conf_valid_correction():
    """高置信度不授予S5修改结构化事实的权限。"""
    p = {"common_info": {"Project Stage": "投入运营"}}
    apply_s5(p, {"project_summary": "x", "corrections": [{"field": "project_stage", "suggested_value": "竣工", "confidence": 0.95}]})
    assert p["common_info"]["Project Stage"] == "投入运营"
    assert p["s5_ignored_corrections"][0]["suggested_value"] == "竣工"


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
    """S5 返回空 dict 时保留结构化事实并生成确定性摘要。"""
    p = {"common_info": {"Project Stage": "签约"}}
    apply_s5(p, {})
    assert p["common_info"]["Project Stage"] == "签约"
    assert "阶段：签约" in p["project_summary"]
    assert p["summary_warning"]


def test_apply_s5_no_summary_when_empty_string():
    """空摘要响应降级并保留诊断。"""
    p = {"common_info": {}, "project_summary": "已有摘要"}
    apply_s5(p, {"project_summary": "   ", "corrections": []})
    assert p["summary_warning"]


# ── involvement_type 纠偏用例 ──

def test_apply_s5_ignores_high_conf_valid_involvement():
    """即使高置信且枚举合法，S5 也不得覆盖已经过证据门控的实体角色。"""
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
        assert ent["Type of involvement of Chinese Entities"] == "EPC总承包"


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


def test_summary_prompt_has_no_candidate_or_unvalidated_raw_facts():
    from s01b_project_extractor.prompts.s5_summary import build_s5_prompt
    prompt = build_s5_prompt({"common_info": {"Project Name": "测试风电项目", "Capacity (MW)": "999MW", "capacity_mw_parsed": None}, "entity_candidates": [{"Implementing Chinese Entity": "未核实企业"}]})
    assert "999MW" not in prompt
    assert "未核实企业" not in prompt


def test_s5_malformed_optional_summary_preserves_structured_facts():
    project = {"common_info": {"Project Stage": "在建"}, "project_summary": "已有摘要"}
    apply_s5(project, {"project_summary": {"text": "竣工"}, "corrections": [None]})
    assert project["common_info"]["Project Stage"] == "在建"
    assert "阶段：在建" in project["project_summary"]
    assert project["summary_warning"]
    assert project["summary_validation_error"]


import pytest
from copy import deepcopy
from s01b_project_extractor.llm.client import LLMResponseParseError


@pytest.mark.parametrize("failure", [RuntimeError("provider unavailable"),
    LLMResponseParseError("bad json", "{broken"), TimeoutError("timeout")])
def test_optional_s5_failure_keeps_facts_and_records_diagnostic(failure):
    from s01b_project_extractor.stages.s5_summary import run_s5
    project = {"common_info": {"Project Name": "海角风电项目", "Project Stage": "在建", "capacity_mw_parsed": 9},
               "chinese_entities": [{"Implementing Chinese Entity": "金风科技", "Type of involvement of Chinese Entities": "Unknown"}]}
    original = deepcopy(project)
    class FailingLLM:
        def complete(self, *args, **kwargs):
            raise failure
    apply_s5(project, run_s5(project, llm=FailingLLM()))
    assert project["common_info"] == original["common_info"]
    assert project["chinese_entities"] == original["chinese_entities"]
    assert "9MW" in project["project_summary"]
    assert "具体角色未明确" in project["project_summary"]
    assert project["summary_warning"]["error_type"] == type(failure).__name__
    if isinstance(failure, LLMResponseParseError):
        assert project["summary_warning"]["response"] == "{broken"


def test_s5_does_not_hide_programming_errors():
    from s01b_project_extractor.stages.s5_summary import run_s5
    class BuggyLLM:
        def complete(self, *args, **kwargs):
            raise TypeError("implementation bug")
    with pytest.raises(TypeError, match="implementation bug"):
        run_s5({"common_info": {}}, llm=BuggyLLM())


@pytest.mark.parametrize("bad", [[], {"project_summary": []}, {"project_summary": ""}])
def test_s5_bad_shape_uses_structured_summary(bad):
    project = {"common_info": {"Project Name": "测试项目", "capacity_mw_parsed": 8}}
    apply_s5(project, bad)
    assert "8MW" in project["project_summary"]
    assert project["summary_warning"]["response"] == bad
