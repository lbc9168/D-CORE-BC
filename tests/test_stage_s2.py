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
