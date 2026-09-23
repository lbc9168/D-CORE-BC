"""主体补录、身份与参与分别判断；单条未决关系不影响已确认参与。"""

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


def test_one_unmatched_member_does_not_block_known_chinese_participant():
    known = {**_entity("已知中企", VERIFIED_CHINESE), "_entity_id": "C001"}
    unknown = _entity("新企业", UNMATCHED_NEEDS_REVIEW)
    project = {"chinese_entities": [known, unknown]}
    _apply_entity_publication_gate(project)
    assert project["chinese_entities"] == [known]
    assert project["publication_status"] == "eligible"
    assert project["needs_review"] and not project["entity_gate_excluded"]
    assert project["entity_candidates"] == [known, unknown]


def test_source_verified_new_chinese_company_is_retained_pending_registration():
    entity = {**_entity("新中企", UNMATCHED_NEEDS_REVIEW),
              "_identity_evidence_status": "verified"}
    project = {"chinese_entities": [entity]}
    _apply_entity_publication_gate(project)
    assert project["chinese_entities"] == [entity]
    assert project["publication_status"] == "eligible"
    assert project["needs_review"] and not project["entity_gate_excluded"]


def test_source_claim_cannot_override_known_foreign_or_ambiguous_registry():
    for status, source in [(VERIFIED_NON_CHINESE, "registry"),
                           (UNMATCHED_NEEDS_REVIEW, "ambiguous"),
                           (UNMATCHED_NEEDS_REVIEW, "registry_needs_review")]:
        entity = {**_entity("歧义名", status), "_identity_evidence_status": "verified",
                  "_entity_matched_source": source}
        project = {"chinese_entities": [entity]}
        _apply_entity_publication_gate(project)
        assert project["chinese_entities"] == []
        assert project["publication_status"] != "eligible"


def test_known_group_with_unknown_legal_entity_can_keep_participation():
    entity = {**_entity("集团通称", VERIFIED_CHINESE), "_group_entity_id": "GROUP001"}
    project = {"chinese_entities": [entity]}
    _apply_entity_publication_gate(project)
    assert project["publication_status"] == "eligible"
    assert project["needs_review"]


def test_missing_participation_is_retained_in_audit_candidates():
    entity = {**_entity("某中企", VERIFIED_CHINESE),
              "_participation_evidence_status": "missing_or_ambiguous",
              "Participation Evidence": [{"quote": "未经验证的原文候选"}]}
    project = {"chinese_entities": [entity]}
    _apply_entity_publication_gate(project)
    assert project["publication_status"] == "pending_review"
    assert project["entity_candidates"][0]["Participation Evidence"]
