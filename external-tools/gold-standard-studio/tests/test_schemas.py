"""Test the rich-gold schema invariants and publication boundary."""

import pytest
from pydantic import ValidationError

from gold_standard_studio.schemas import EvidenceSpan, GoldField, GoldProject, RichGoldRecord
from gold_standard_studio.field_registry import FIELD_REGISTRY


ARTICLE_SHA256 = "a" * 64


def make_evidence() -> EvidenceSpan:
    """Create a valid article evidence span for contract tests."""
    return EvidenceSpan(
        quote="100MW solar project",
        start=0,
        end=19,
        article_sha256=ARTICLE_SHA256,
    )


def make_record(*, field_state: str = "present", review_status: str = "reviewed") -> RichGoldRecord:
    """Create a minimally publishable rich record except for the requested state."""
    evidence = [make_evidence()] if field_state == "present" else []
    fields = {
        name: GoldField(state="not_mentioned", evidence_required=spec.evidence_required)
        for name, spec in FIELD_REGISTRY.items()
    }
    fields["capacity_mw"] = GoldField(
        state=field_state,
        value=100 if field_state == "present" else None,
        evidence=evidence,
        evidence_required=field_state == "present",
    )
    return RichGoldRecord(
        sample_id="S001",
        article_sha256=ARTICLE_SHA256,
        article_review_status=review_status,
        article_status="valid",
        projects=[
            GoldProject(
                gold_project_id="P01",
                fields=fields,
            )
        ],
    )


def test_present_critical_field_requires_confirmed_evidence():
    """Reject a critical present field when no article evidence confirms it."""
    with pytest.raises(ValidationError):
        GoldField(state="present", value=100, evidence=[], evidence_required=True)


def test_non_present_field_rejects_value():
    """Reject values attached to a non-present field state."""
    with pytest.raises(ValidationError):
        GoldField(state="not_applicable", value=100, evidence=[])


def test_present_field_requires_value():
    """Reject a present field that omits its required value."""
    with pytest.raises(ValidationError, match="present field requires value"):
        GoldField(state="present")


@pytest.mark.parametrize(("start", "end"), [(3, 3), (4, 3)])
def test_evidence_end_must_follow_start(start: int, end: int):
    """Reject evidence spans whose end offset is not after the start offset."""
    with pytest.raises(ValidationError, match="evidence end must be greater than start"):
        EvidenceSpan(
            quote="project",
            start=start,
            end=end,
            article_sha256=ARTICLE_SHA256,
        )


@pytest.mark.parametrize("article_sha256", ["not-a-hash", "A" * 64])
def test_evidence_requires_lowercase_sha256(article_sha256: str):
    """Reject evidence whose source hash is not a lowercase SHA-256 digest."""
    with pytest.raises(ValidationError):
        EvidenceSpan(quote="project", start=0, end=7, article_sha256=article_sha256)


def test_formal_record_rejects_needs_adjudication():
    """Block publication while any formal field still needs adjudication."""
    record = make_record(field_state="needs_adjudication")

    with pytest.raises(ValueError, match="not publishable"):
        record.assert_publishable()


def test_unreviewed_article_record_is_not_publishable():
    """Block publication until a human marks the entire article reviewed."""
    record = make_record(review_status="unreviewed")

    with pytest.raises(ValueError, match="not publishable"):
        record.assert_publishable()


def test_uncertain_field_is_formal_but_publishable():
    """Allow uncertainty in rich gold for later ineligible projection."""
    record = make_record(field_state="uncertain")

    record.assert_publishable()


def test_review_status_alias_does_not_replace_authoritative_article_status():
    """Accept the concise workflow alias while keeping an explicit negative gold classification separate."""
    record = RichGoldRecord(
        sample_id="S001",
        article_sha256=ARTICLE_SHA256,
        review_status="reviewed",
        article_status="irrelevant",
    )

    record.assert_publishable()
