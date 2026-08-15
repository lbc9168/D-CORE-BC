"""gold_loader：文章级 status + projects[]（每项目 entities[{name,involvement_type}]）的 Pydantic 校验。"""
import pytest
from eval import gold_loader

def _write(tmp_path, lines):
    p = tmp_path / "gold.jsonl"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p

def test_load_valid_multiproject(tmp_path):
    p = _write(tmp_path, [
        '{"source_link":"http://a","status":"valid","projects":[{"project_name":"A","category":"光伏","entities":[{"name":"中国电建","involvement_type":"EPC总承包"}]}]}',
        '{"source_link":"http://b","status":"irrelevant","projects":[]}',
    ])
    recs = gold_loader.load(p)
    assert len(recs) == 2
    assert recs[0]["status"] == "valid"
    assert recs[0]["projects"][0]["entities"][0]["name"] == "中国电建"
    assert recs[1]["projects"] == []

def test_load_rejects_bad_status_with_lineno(tmp_path):
    p = _write(tmp_path, [
        '{"source_link":"http://a","status":"valid","projects":[]}',
        '{"source_link":"http://b","status":"maybe","projects":[]}',
    ])
    with pytest.raises(ValueError) as ei:
        gold_loader.load(p)
    assert "第 2 行" in str(ei.value)

def test_load_rejects_entity_without_name(tmp_path):
    p = _write(tmp_path, [
        '{"source_link":"http://a","status":"valid","projects":[{"project_name":"A","entities":[{"involvement_type":"EPC总承包"}]}]}',
    ])
    with pytest.raises(ValueError):
        gold_loader.load(p)

def test_load_accepts_chinese_unnamed(tmp_path):
    p = tmp_path / "gold.jsonl"
    p.write_text('{"source_link":"http://a","status":"chinese_unnamed","projects":[{"project_name":"A","category":"光伏","entities":[]}]}', encoding="utf-8")
    from eval import gold_loader
    recs = gold_loader.load(p)
    assert recs[0]["status"] == "chinese_unnamed"

def test_load_accepts_optional_taxonomy_labels_without_breaking_old_gold(tmp_path):
    p = _write(tmp_path, [
        '{"source_link":"http://a","status":"valid","projects":[{"project_name":"A","category":"光伏","generation_sources":["光伏"],"storage_status":"confirmed","storage_power_mw":50,"storage_energy_mwh":100}]}',
        '{"source_link":"http://b","status":"valid","projects":[{"project_name":"B","category":"风电"}]}',
    ])
    recs = gold_loader.load(p)
    assert recs[0]["projects"][0]["generation_sources"] == ["光伏"]
    assert recs[1]["projects"][0]["generation_sources"] is None
