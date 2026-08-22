"""中企身份未核实的项目必须进入人工复核且不得自动发布。"""

from s01b_project_extractor.stages.entity_normalize import (
    UNMATCHED_NEEDS_REVIEW,
    VERIFIED_CHINESE,
    VERIFIED_NON_CHINESE,
)
from s01b_project_extractor.stages.orchestrator import _apply_entity_publication_gate


def _entity(name: str, status: str) -> dict:
    """构造带确定性国别状态的最小中企实体。"""
    return {
        "Implementing Chinese Entity": name,
        "_entity_verification_status": status,
        "_participation_evidence_status": "verified",
    }


def test_unmatched_entity_blocks_publication_and_requires_review():
    project = {"chinese_entities": [_entity("某企业", UNMATCHED_NEEDS_REVIEW)]}
    _apply_entity_publication_gate(project)
    assert project["entity_gate_excluded"] is True
    assert project["needs_review"] is True


def test_verified_non_chinese_is_removed_but_verified_chinese_can_publish():
    project = {"chinese_entities": [
        _entity("国家电力投资集团有限公司", VERIFIED_CHINESE),
        _entity("Citaglobal Berhad", VERIFIED_NON_CHINESE),
    ]}
    _apply_entity_publication_gate(project)
    assert [e["Implementing Chinese Entity"] for e in project["chinese_entities"]] == [
        "国家电力投资集团有限公司"
    ]
    assert not project.get("entity_gate_excluded", False)


def test_only_non_chinese_entities_are_excluded_without_review():
    project = {"chinese_entities": [_entity("BGMC Holdings Berhad", VERIFIED_NON_CHINESE)]}
    _apply_entity_publication_gate(project)
    assert project["chinese_entities"] == []
    assert project["entity_gate_excluded"] is True
    assert project["needs_review"] is False


def test_unnamed_chinese_participation_is_review_only():
    project = {"chinese_entities": []}
    _apply_entity_publication_gate(project, unnamed=True)
    assert project["entity_gate_excluded"] is True
    assert project["needs_review"] is True


def test_missing_participation_binding_is_removed_and_blocks_when_no_entity_remains():
    """身份正确但没有项目—企业—角色证据时不得形成可发布中企关系。"""
    project = {"chinese_entities": [{
        **_entity("国家电力投资集团有限公司", VERIFIED_CHINESE),
        "_participation_evidence_status": "missing_or_ambiguous",
    }]}
    _apply_entity_publication_gate(project)
    assert project["chinese_entities"] == []
    assert project["entity_gate_excluded"] is True
    assert project["needs_review"] is True
    assert project["entity_review_reason_type"] == "entity_relation"
