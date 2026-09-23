"""省份缓存必须同时绑定国家和 location，不能只信任可重用的 canonical ID。"""

import json

from s03_data_exporter.province_extractor import extract_provinces


def test_legacy_project_id_cache_migrates_to_location_key(tmp_path):
    """旧 ID 缓存首次使用后写入位置键和 ID 指纹。"""
    cache_path = tmp_path / "province_cache.json"
    cache_path.write_text(json.dumps({"MYS-PV-0001": "Selangor"}), encoding="utf-8")
    record = {
        "project_id": "MYS-PV-0001", "location": "雪兰莪州",
        "country": "马来西亚", "country_iso": "MYS",
    }
    assert extract_provinces([record], cache_path, "unused") == {"MYS-PV-0001": "Selangor"}
    migrated = json.loads(cache_path.read_text(encoding="utf-8"))
    assert migrated["location::MYS::雪兰莪州"] == "Selangor"
    assert migrated["fingerprint::MYS-PV-0001"] == "location::MYS::雪兰莪州"


def test_reused_project_id_does_not_reuse_old_province(tmp_path, monkeypatch):
    """同一 ID 的 location 改变后，旧省份值必须失效并等待重新提取。"""
    cache_path = tmp_path / "province_cache.json"
    cache_path.write_text(json.dumps({
        "MYS-PV-0001": "Selangor",
        "location::MYS::雪兰莪州": "Selangor",
        "fingerprint::MYS-PV-0001": "location::MYS::雪兰莪州",
    }), encoding="utf-8")
    monkeypatch.setenv("SKIP_PROVINCE_API", "1")
    changed = {
        "project_id": "MYS-PV-0001", "location": "砂拉越州",
        "country": "马来西亚", "country_iso": "MYS",
    }
    assert extract_provinces([changed], cache_path, "unused") == {"MYS-PV-0001": None}


import pytest
from types import SimpleNamespace
from unittest.mock import Mock
from s03_data_exporter import province_extractor as module


@pytest.mark.parametrize("bad", [{}, {"results": []}, {"results": [{"id": "A", "province": "Foo"}]},
    {"results": [{"id": "A", "province": []}, {"id": "B", "province": None}]},
    {"results": [{"id": "A", "province": None}, {"id": "A", "province": None}]}])
def test_bad_province_batch_is_retryable_without_partial_cache(tmp_path, monkeypatch, bad):
    client = Mock()
    client.models.generate_content.return_value = SimpleNamespace(text=json.dumps(bad))
    monkeypatch.setattr(module.genai, "Client", lambda **kw: client)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(module, "build_admin1_dict", lambda: {})
    monkeypatch.delenv("SKIP_PROVINCE_API", raising=False)
    records = [{"project_id": pid, "location": pid, "country_iso": "ARG", "country": "Argentina"} for pid in ("A", "B")]
    cache = tmp_path / "cache.json"
    report = {}
    assert extract_provinces(records, cache, "test", diagnostics=report) == {"A": None, "B": None}
    assert report["failed_ids"] == ["A", "B"]
    assert report["failures"][0]["attempts"] == 3
    assert json.loads(cache.read_text()) == {}
    client.models.generate_content.return_value = SimpleNamespace(text=json.dumps({"results": [{"id": "A", "province": "Foo"}, {"id": "B", "province": None}]}))
    assert extract_provinces(records, cache, "test", diagnostics=report) == {"A": "Foo", "B": None}
    assert report["failed_ids"] == []
    assert client.models.generate_content.call_count == 4


def test_province_network_failure_is_reported(tmp_path, monkeypatch):
    client = Mock()
    client.models.generate_content.side_effect = RuntimeError("service unavailable")
    monkeypatch.setattr(module.genai, "Client", lambda **kw: client)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(module, "build_admin1_dict", lambda: {})
    monkeypatch.delenv("SKIP_PROVINCE_API", raising=False)
    report = {}
    extract_provinces([{"project_id": "A", "location": "Foo", "country": "Argentina"}], tmp_path / "cache.json", "test", diagnostics=report)
    assert report["failed_ids"] == ["A"]
    assert "service unavailable" in report["failures"][0]["error"]
