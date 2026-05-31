"""LLM client 单测：JSON 解析、重试、provider 切换，全部用注入的 fake provider，不调真实 API。"""
import json
import pytest
from s01b_project_extractor.llm.client import LLMClient, LLMConfig


class _FakeGenai:
    """模拟 google.genai client：按预设脚本依次返回/抛错。"""
    def __init__(self, script):
        self._script = list(script)
        self.calls = 0
        self.models = self

    def generate_content(self, *, model, contents, config):
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return type("Resp", (), {"text": item})()


def test_complete_parses_json():
    fake = _FakeGenai(['{"status": "valid_project"}'])
    c = LLMClient(LLMConfig(provider="gemini", model="m"), _client=fake)
    out = c.complete("p", stage="s1", json_mode=True)
    assert out == {"status": "valid_project"}
    assert fake.calls == 1


def test_complete_retries_then_succeeds():
    fake = _FakeGenai([ValueError("boom"), '{"ok": 1}'])
    c = LLMClient(LLMConfig(provider="gemini", model="m", max_retries=3, backoff_base=0), _client=fake)
    out = c.complete("p", stage="s1", json_mode=True)
    assert out == {"ok": 1}
    assert fake.calls == 2


def test_complete_raises_after_max_retries():
    fake = _FakeGenai([ValueError("a"), ValueError("b"), ValueError("c")])
    c = LLMClient(LLMConfig(provider="gemini", model="m", max_retries=3, backoff_base=0), _client=fake)
    with pytest.raises(RuntimeError, match="s1.*失败"):
        c.complete("p", stage="s1", json_mode=True)
    assert fake.calls == 3


def test_bad_json_is_retried_and_raises():
    fake = _FakeGenai(["not json", "still not", "{nope}"])
    c = LLMClient(LLMConfig(provider="gemini", model="m", max_retries=3, backoff_base=0), _client=fake)
    with pytest.raises(RuntimeError):
        c.complete("p", stage="s1", json_mode=True)
