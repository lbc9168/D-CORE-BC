"""Test immutable evidence lookup and field-level candidate disagreement reporting."""

from __future__ import annotations

import hashlib

import pytest

from gold_standard_studio.evidence import (
    AmbiguousEvidenceError,
    EvidenceNotFoundError,
    compare_candidates,
    locate_evidence,
    relocate_quote,
)
from gold_standard_studio.schemas import CandidateBundle, CandidateValue, EvidenceSpan, GoldField, GoldProject


def _sha(text: str) -> str:
    """Return the independently calculated content hash used by the evidence contract."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _project(identifier: str, **values: object) -> GoldProject:
    """Create a source candidate project whose explicitly supplied values are present fields."""
    return GoldProject(
        gold_project_id=identifier,
        fields={name: GoldField(state="present", value=value) for name, value in values.items()},
    )


def test_quote_relocation_recovers_a_shifted_offset():
    """Find a unique quote after source text growth invalidates an earlier offset."""
    text = "前文新增。建设100MW光伏电站，并配套20MW/40MWh储能。"

    span = relocate_quote(text, "20MW/40MWh储能", expected_start=5, article_sha256=_sha(text))

    assert text[span.start : span.end] == "20MW/40MWh储能"
    assert span.article_sha256 == _sha(text)


def test_ambiguous_quote_requires_review_instead_of_picking_first_match():
    """Reject repeated quotes without an offset hint so evidence cannot silently move."""
    text = "储能系统；储能系统"

    with pytest.raises(AmbiguousEvidenceError):
        relocate_quote(text, "储能系统", expected_start=None, article_sha256=_sha(text))


def test_overlapping_quote_occurrences_are_ambiguous_too():
    """Reject overlapping text matches so a regex implementation cannot silently skip a valid location."""
    text = "aaa"

    with pytest.raises(AmbiguousEvidenceError):
        relocate_quote(text, "aa", expected_start=None, article_sha256=_sha(text))


def test_hash_or_offset_drift_relocates_only_a_unique_quote():
    """Recover one stale evidence span while making its new immutable hash explicit to callers."""
    old_text = "建设100MW光伏电站。"
    new_text = "前文新增。建设100MW光伏电站。"
    old_start = old_text.index("100MW")
    stale = EvidenceSpan(
        quote="100MW",
        start=old_start,
        end=old_start + len("100MW"),
        article_sha256=_sha(old_text),
    )

    relocated = locate_evidence(new_text, stale)

    assert relocated.article_sha256 == _sha(new_text)
    assert relocated.start == new_text.index("100MW")
    assert relocated != stale


def test_missing_quote_is_an_explicit_blocking_evidence_error():
    """Reject a stale quote that cannot be supported by the available article text."""
    text = "建设100MW光伏电站。"

    with pytest.raises(EvidenceNotFoundError):
        relocate_quote(text, "不存在", expected_start=0, article_sha256=_sha(text))


def test_compare_candidates_normalizes_field_types_and_preserves_zero():
    """Avoid false conflicts for normalized names, sets, numeric tolerance, and zero numeric values."""
    bundle = CandidateBundle(
        sample_id="S001",
        article_sha256="a" * 64,
        sources={
            "database": CandidateValue(
                source_name="database",
                projects=[_project("db-1", project_name="ACME Solar Co., Ltd.", generation_sources=["光伏", "风电"], capacity_mw=0)],
            ),
            "review": CandidateValue(
                source_name="review",
                projects=[_project("review-1", project_name="acme solar company limited", generation_sources=["风电", "光伏"], capacity_mw=0.0)],
            ),
        },
    )

    assert compare_candidates(bundle) == []


def test_compare_candidates_reports_true_numeric_and_category_conflicts_with_error_source_visible():
    """Report only successful-source value disagreements while retaining failed-source context."""
    bundle = CandidateBundle(
        sample_id="S002",
        article_sha256="b" * 64,
        sources={
            "database": CandidateValue(source_name="database", projects=[_project("db-1", project_name="Alpha", category="风电", capacity_mw=100)]),
            "deterministic": CandidateValue(source_name="deterministic", projects=[_project("det-1", project_name="Alpha", proposed_category="光伏", capacity_mw=106)]),
            "review": CandidateValue(source_name="review", status="error", error_code="timeout"),
        },
    )

    conflicts = compare_candidates(bundle)

    assert {(conflict.field_name, conflict.reason_code) for conflict in conflicts} == {("category", "field_disagreement"), ("capacity_mw", "field_disagreement")}
    assert all(conflict.error_sources == ("review",) for conflict in conflicts)
    assert {conflict.field_name: conflict.source_values for conflict in conflicts}["capacity_mw"] == {
        "database": 100,
        "deterministic": 106,
    }


def test_compare_candidates_treats_multiple_projects_as_a_structure_conflict():
    """Make project-count disagreement explicit instead of aligning unrelated project positions silently."""
    bundle = CandidateBundle(
        sample_id="S003",
        article_sha256="c" * 64,
        sources={
            "database": CandidateValue(source_name="database", projects=[_project("db-1", category="风电")]),
            "review": CandidateValue(source_name="review", projects=[_project("rv-1", category="风电"), _project("rv-2", category="光伏")]),
        },
    )

    conflict = compare_candidates(bundle)[0]

    assert conflict.field_name == "projects"
    assert conflict.reason_code == "project_count_disagreement"
    assert conflict.source_values == {"database": 1, "review": 2}


def test_compare_candidates_aligns_reversed_source_projects_by_name_not_position():
    """Compare matching project identities even when a source emits the same projects in reverse order."""
    bundle = CandidateBundle(
        sample_id="S004",
        article_sha256="d" * 64,
        sources={
            "database": CandidateValue(
                source_name="database",
                projects=[
                    _project("db-solar", project_name="Alpha Solar", category="光伏"),
                    _project("db-wind", project_name="Beta Wind", category="风电"),
                ],
            ),
            "review": CandidateValue(
                source_name="review",
                projects=[
                    _project("review-wind", project_name="Beta Wind", category="风电"),
                    _project("review-solar", project_name="Alpha Solar", category="光伏"),
                ],
            ),
        },
    )

    assert compare_candidates(bundle) == []


def test_ambiguous_name_alignment_surfaces_a_structure_conflict_without_field_comparisons():
    """Refuse to guess between two conservatively fuzzy project-name matches from one source."""
    bundle = CandidateBundle(
        sample_id="S005",
        article_sha256="e" * 64,
        sources={
            "database": CandidateValue(source_name="database", projects=[_project("db", project_name="Alpha Solar Plant", category="光伏")]),
            "review": CandidateValue(
                source_name="review",
                projects=[
                    _project("rv-a", project_name="Alpha Solar Plants", category="风电"),
                    _project("rv-b", project_name="Alpha Solar Plante", category="风电"),
                ],
            ),
        },
    )

    conflicts = compare_candidates(bundle)

    assert any(conflict.reason_code == "project_alignment_ambiguous" for conflict in conflicts)
    assert not any(conflict.field_name in {"project_name", "category"} for conflict in conflicts)


def test_unmatched_project_names_remain_an_explicit_structure_conflict():
    """Do not compare unrelated projects merely because different sources happened to emit one item each."""
    bundle = CandidateBundle(
        sample_id="S006",
        article_sha256="f" * 64,
        sources={
            "database": CandidateValue(source_name="database", projects=[_project("db", project_name="Alpha Solar", category="光伏")]),
            "review": CandidateValue(source_name="review", projects=[_project("rv", project_name="Beta Wind", category="风电")]),
        },
    )

    conflicts = compare_candidates(bundle)

    assert [conflict.reason_code for conflict in conflicts] == ["project_alignment_unmatched"]
