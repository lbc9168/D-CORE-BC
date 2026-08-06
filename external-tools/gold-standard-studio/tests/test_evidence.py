"""Test immutable evidence lookup and field-level candidate disagreement reporting."""

from __future__ import annotations

import pytest

from gold_standard_studio.evidence import (
    AmbiguousEvidenceError,
    EvidenceNotFoundError,
    compare_candidates,
    locate_evidence,
    relocate_quote,
)
from gold_standard_studio.schemas import CandidateBundle, CandidateValue, EvidenceSpan, GoldField, GoldProject
from gold_standard_studio.sources import ArticleSnapshot, article_sha256


def _sha(text: str) -> str:
    """Return the canonical title-content-date hash used by the evidence contract."""
    return _snapshot(text).article_sha256


def _snapshot(text: str) -> ArticleSnapshot:
    """Build one immutable canonical snapshot around the supplied evidence text."""
    title, publish_date = "证据文章", "2026-08-06"
    return ArticleSnapshot(
        source_link="https://example.test/evidence",
        title=title,
        content=text,
        publish_date=publish_date,
        article_sha256=article_sha256(title, text, publish_date),
        raw_values={},
        display_values={},
    )


def _project(identifier: str, **values: object) -> GoldProject:
    """Create a source candidate project whose explicitly supplied values are present fields."""
    return GoldProject(
        gold_project_id=identifier,
        fields={name: GoldField(state="present", value=value) for name, value in values.items()},
    )


def test_quote_relocation_recovers_a_shifted_offset():
    """Find a unique quote after source text growth invalidates an earlier offset."""
    text = "前文新增。建设100MW光伏电站，并配套20MW/40MWh储能。"

    span = relocate_quote(_snapshot(text), "20MW/40MWh储能", expected_start=5)

    assert text[span.start : span.end] == "20MW/40MWh储能"
    assert span.article_sha256 == _sha(text)


def test_ambiguous_quote_requires_review_instead_of_picking_first_match():
    """Reject repeated quotes without an offset hint so evidence cannot silently move."""
    text = "储能系统；储能系统"

    with pytest.raises(AmbiguousEvidenceError):
        relocate_quote(_snapshot(text), "储能系统", expected_start=None)


def test_overlapping_quote_occurrences_are_ambiguous_too():
    """Reject overlapping text matches so a regex implementation cannot silently skip a valid location."""
    text = "aaa"

    with pytest.raises(AmbiguousEvidenceError):
        relocate_quote(_snapshot(text), "aa", expected_start=None)


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

    relocated = locate_evidence(_snapshot(new_text), stale)

    assert relocated.article_sha256 == _sha(new_text)
    assert relocated.start == new_text.index("100MW")
    assert relocated != stale


def test_missing_quote_is_an_explicit_blocking_evidence_error():
    """Reject a stale quote that cannot be supported by the available article text."""
    text = "建设100MW光伏电站。"

    with pytest.raises(EvidenceNotFoundError):
        relocate_quote(_snapshot(text), "不存在", expected_start=0)


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
            "deterministic": CandidateValue(source_name="deterministic", projects=[_project("det-1", project_name="Alpha", category="光伏", capacity_mw=106)]),
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


@pytest.mark.parametrize(
    ("database_projects", "review_projects"),
    [
        (
            [("db-a", "Alpha Solar Project A", "光伏"), ("db-base", "Alpha Solar Project", "风电")],
            [("review-base", "Alpha Solar Project", "风电"), ("review-a", "Alpha Solar Project A1", "光伏")],
        ),
        (
            [("db-base", "Alpha Solar Project", "风电"), ("db-a", "Alpha Solar Project A", "光伏")],
            [("review-a", "Alpha Solar Project A1", "光伏"), ("review-base", "Alpha Solar Project", "风电")],
        ),
    ],
)
def test_global_alignment_reserves_exact_pair_before_fuzzy_pairs_regardless_of_list_order(
    database_projects, review_projects
):
    """Reserve the exact identity globally so a nearby fuzzy project cannot steal it by traversal order."""
    bundle = CandidateBundle(
        sample_id="S007",
        article_sha256="a" * 64,
        sources={
            "database": CandidateValue(
                source_name="database",
                projects=[_project(identifier, project_name=name, category=category) for identifier, name, category in database_projects],
            ),
            "review": CandidateValue(
                source_name="review",
                projects=[_project(identifier, project_name=name, category=category) for identifier, name, category in review_projects],
            ),
        },
    )

    assert compare_candidates(bundle) == []


def test_multiple_optimum_fuzzy_assignment_remains_an_explicit_alignment_ambiguity():
    """Reject an all-fuzzy graph with two equally complete one-to-one assignments instead of choosing a tie."""
    bundle = CandidateBundle(
        sample_id="S008",
        article_sha256="b" * 64,
        sources={
            "database": CandidateValue(
                source_name="database",
                projects=[
                    _project("db-a", project_name="Alpha Solar Project A", category="光伏"),
                    _project("db-b", project_name="Alpha Solar Project B", category="风电"),
                ],
            ),
            "review": CandidateValue(
                source_name="review",
                projects=[
                    _project("review-c", project_name="Alpha Solar Project C", category="光伏"),
                    _project("review-d", project_name="Alpha Solar Project D", category="风电"),
                ],
            ),
        },
    )

    conflicts = compare_candidates(bundle)

    assert [conflict.reason_code for conflict in conflicts] == ["project_alignment_ambiguous"]
    assert not any(conflict.field_name in {"project_name", "category"} for conflict in conflicts)
