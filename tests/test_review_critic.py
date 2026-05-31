"""critic：给定原文+项目提取，产出逐字段 verdict（pydantic 校验）。"""
import pytest
from pydantic import ValidationError
from review import critic

class _MockLLM:
    def __init__(self, payload): self._p = payload
    def complete(self, prompt, *, stage, json_mode=True): return self._p

def test_critic_parses_verdicts():
    payload = {"verdicts": [
        {"field": "category", "verdict": "wrong", "suggested_value": "风电", "confidence": 0.9, "evidence": "原文称风电场"},
        {"field": "status", "verdict": "unsupported", "suggested_value": "irrelevant", "confidence": 0.85, "evidence": "实为油气项目"}]}
    r = critic.review_project(article={"title":"t","content":"正文"}, project={"category":"光伏"},
                              suspicious=["category"], llm=_MockLLM(payload))
    assert len(r) == 2
    assert r[0].field == "category" and r[0].verdict == "wrong" and r[0].suggested_value == "风电"

def test_critic_rejects_malformed_output():
    payload = {"verdicts": [{"field": "category", "verdict": "BOGUS", "confidence": 2.0}]}
    with pytest.raises(ValidationError):
        critic.review_project(article={"title":"t","content":"c"}, project={"category":"光伏"},
                              suspicious=["category"], llm=_MockLLM(payload))

def test_critic_nondict_returns_empty():
    r = critic.review_project(article={"title":"t","content":"c"}, project={"category":"光伏"},
                              suspicious=["category"], llm=_MockLLM("not a dict"))
    assert r == []
