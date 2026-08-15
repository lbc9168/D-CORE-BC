"""LLM client 单测：JSON 解析、重试、provider 切换，全部用注入的 fake provider，不调真实 API。"""
import pytest
from s01b_project_extractor.llm.client import LLMClient, LLMConfig, _request_timeout_from_env


class _FakeGenai:
    """模拟 google.genai client：按预设脚本依次返回/抛错。"""
    def __init__(self, script):
        self._script = list(script)
        self.calls = 0
        self.models = self

    def generate_content(self, *, model, contents, config):
        self.calls += 1
        self.last_config = config
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


class _FakeOpenAI:
    """模拟 openai client：chat.completions.create 返回预设文本。"""
    def __init__(self, text):
        self._text = text
        self.chat = self
        self.completions = self
        self.calls = 0

    def create(self, *, model, messages, temperature=None, response_format=None, reasoning_effort=None):
        self.calls += 1
        self.last_kwargs = {
            "model": model,
            "temperature": temperature,
            "response_format": response_format,
            "reasoning_effort": reasoning_effort,
        }
        msg = type("Msg", (), {"content": self._text})()
        choice = type("Choice", (), {"message": msg})()
        return type("Resp", (), {"choices": [choice]})()


def test_deepseek_dispatch_parses_json():
    fake = _FakeOpenAI('{"a": 2}')
    c = LLMClient(LLMConfig(provider="deepseek", model="ds"), _client=fake)
    out = c.complete("p", stage="s2", json_mode=True)
    assert out == {"a": 2}
    assert fake.calls == 1


def test_openai_dispatch_uses_reasoning_effort_without_temperature():
    """OpenAI Nano 使用 JSON mode 与 reasoning effort，但不发送 sampling temperature。"""
    fake = _FakeOpenAI('{"a": 3}')
    cfg = LLMConfig(provider="openai", model="gpt-5.4-nano", temperature=None, reasoning_effort="none")
    out = LLMClient(cfg, _client=fake).complete("return JSON", stage="s2", json_mode=True)
    assert out == {"a": 3}
    assert fake.last_kwargs == {
        "model": "gpt-5.4-nano",
        "temperature": None,
        "response_format": {"type": "json_object"},
        "reasoning_effort": "none",
    }


def test_gemini_35_omits_deprecated_temperature():
    """Gemini 3.x generateContent 不再携带已废止的 sampling 参数。"""
    fake = _FakeGenai(['{"ok": true}'])
    cfg = LLMConfig(provider="gemini", model="gemini-3.5-flash-lite", temperature=0.14)
    out = LLMClient(cfg, _client=fake).complete("p", stage="s1", json_mode=True)
    assert out == {"ok": True}
    assert fake.last_config.temperature is None


def test_complete_plain_text_when_not_json_mode():
    fake = _FakeGenai(["hello world"])
    c = LLMClient(LLMConfig(provider="gemini", model="m"), _client=fake)
    out = c.complete("p", stage="s5", json_mode=False)
    assert out == "hello world"


def test_request_timeout_env_is_positive_and_fail_safe(monkeypatch):
    """单次请求超时允许实验覆盖，但非法值不能恢复为无限等待。"""
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECONDS", "45.5")
    assert _request_timeout_from_env() == 45.5
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECONDS", "invalid")
    assert _request_timeout_from_env() == 90.0
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECONDS", "0")
    assert _request_timeout_from_env() == 90.0
