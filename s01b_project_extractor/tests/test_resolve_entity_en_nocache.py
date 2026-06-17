"""_resolve_entity_en：翻译失败（如 503）必须回退中文，且**不得**把失败结果写进缓存（防污染共享 company_name_cache）。"""
from s01b_project_extractor.tools import project_extractor_tool as t


class _FakeModels:
    def generate_content(self, **k):
        raise RuntimeError("503 UNAVAILABLE")


class _FakeClient:
    models = _FakeModels()


def test_cache_hit_returns_without_calling_client(monkeypatch):
    monkeypatch.setattr(t, "_name_cache", {"已存在公司": {"en": "Existing Co"}})
    assert t._resolve_entity_en("已存在公司", client=None) == "Existing Co"


def test_failure_falls_back_and_does_not_cache(monkeypatch):
    monkeypatch.setattr(t, "_name_cache", {})
    saved = {"called": False}
    monkeypatch.setattr(t, "_save_name_cache", lambda c: saved.update(called=True))
    out = t._resolve_entity_en("全新未知公司ZZZ", client=_FakeClient())
    assert out == "全新未知公司ZZZ"                 # 回退中文
    assert "全新未知公司ZZZ" not in t._name_cache    # 未污染内存缓存
    assert saved["called"] is False                # 未写回文件
