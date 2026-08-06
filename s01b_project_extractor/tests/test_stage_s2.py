"""S2 阶段测试：枚举校验与中文名校验 + run_s2 融资默认值。"""
from s01b_project_extractor.stages.s2_basic import validate_s2, run_s2


class _StubLLM:
    """按预设返回固定 S2 输出的桩 LLM。"""
    def __init__(self, payload): self._payload = payload
    def complete(self, prompt, *, stage, json_mode=True):
        return self._payload


def test_run_s2_defaults_financing_model_unknown():
    """S2 未给 Financing Model 时默认 Unknown（与 gold 对齐，S3 后覆盖）。"""
    llm = _StubLLM({"projects": [{"common_info": {"Project Name": "肯尼亚100MW光伏项目", "Category": "光伏"}}]})
    projs = run_s2("t", "c", "2025-01-01", llm=llm)
    assert projs[0]["common_info"]["Financing Model"] == "Unknown"


def test_run_s2_keeps_existing_financing_model():
    """S2 已给出 Financing Model 时不覆盖。"""
    llm = _StubLLM({"projects": [{"common_info": {"Project Name": "X项目", "Financing Model": "信贷融资"}}]})
    projs = run_s2("t", "c", "2025-01-01", llm=llm)
    assert projs[0]["common_info"]["Financing Model"] == "信贷融资"


def test_validate_s2_flags_bad_category():
    errs = validate_s2([{"common_info": {"Category": "太阳能", "Project Stage": "在建", "Project Name": "肯尼亚光伏项目"}}])
    assert any("分类不合法" in m for m in errs)


def test_validate_s2_flags_non_chinese_name():
    errs = validate_s2([{"common_info": {"Category": "光伏", "Project Stage": "在建", "Project Name": "Kenya Solar"}}])
    assert any("名称未含中文" in m for m in errs)


def test_validate_s2_accepts_good():
    assert validate_s2([{"common_info": {"Category": "光伏", "Project Stage": "在建", "Project Name": "肯尼亚100MW光伏项目"}}]) == []


def test_validate_s2_empty_list_returns_empty():
    assert validate_s2([]) == []


def test_run_s2_defaults_in_scope_true_when_absent():
    """S2 未给 in_scope 时默认 True（不误排除）。"""
    from s01b_project_extractor.stages.s2_basic import run_s2
    class _Stub:
        def complete(self, prompt, *, stage, json_mode=True):
            return {"projects": [{"common_info": {"Project Name": "肯尼亚100MW光伏项目", "Category": "光伏"}}]}
    projs = run_s2("t", "c", "2025-01-01", llm=_Stub())
    assert projs[0]["in_scope"] is True
    assert projs[0]["out_of_scope_reason"] == ""


def test_run_s2_passes_through_in_scope_false():
    """S2 判 in_scope=false（油气/民用误入）时原样透传，带 reason。"""
    from s01b_project_extractor.stages.s2_basic import run_s2
    class _Stub:
        def complete(self, prompt, *, stage, json_mode=True):
            return {"projects": [{
                "common_info": {"Project Name": "某LNG接收站", "Category": "综合能源"},
                "in_scope": False, "out_of_scope_reason": "油气接收储运，非发电/制造/输变电",
            }]}
    projs = run_s2("t", "c", "2025-01-01", llm=_Stub())
    assert projs[0]["in_scope"] is False
    assert "油气" in projs[0]["out_of_scope_reason"]


def test_s2_prompt_tightens_integrated_energy_and_has_in_scope_output():
    """S2 prompt 收紧综合能源定义并要求输出 in_scope/out_of_scope_reason。"""
    from s01b_project_extractor.prompts.s2_basic import build_s2_prompt
    p = build_s2_prompt("t", "c", "2026-01-01")
    assert "in_scope" in p and "out_of_scope_reason" in p
    assert "综合能源仅指" in p or "综合能源只指" in p
    assert "不是综合能源" in p or "不应成为项目" in p
    assert "可再生能源" not in p


def test_run_s2_normalizes_energy_facts_without_overwriting_legacy_category():
    """能源事实应结构化进入 shadow 键，但 legacy Category 必须原样保留作对照。"""
    llm = _StubLLM({"projects": [{"common_info": {
        "Project Name": "意大利光储项目",
        "Category": "综合能源",
        "Generation Sources": ["光伏"],
        "Storage Status": "confirmed",
        "Storage Power": "20MW",
        "Storage Energy": "40MWh",
        "Energy Composition Evidence": "光伏电站配套20MW/40MWh储能",
    }}]})
    project = run_s2("t", "c", "2026-01-01", llm=llm)[0]
    common = project["common_info"]
    assert common["Category"] == "综合能源"
    assert common["generation_sources_json"] == '["光伏"]'
    assert common["storage_status_normalized"] == "confirmed"
    assert common["storage_power_mw_raw_phrase"] == "20MW"
    assert common["storage_energy_mwh_raw_phrase"] == "40MWh"
    assert common["storage_power_mw_parsed"] == 20.0
    assert common["storage_energy_mwh_parsed"] == 40.0


def test_validate_s2_flags_invalid_energy_fact_enums():
    """S2 校验应拒绝把储能或未知字符串当作发电源，以及非法储能状态。"""
    errors = validate_s2([{"common_info": {
        "Category": "光伏", "Project Stage": "在建", "Project Name": "某光伏项目",
        "Generation Sources": ["光伏", "储能"], "Storage Status": "active",
    }}])
    assert any("发电源不合法" in message for message in errors)
    assert any("储能状态不合法" in message for message in errors)
