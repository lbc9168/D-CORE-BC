"""测试能源事实与影子分类策略：储能不充当发电源，标签只来自 confirmed 事实。"""

import pytest

from s01b_project_extractor.energy_taxonomy import (
    EnergyFacts,
    aggregate_energy_facts,
    classify_energy,
    extract_energy_facts,
)


@pytest.mark.parametrize(
    ("sources", "storage_status", "category", "tags"),
    [
        (["光伏"], "confirmed", "光伏", ("光储", "含储能")),
        (["风电"], "confirmed", "风电", ("风储", "含储能")),
        (["风电", "光伏"], "confirmed", "综合能源", ("风光储", "含储能")),
        (["光伏", "火电"], "confirmed", "综合能源", ("光火储", "含储能")),
        ([], "confirmed", "储能", ("独立储能", "含储能")),
        (["光伏"], "planned", "光伏", ()),
        (["光伏"], "optional", "光伏", ()),
    ],
)
def test_classification_matrix(sources, storage_status, category, tags):
    """若分类策略把储能算作发电源或给计划项目打 confirmed 标签，本测试失败。"""
    result = classify_energy(sources, storage_status)
    assert result.proposed_category == category
    assert result.technology_tags == tags
    assert result.needs_review is False


def test_invalid_or_missing_generation_facts_require_review():
    """无发电源且没有 confirmed 储能时不得硬分到任一生产类别。"""
    result = classify_energy([], "not_mentioned")
    assert result.proposed_category is None
    assert result.needs_review is True
    assert result.review_reason == "missing_generation_source"


def test_aggregate_uses_source_union_and_storage_precedence():
    """后来的 not_mentioned 不得降级既有 confirmed 储能证据。"""
    facts = aggregate_energy_facts(
        [
            EnergyFacts(("光伏",), "confirmed", evidence="光伏配套储能"),
            EnergyFacts(("风电",), "not_mentioned", evidence="风电二期"),
        ]
    )
    assert facts.generation_sources == ("光伏", "风电")
    assert facts.storage_status == "confirmed"
    assert facts.evidence == "光伏配套储能\n风电二期"


def test_extract_confirmed_storage_pair_and_generation_source():
    """明确光伏配储且披露 MW/MWh 时应产生完整事实。"""
    facts = extract_energy_facts("建设100MW光伏电站，配套20MW/40MWh储能系统")
    assert facts.generation_sources == ("光伏",)
    assert facts.storage_status == "confirmed"
    assert facts.storage_power_mw_raw == "20MW"
    assert facts.storage_energy_mwh_raw == "40MWh"
    assert facts.storage_power_mw == 20.0
    assert facts.storage_energy_mwh == 40.0


def test_aggregate_prefers_most_informative_event_with_same_status():
    """同为 confirmed 时，空容量事件不能遮蔽另一事件的明确储能容量。"""
    facts = aggregate_energy_facts([
        EnergyFacts(("光伏",), "confirmed", evidence="已配储"),
        EnergyFacts(("光伏",), "confirmed", "50MW", "100MWh", 50.0, 100.0, "储能50MW/100MWh"),
    ])
    assert (facts.storage_power_mw, facts.storage_energy_mwh) == (50.0, 100.0)


@pytest.mark.parametrize(
    ("text", "expected_status"),
    [
        ("项目预留20MW/40MWh储能空间", "planned"),
        ("招标方案可选配20MW/40MWh储能", "optional"),
        ("项目年发电量为108000MWh", "not_mentioned"),
        ("电池工厂年产能20GWh", "not_mentioned"),
        ("企业向该光伏电站出口battery设备", "unclear"),
    ],
)
def test_non_confirmed_context_never_fills_storage_metrics(text, expected_status):
    """计划、可选、年发电量、工厂产能和设备出口不得污染 confirmed 储能指标。"""
    facts = extract_energy_facts(text)
    assert facts.storage_status == expected_status
    assert facts.storage_power_mw is None
    assert facts.storage_energy_mwh is None


def test_invalid_generation_source_is_rejected():
    """储能、综合能源等非发电源不得混入 generation_sources。"""
    with pytest.raises(ValueError, match="invalid generation source"):
        classify_energy(["光伏", "储能"], "confirmed")
