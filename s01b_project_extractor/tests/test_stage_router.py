"""StageRouter + build_stage_router_from_env 测试：按 stage 分派到不同 provider，全用 stub，不调真实 API。"""
from s01b_project_extractor.llm.client import (
    StageRouter,
    _parse_stage_models,
    _parse_stage_providers,
    build_stage_router_from_env,
)


class _StubClient:
    """记录被调 stage 的桩 client，返回标记自己 provider 的结果。"""
    def __init__(self, tag): self.tag = tag; self.calls = []
    def complete(self, prompt, *, stage, json_mode=True):
        self.calls.append((stage, json_mode)); return {"who": self.tag, "stage": stage}


def test_router_dispatches_by_stage():
    gem, ds = _StubClient("gemini"), _StubClient("deepseek")
    router = StageRouter(default=gem, by_stage={"s3": ds, "s4": ds, "s5": ds})
    assert router.complete("p", stage="s1")["who"] == "gemini"
    assert router.complete("p", stage="s2")["who"] == "gemini"
    assert router.complete("p", stage="s3")["who"] == "deepseek"
    assert router.complete("p", stage="s5")["who"] == "deepseek"
    assert router.complete("p", stage="sX")["who"] == "gemini"  # 未知 stage 落默认


def test_router_passes_stage_and_json_mode_through():
    gem = _StubClient("gemini")
    StageRouter(default=gem, by_stage={}).complete("p", stage="s1", json_mode=False)
    assert gem.calls == [("s1", False)]


def test_parse_stage_providers():
    assert _parse_stage_providers("s3:deepseek, s4:deepseek , s5:deepseek") == {
        "s3": "deepseek", "s4": "deepseek", "s5": "deepseek"}
    assert _parse_stage_providers("") == {}
    assert _parse_stage_providers("garbage,no-colon") == {}
    assert _parse_stage_models("s1:gpt-5-nano,s2:gpt-5.4-nano") == {
        "s1": "gpt-5-nano", "s2": "gpt-5.4-nano"}


def test_build_router_from_env_parses_overrides(monkeypatch):
    built = {}
    def _fake(cfg): c = _StubClient(cfg.provider); built[cfg.provider] = c; return c
    monkeypatch.setenv("S01B_PROVIDER", "gemini")
    monkeypatch.setenv("S01B_STAGE_PROVIDERS", "s3:deepseek,s4:deepseek,s5:deepseek")
    router = build_stage_router_from_env(_client_factory=_fake)
    assert router.complete("p", stage="s1")["who"] == "gemini"
    assert router.complete("p", stage="s2")["who"] == "gemini"
    assert router.complete("p", stage="s3")["who"] == "deepseek"
    assert set(built) == {"gemini", "deepseek"}  # 每个 provider 仅建一个 client（复用）


def test_build_router_no_overrides_single_provider(monkeypatch):
    monkeypatch.setenv("S01B_PROVIDER", "gemini")
    monkeypatch.delenv("S01B_STAGE_PROVIDERS", raising=False)
    router = build_stage_router_from_env(_client_factory=lambda cfg: _StubClient(cfg.provider))
    assert router.complete("p", stage="s1")["who"] == "gemini"
    assert router.complete("p", stage="s3")["who"] == "gemini"  # 无 override → 全默认


def test_build_router_supports_stage_model_overrides(monkeypatch):
    """同一 OpenAI provider 可按阶段选择两个 Nano 模型，并与 DeepSeek 混合。"""
    built = {}

    def _fake(cfg):
        client = _StubClient(f"{cfg.provider}/{cfg.model}")
        built[(cfg.provider, cfg.model)] = client
        return client

    monkeypatch.setenv("GPT_API_KEY", "test-only")
    monkeypatch.setenv("DS_API_KEY", "test-only")
    monkeypatch.setenv("DS_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("S01B_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5-nano")
    monkeypatch.setenv("S01B_STAGE_PROVIDERS", "s34:deepseek")
    monkeypatch.setenv("S01B_STAGE_MODELS", "s2:gpt-5.4-nano")

    router = build_stage_router_from_env(_client_factory=_fake)
    assert router.complete("p", stage="s1")["who"] == "openai/gpt-5-nano"
    assert router.complete("p", stage="s2")["who"] == "openai/gpt-5.4-nano"
    assert router.complete("p", stage="s34")["who"] == "deepseek/deepseek-v4-flash"
    assert set(built) == {
        ("openai", "gpt-5-nano"),
        ("openai", "gpt-5.4-nano"),
        ("deepseek", "deepseek-v4-flash"),
    }
