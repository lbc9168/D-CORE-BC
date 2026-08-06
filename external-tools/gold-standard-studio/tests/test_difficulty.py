"""Test explainable candidate difficulty scoring and deterministic audit escalation."""

from __future__ import annotations

from gold_standard_studio.difficulty import score_difficulty, select_for_audit
from gold_standard_studio.review_store import ReviewStore
from gold_standard_studio.schemas import CandidateBundle, CandidateValue, GoldEntity, GoldField, GoldProject


def _project(identifier: str, *, fields: dict[str, GoldField], entities: list[GoldEntity] | None = None) -> GoldProject:
    """Build a candidate project with explicit fields and optional role-bearing entities."""
    return GoldProject(gold_project_id=identifier, fields=fields, entities=entities or [])


def test_database_hb_vs_fact_pv_is_an_explained_conflict_with_storage_context():
    """Expose category disagreement and a storage signal without an opaque confidence score."""
    bundle = CandidateBundle(
        sample_id="S001",
        article_sha256="a" * 64,
        sources={
            "database": CandidateValue(
                source_name="database",
                projects=[_project("db", fields={"project_name": GoldField(state="present", value="Alpha"), "category": GoldField(state="present", value="混合能源")})],
            ),
            "deterministic": CandidateValue(
                source_name="deterministic",
                projects=[
                    _project(
                        "facts",
                        fields={
                            "project_name": GoldField(state="present", value="Alpha"),
                            "proposed_category": GoldField(state="present", value="光伏"),
                            "storage_status": GoldField(state="present", value="with_storage"),
                        },
                    )
                ],
            ),
        },
    )

    score = score_difficulty(bundle)

    assert "category_disagreement" in score.reason_codes
    assert "storage_context" in score.risk_tags
    assert score.score >= 20


def test_difficulty_records_evidence_and_multi_value_risks_deterministically():
    """Add each documented reason once when critical support, amounts, and entities are genuinely risky."""
    no_evidence = GoldField(state="present", value=100)
    bundle = CandidateBundle(
        sample_id="S002",
        article_sha256="b" * 64,
        sources={
            "database": CandidateValue(
                source_name="database",
                projects=[
                    _project(
                        "db",
                        fields={"capacity_mw": no_evidence, "total_investment_usd": GoldField(state="present", value=100)},
                        entities=[GoldEntity(role="developer", name="A"), GoldEntity(role="contractor", name="B")],
                    )
                ],
            ),
            "review": CandidateValue(
                source_name="review",
                projects=[_project("review", fields={"total_investment_usd": GoldField(state="present", value=200)})],
            ),
        },
    )

    score = score_difficulty(bundle)

    assert score.reason_codes == ["missing_required_evidence", "multiple_amounts", "multiple_entities"]
    assert score.score == 40
    assert score.risk_tags == ["missing_evidence", "multiple_amounts", "multiple_entities"]


def test_missing_or_error_sources_reduce_support_without_being_value_conflicts():
    """Score one usable source as limited support while ignoring a failed provider as a false value."""
    bundle = CandidateBundle(
        sample_id="S003",
        article_sha256="c" * 64,
        sources={
            "database": CandidateValue(source_name="database", projects=[]),
            "review": CandidateValue(source_name="review", status="error", error_code="timeout"),
        },
    )

    score = score_difficulty(bundle)

    assert score.reason_codes == ["single_source_support"]
    assert score.score == 10
    assert score.risk_tags == ["source_failure", "single_source_support"]


def test_audit_selection_is_deterministic_and_about_ten_percent():
    """Select a stable hash-derived audit subset without depending on process-global randomness."""
    selected = [select_for_audit(f"S{index:03}", seed=20260806, rate=0.10) for index in range(1000)]

    assert 80 <= sum(selected) <= 120
    assert selected == [select_for_audit(f"S{index:03}", seed=20260806, rate=0.10) for index in range(1000)]


def test_audit_selection_honors_zero_and_one_rate_boundaries():
    """Keep deterministic audit selection empty at zero and exhaustive at one without random edge leaks."""
    sample_ids = [f"S{index:03}" for index in range(20)]

    assert not any(select_for_audit(sample_id, seed=20260806, rate=0) for sample_id in sample_ids)
    assert all(select_for_audit(sample_id, seed=20260806, rate=1) for sample_id in sample_ids)


def test_audited_error_increases_each_risk_tag_rate_with_a_cap(tmp_path):
    """Persist per-risk escalation so subsequent audit selection can use a higher bounded rate."""
    store = ReviewStore(tmp_path / "review.db")
    store.initialize()

    assert store.get_risk_audit_rate("storage_context") == 0.10
    assert store.record_audit_error(["storage_context", "multiple_amounts"]) == {
        "multiple_amounts": 0.20,
        "storage_context": 0.20,
    }
    for _ in range(20):
        store.record_audit_error(["storage_context"])

    assert store.get_risk_audit_rate("storage_context") == 1.0
