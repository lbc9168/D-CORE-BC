"""Registry clues inform S1 before its decision; substring hits cannot override semantics."""
import pytest

from s01b_project_extractor.stages.entity_normalize import verified_chinese_mentions
from s01b_project_extractor.stages.s1_relevance import run_s1, validate_s1


class NegativeLLM:
    def __init__(self, status):
        self.status = status
        self.prompt = ""

    def complete(self, prompt, **kwargs):
        self.prompt = prompt
        return {"status": self.status, "reasoning": "模型原判定"}


def test_known_company_hint_is_supplied_before_decision_and_versions_recorded():
    llm = NegativeLLM("no_chinese_participation")
    out = run_s1("电站建设", "中国电建承建赛尔吉利储能电站。", "2026-09-15", llm=llm)
    assert out["status"] == "no_chinese_participation"
    assert out["identity_recall"]["original_status"] == "no_chinese_participation"
    assert out["identity_recall"]["rescued_for_extraction"] is False
    assert out["identity_recall"]["mentions"]
    assert all(len(value) == 64 for value in out["identity_recall"]["registry_hashes"].values())
    assert "仅证明企业身份" in llm.prompt


@pytest.mark.parametrize("status", ["irrelevant", "invalid_status"])
def test_does_not_override_scope_or_invalid_output(status):
    out = run_s1("背景新闻", "中国电建出席会议。", "2026-09-15", llm=NegativeLLM(status))
    assert out["status"] == status
    assert bool(validate_s1(out)) == (status == "invalid_status")


def test_unregistered_company_does_not_acquire_chinese_identity():
    out = run_s1("电站", "陌生阿尔法公司承建电站。", "2026-09-15",
                 llm=NegativeLLM("no_chinese_participation"))
    assert out["status"] == "no_chinese_participation"


def test_foreign_namesake_negative_is_not_overridden_by_registry_substring(monkeypatch):
    # Isolate the decision from evolving alias indexes: an erroneous substring
    # hint must remain harmless even if a future registry reintroduces this name.
    monkeypatch.setattr("s01b_project_extractor.stages.s1_relevance.verified_chinese_mentions",
                        lambda _text: [{"raw_name": "国家电网", "entity_id": "ent_chinese_grid"}])
    llm = NegativeLLM("no_chinese_participation")
    out = run_s1("乌兹别克斯坦风电项目", "项目接入乌兹别克斯坦国家电网，中方企业未参与。",
                 "2026-09-23", llm=llm)
    assert out["identity_recall"]["mentions"]
    assert out["status"] == "no_chinese_participation"
    assert out["identity_recall"]["rescued_for_extraction"] is False


def test_semantically_plausible_unregistered_candidate_can_continue():
    out = run_s1("储能项目", "新星储能提供系统。", "2026-09-23",
                 llm=NegativeLLM("valid_project"))
    assert out["status"] == "valid_project"
    assert out["identity_recall"]["mentions"] == []


def test_english_boundary_and_foreign_company():
    assert verified_chinese_mentions("ACWA Power owns the project.") == []
    assert verified_chinese_mentions("NotLONGiCompany builds it.") == []
    assert verified_chinese_mentions("LONGi builds it.")
    assert verified_chinese_mentions("外国投资者拟参与该项目。") == []
