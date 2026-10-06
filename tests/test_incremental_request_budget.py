"""Real caps protect retries/resumes/concurrency before the SDK network boundary."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from incremental_request_budget import BudgetStop, RequestLedger, audit_pipeline_requests


def ledger(path, **kwargs):
    options = dict(raw_ids=[1, 2], limits={"extraction": 3}, per_article=2,
                   token_stop=100, allowed={("gemini", "test")})
    return RequestLedger(path, **(options | kwargs))


def call(l, ids, invoke, **kw):
    return l.invoke("extraction", ids, "gemini", "test", {"contents": "test"}, invoke, stage="s1", **kw)


def response():
    return SimpleNamespace(usage_metadata=SimpleNamespace(model_dump=lambda **kw: {"total_token_count": 3}),
                           model_dump=lambda **kw: {"text": "saved"})


def test_failures_and_resume_share_per_article_cap(tmp_path):
    def failed():
        raise TimeoutError("offline")
    with pytest.raises(TimeoutError):
        call(ledger(tmp_path), [1], failed)
    call(ledger(tmp_path), [1], response)
    with pytest.raises(BudgetStop, match="per-article"):
        call(ledger(tmp_path), [1], lambda: pytest.fail("must not reach SDK"), allow_cache=False)
    assert ledger(tmp_path).usage_report()["requests"] == 2


def test_concurrent_pair_requests_cannot_overspend(tmp_path):
    l = ledger(tmp_path)
    def invoke(_):
        try:
            call(l, [1, 2], response)
            return "sent"
        except BudgetStop:
            return "blocked"
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(invoke, range(8))).count("sent") == 2
    assert len(l.calls) == 2


def test_cache_reuse_and_budget_cannot_be_reset(tmp_path):
    l = ledger(tmp_path)
    call(l, [1], response)
    l = ledger(tmp_path)
    assert call(l, [1], lambda: pytest.fail("cache hit must not call SDK"), restore=lambda d: d) == {"text": "saved"}
    assert len(l.calls) == 1 and len(l.reuses) == 1
    with pytest.raises(ValueError, match="different budget"):
        ledger(tmp_path, per_article=3)
    with pytest.raises(BudgetStop, match="scope"):
        call(l, [3], response)


def test_token_threshold_stops_subsequent_request(tmp_path):
    l = ledger(tmp_path, token_stop=3)
    call(l, [1], response)
    with pytest.raises(BudgetStop, match="token"):
        call(l, [2], lambda: pytest.fail("token threshold must stop SDK"))


def test_sdk_instrumentation_attributes_cached_writer_and_reuses_response(tmp_path, monkeypatch):
    from google import genai
    from google.genai import models, types
    from s01b_project_extractor.llm.client import LLMClient, LLMConfig
    from s01b_project_extractor.tools import project_extractor_tool as writer
    l = ledger(tmp_path, limits={"extraction": 3, "translation": 3, "dedup": 3})
    sdk_calls = []
    def sdk(self, **kwargs):
        sdk_calls.append(kwargs)
        return types.GenerateContentResponse(
            candidates=[types.Candidate(content=types.Content(parts=[types.Part(text='{"ok": true}')]))],
            usage_metadata=types.GenerateContentResponseUsageMetadata(total_token_count=3),
        )
    monkeypatch.setattr(models.Models, "generate_content", sdk)
    with audit_pipeline_requests(l, [{"id": 1, "url": "https://test/1"}]):
        assert writer._load_recorded_results(None).get("https://test/1") is None
        client = genai.Client(api_key="offline-test")
        llm = LLMClient(LLMConfig(model="test"), _client=client)
        assert llm.complete("frozen extraction", stage="s1") == {"ok": True}
        client.models.generate_content(model="test", contents="name translation")
        assert [c["category"] for c in l.calls] == ["extraction", "translation"]
        assert all(c["raw_ids"] == [1] for c in l.calls)
        assert llm.complete("frozen extraction", stage="s1") == {"ok": True}
        assert len(sdk_calls) == 2 and len(l.calls) == 2 and len(l.reuses) == 1
        with pytest.raises(BudgetStop, match="frozen batch"):
            writer._load_recorded_results(None).get("https://outside/")
        client.close()
