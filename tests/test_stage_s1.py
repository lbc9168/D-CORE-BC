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
