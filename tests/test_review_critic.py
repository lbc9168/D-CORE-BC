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


def test_verdict_accepts_exclude_value():
    """Verdict.verdict 新增合法取值 'exclude'。"""
    from review.critic import Verdict
    v = Verdict(field="status", verdict="exclude", suggested_value=None, confidence=1.0, evidence="油气开采，非发电")
    assert v.verdict == "exclude"


def test_critic_parses_exclude_verdict():
    """critic 能解析 verdict=exclude 的输出。"""
    from review import critic
    class _Mock:
        def complete(self, prompt, *, stage, json_mode=True):
            return {"verdicts": [{"field": "status", "verdict": "exclude",
                                  "suggested_value": None, "confidence": 1.0,
                                  "evidence": "实为油气管道，非发电/制造/输变电"}]}
    r = critic.review_project(article={"title": "t", "content": "c"},
                              project={"category": "综合能源"}, suspicious=["status"], llm=_Mock())
    assert len(r) == 1 and r[0].verdict == "exclude"


def test_critic_prompt_injects_enums_and_scope():
    """critic prompt 注入合法 category/involvement 枚举，并用三类范围（不提'可再生能源'窄词）。"""
    import bri_enums
    from review.critic import _build_critic_prompt
    p = _build_critic_prompt(article={"title": "t", "content": "正文"},
                             project={"category": "光伏"}, suspicious=["involvement_type"])
    assert "EPC总承包" in p
    assert "光伏" in p and "火电" in p
    assert "可再生能源" not in p
    assert ("发电" in p) and ("输变电" in p)
    assert "exclude" in p
