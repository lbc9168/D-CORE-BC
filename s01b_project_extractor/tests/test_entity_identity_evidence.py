"""名称登记、文本身份与项目角色的边界回归；全部使用固定原文，无模型调用。"""
import pytest

from s01b_project_extractor.stages.entity_normalize import EntityResolution
from s01b_project_extractor.stages.s4_entity import (
    enforce_entity_role_evidence, normalize_and_verify_entities,
)


def _entity(name, quote, role="EPC总承包", anchor="赛尔吉利储能电站项目"):
    return {
        "Implementing Chinese Entity": name,
        "Type of involvement of Chinese Entities": role,
        "Participation Evidence": [{"role": role, "quote": quote, "project_anchor": anchor}],
    }


def test_real_chengjian_participation_retained_without_inventing_epc():
    quote = "由中国电建承建的乌兹别克斯坦赛尔吉利储能电站项目正式投运。"
    entity = _entity("中国电建", quote)
    original_evidence = list(entity["Participation Evidence"])
    enforce_entity_role_evidence([entity], "", quote, "赛尔吉利储能电站项目")
    assert entity["_participation_evidence_status"] == "verified"
    assert entity["Type of involvement of Chinese Entities"] == "Unknown"
    assert entity["Participation Evidence"] == original_evidence
    assert "角色证据不足" in entity["_participation_evidence_reasons"][0]


@pytest.mark.parametrize(("quote", "expected"), [
    ("赛尔吉利储能电站项目正式开工。中国电建承建该项目。", "verified"),
    ("赛尔吉利储能电站项目正式开工；中国电建承建该项目。", "missing_or_ambiguous"),
    ("赛尔吉利储能电站项目与纳沃伊光伏项目开工。中国电建承建该项目。", "missing_or_ambiguous"),
    ("赛尔吉利储能电站项目与纳沃伊光伏项目开工，中国电建承建该项目。", "missing_or_ambiguous"),
    ("赛尔吉利储能电站项目正式开工。中国电建承建纳沃伊光伏项目。", "missing_or_ambiguous"),
    ("赛尔吉利储能电站项目由甲公司承建，中国电建出席会议。", "missing_or_ambiguous"),
    ("中国电建并未参与赛尔吉利储能电站项目。", "missing_or_ambiguous"),
])
def test_limited_cross_sentence_binding(quote, expected):
    entity = _entity("中国电建", quote)
    enforce_entity_role_evidence([entity], "", quote, "赛尔吉利储能电站项目")
    assert entity["_participation_evidence_status"] == expected


def test_same_capacity_cannot_bind_another_project():
    quote = "中国电建承建布哈拉100MW光伏项目。"
    entity = _entity("中国电建", quote, anchor="布哈拉100MW光伏项目")
    enforce_entity_role_evidence([entity], "", quote, "纳沃伊100MW光伏项目")
    assert entity["_participation_evidence_status"] == "missing_or_ambiguous"


@pytest.mark.parametrize("capacity", ["100MW", "100兆瓦", "100MWh"])
def test_generic_capacity_anchor_cannot_bypass_project_binding(capacity):
    quote = f"中国电建承建布哈拉{capacity}光伏项目。"
    entity = _entity("中国电建", quote, anchor=f"{capacity}光伏项目")
    enforce_entity_role_evidence([entity], "", quote, f"纳沃伊{capacity}光伏项目")
    assert entity["_participation_evidence_status"] == "missing_or_ambiguous"


@pytest.mark.parametrize(("quote", "evidence", "expected"), [
    ("中国企业新辰能源承建纳沃伊光伏项目。", "中国企业新辰能源", "verified"),
    ("新辰能源是一家中国企业。", "新辰能源是一家中国企业。", "verified"),
    ("新辰能源承建纳沃伊光伏项目。", "中国企业新辰能源", "missing_or_ambiguous"),
    ("中国企业甲公司与新辰能源合作。", "中国企业甲公司与新辰能源合作。", "missing_or_ambiguous"),
    ("新辰能源不是中国企业。", "新辰能源不是中国企业。", "missing_or_ambiguous"),
    ("新辰能源承建中国企业的纳沃伊光伏项目。", "新辰能源承建中国企业的纳沃伊光伏项目。", "missing_or_ambiguous"),
])
def test_text_identity_needs_original_named_subject(monkeypatch, quote, evidence, expected):
    monkeypatch.setattr("s01b_project_extractor.stages.s4_entity.resolve_entity", lambda name: EntityResolution(name, "", "unmatched_needs_review", ""))
    entity = {"Implementing Chinese Entity": "新辰能源", "Chinese Identity Evidence": evidence}
    normalize_and_verify_entities([entity], "", quote)
    assert entity["_identity_evidence_status"] == expected
    assert entity["_entity_verification_status"] == "unmatched_needs_review"
    assert entity["_entity_raw_name"] == "新辰能源"
    assert entity["Chinese Identity Evidence"] == evidence


def test_confirmed_foreign_registry_cannot_be_overridden(monkeypatch):
    monkeypatch.setattr("s01b_project_extractor.stages.s4_entity.resolve_entity", lambda name: EntityResolution(name, "", "verified_non_chinese", "developer_registry"))
    quote = "中国企业外方甲公司承建纳沃伊项目。"
    entity = {"Implementing Chinese Entity": "外方甲公司", "Chinese Identity Evidence": quote}
    normalize_and_verify_entities([entity], "", quote)
    assert entity["_identity_evidence_status"] == "missing_or_ambiguous"
    assert "登记册已确认非中企" in entity["_identity_evidence_reasons"][0]
