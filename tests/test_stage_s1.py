"""S1 阶段测试：run_s1 透传、validate_s1 枚举校验。"""
import pytest
from s01b_project_extractor.stages.s1_relevance import run_s1, validate_s1


class _FakeLLM:
    def __init__(self, payload): self._p = payload
    def complete(self, prompt, *, stage, json_mode=True):
        assert stage == "s1"
        return self._p


def test_run_s1_returns_status():
    out = run_s1("t", "c", "2025-01-01", llm=_FakeLLM({"status": "irrelevant", "reasoning": "油气项目"}))
    assert out["status"] == "irrelevant"


def test_validate_s1_accepts_valid_status():
    assert validate_s1({"status": "valid_project"}) == []


def test_validate_s1_rejects_bad_status():
    errs = validate_s1({"status": "maybe"})
    assert errs and "status 非法" in errs[0]


def test_s1_prompt_covers_three_scopes():
    """S1 第一步范围必须显式覆盖三类：发电(含火电)、新能源制造业、输变电/电网。"""
    from s01b_project_extractor.prompts.s1_relevance import build_s1_prompt
    p = build_s1_prompt("标题", "正文", "2026-01-01")
    assert "新能源制造业" in p, "S1 范围须显式纳入新能源制造业（组件厂/电池厂）"
    assert "输变电" in p or "电网" in p, "S1 范围须显式纳入输变电/电网工程"
    assert ("组件厂" in p) or ("电池厂" in p) or ("多晶硅" in p), "S1 须给制造业的可识别线索"


def test_s1_prompt_keeps_exclusions_and_recall_first():
    """排除项与 recall-first 倾向保留。"""
    from s01b_project_extractor.prompts.s1_relevance import build_s1_prompt
    p = build_s1_prompt("t", "c", "2026-01-01")
    assert "油气" in p and "采矿" in p
    assert "存疑" in p
