import json
from s01b_project_extractor.tools import project_extractor_tool as pe

class _FakeModels:
    def __init__(self, payload): self._payload = payload
    def generate_content(self, *, model, contents, config):
        class R: text = json.dumps(self._payload)
        return R()

class _FakeClient:
    def __init__(self, payload): self.models = _FakeModels(payload)

def test_extract_one_returns_dict():
    client = _FakeClient({"status": "irrelevant", "analysis": {}})
    out = pe.extract_one("标题", "正文", "2024-01-01", client=client)
    assert out == {"status": "irrelevant", "analysis": {}}

def test_extract_one_raises_on_persistent_failure(monkeypatch):
    monkeypatch.setattr(pe.time, "sleep", lambda s: None)  # don't actually sleep
    class _Boom:
        def generate_content(self, **k): raise ValueError("boom")
    class _C: models = _Boom()
    import pytest
    with pytest.raises(RuntimeError):
        pe.extract_one("t", "c", "d", client=_C())
