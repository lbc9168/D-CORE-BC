"""gold_loader：文章级 status + projects[]（每项目 entities[{name,involvement_type}]）的 Pydantic 校验。"""
import json

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


def test_load_accepts_optional_studio_projection_metadata(tmp_path):
    """Accept Studio split, risk, and field-eligibility metadata while retaining legacy fields."""
    p = _write(tmp_path, [
        '{"source_link":"http://a","article_id":"S001","status":"valid","dataset_split":"development","risk_tags":["capacity"],"difficulty":{"score":10},"projects":[{"capacity_mw":12,"country":"肯尼亚","project_developer":"中国电建","planned_start_date":"2026-01-01","planned_complete_date":"2027-01-01","investment_date":"2025-01-01","contract_amount_usd":3,"financing_amount_usd":4,"project_summary":"x","field_eligibility":{"capacity_mw":true}}]}',
    ])

    record = gold_loader.load(p)[0]

    assert record["dataset_split"] == "development"
    assert record["risk_tags"] == ["capacity"]
    assert record["difficulty"] == {"score": 10}
    assert record["projects"][0]["field_eligibility"] == {"capacity_mw": True}
    assert record["projects"][0]["project_developer"] == "中国电建"


def test_old_gold_defaults_to_empty_project_eligibility_mapping(tmp_path):
    """Legacy gold rows retain an empty mapping rather than inheriting a rich projection state."""
    record = gold_loader.load(_write(tmp_path, [
        '{"source_link":"http://legacy","status":"valid","projects":[{"project_name":"A","category":"光伏"}]}'
    ]))[0]

    assert record["projects"][0]["field_eligibility"] == {}


def test_sampling_provenance_accepts_legacy_shape_and_preserves_new_verification_fields(tmp_path):
    """Keep old projections loadable while retaining Studio's explicit provenance verification state."""
    legacy = {
        "sampling_seed": 17,
        "inclusion_reasons": ["legacy_quota"],
        "original_category_strata": ["光伏"],
        "risk_tags": [],
        "negative_proxy": False,
    }
    verified = {
        **legacy,
        "verified": True,
        "verification_source": "operator_review",
    }
    p = _write(
        tmp_path,
        [
            '{"source_link":"http://legacy","status":"irrelevant","projects":[],"sampling_provenance":'
            + json.dumps(legacy, ensure_ascii=False)
            + "}",
            '{"source_link":"http://verified","status":"irrelevant","projects":[],"sampling_provenance":'
            + json.dumps(verified, ensure_ascii=False)
            + "}",
        ],
    )

    records = gold_loader.load(p)

    assert records[0]["sampling_provenance"]["verified"] is None
    assert records[1]["sampling_provenance"]["verified"] is True
    assert records[1]["sampling_provenance"]["verification_source"] == "operator_review"
