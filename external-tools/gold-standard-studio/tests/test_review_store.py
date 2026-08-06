"""Exercise the durable, transactional review-store boundary with a real SQLite database."""

from __future__ import annotations

import sqlite3

import pytest

from gold_standard_studio.review_store import ReviewStore, ReviewedRecordError
from gold_standard_studio.field_registry import FIELD_REGISTRY
from gold_standard_studio.schemas import (
    CandidateBundle,
    CandidateValue,
    DifficultyScore,
    EvidenceSpan,
    GoldEntity,
    GoldField,
    GoldProject,
    RichGoldRecord,
    SamplingProvenance,
)
from gold_standard_studio.sources import ArticleSnapshot, article_sha256


ARTICLE_TITLE = "测试项目"
ARTICLE_CONTENT = "风电光伏"
ARTICLE_DATE = "2026-08-06"
ARTICLE_SHA256 = article_sha256(ARTICLE_TITLE, ARTICLE_CONTENT, ARTICLE_DATE)


def make_snapshot(sample_id: str) -> ArticleSnapshot:
    """Build the canonical source snapshot required by every confirmation test."""
    return ArticleSnapshot(
        source_link=f"https://example.test/{sample_id}",
        title=ARTICLE_TITLE,
        content=ARTICLE_CONTENT,
        publish_date=ARTICLE_DATE,
        article_sha256=ARTICLE_SHA256,
        raw_values={},
        display_values={},
    )


def make_gold_field(value: str, *, state: str = "present") -> GoldField:
    """Build one registry-compliant category decision with exact fixture evidence when present."""
    evidence = []
    if state == "present":
        start = ARTICLE_CONTENT.index(value)
        evidence = [EvidenceSpan(quote=value, start=start, end=start + len(value), article_sha256=ARTICLE_SHA256)]
    return GoldField(
        state=state,
        value=value if state == "present" else None,
        evidence=evidence,
        evidence_required=True,
    )


def make_ready_article(sample_id: str = "S001") -> RichGoldRecord:
    """Build a reviewable single-project draft with a stable project identifier."""
    fields = {
        name: GoldField(state="not_mentioned", evidence_required=spec.evidence_required)
        for name, spec in FIELD_REGISTRY.items()
    }
    fields["category"] = make_gold_field("风电")
    return RichGoldRecord(
        sample_id=sample_id,
        article_sha256=ARTICLE_SHA256,
        source_link=f"https://example.test/{sample_id}",
        sampling_provenance=SamplingProvenance(
            sampling_seed=20260806,
            inclusion_reasons=["review_store_fixture"],
            original_category_strata=["风电"],
            risk_tags=[],
            negative_proxy=False,
        ),
        projects=[
            GoldProject(
                gold_project_id="P01",
                fields=fields,
                entities=[
                    GoldEntity(
                        name="风电",
                        role="EPC总承包",
                        evidence=[
                            EvidenceSpan(
                                quote="风电",
                                start=0,
                                end=2,
                                article_sha256=ARTICLE_SHA256,
                            )
                        ],
                    )
                ],
            )
        ],
    )


def seeded_store(tmp_path, *, unresolved: bool = False) -> ReviewStore:
    """Seed two drafts so queue ordering, resumption, and review guards use real persisted data."""
    store = ReviewStore(tmp_path / "review.db")
    store.initialize()
    first = make_ready_article("S001")
    if unresolved:
        first.projects[0].fields["category"] = make_gold_field("", state="needs_adjudication")
    store.seed_article(first, snapshot=make_snapshot("S001"))
    store.seed_article(make_ready_article("S002"), snapshot=make_snapshot("S002"))
    store.save_article_status("S001", "valid", actor="fixture")
    store.save_article_status("S002", "valid", actor="fixture")
    return store


def test_initialize_is_idempotent_and_persists_schema_metadata(tmp_path):
    """Create the review schema twice while retaining the one durable schema-version marker."""
    store = ReviewStore(tmp_path / "review.db")

    store.initialize()
    store.initialize()

    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT value FROM studio_meta WHERE key = 'schema_version'").fetchone() == (
            str(store.schema_version),
        )


def test_field_decision_is_atomic_and_audited(tmp_path):
    """Persist a human field change and its before-and-after audit entry in one transaction."""
    store = ReviewStore(tmp_path / "review.db")
    store.initialize()
    store.seed_article(make_ready_article())

    store.save_field_decision("S001", "P01.category", make_gold_field("光伏"), actor="human")

    assert store.get_article("S001").projects[0].fields["category"].value == "光伏"
    audit = store.list_audit("S001")
    assert audit[-1].field_path == "P01.category"
    assert audit[-1].old_value == "风电"
    assert audit[-1].new_value == "光伏"
    assert audit[-1].actor == "human"


def test_failed_field_decision_leaves_draft_and_audit_unchanged(tmp_path):
    """Reject an unsafe path without committing either a partial draft mutation or audit row."""
    store = seeded_store(tmp_path)
    before_audit = store.list_audit("S001")

    with pytest.raises(ValueError, match="unknown field"):
        store.save_field_decision("S001", "P01.missing", make_gold_field("光伏"), actor="human")

    assert store.get_article("S001").projects[0].fields["category"].value == "风电"
    assert store.list_audit("S001") == before_audit


def test_confirm_rejects_unresolved_conflicts(tmp_path):
    """Block review completion until no formal field remains in needs-adjudication state."""
    store = seeded_store(tmp_path, unresolved=True)

    with pytest.raises(ValueError, match="unresolved conflicts"):
        store.confirm_article("S001", actor="human")

    assert store.get_article("S001").review_status == "unreviewed"


def test_confirm_revalidates_current_state_after_an_observer_has_seen_an_earlier_draft(tmp_path):
    """Reject a later pending-field write even when an earlier observer saw a confirmable draft."""
    store = seeded_store(tmp_path)
    evidenced = GoldField(
        state="present",
        value="风电",
        evidence_required=True,
        evidence=[EvidenceSpan(quote="风电", start=0, end=2, article_sha256=ARTICLE_SHA256)],
    )
    store.save_field_decision("S001", "P01.category", evidenced, actor="human")
    observed = store.get_article("S001")
    store.save_field_decision("S001", "P01.category", make_gold_field("", state="needs_adjudication"), actor="other")

    with pytest.raises(ValueError, match="unresolved conflicts"):
        store.confirm_article("S001", actor="human")

    assert observed.projects[0].fields["category"].state == "present"
    assert store.get_article("S001").review_status == "unreviewed"


def test_confirm_rejects_registered_evidence_flag_mismatch_inside_store(tmp_path):
    """Enforce the registry-derived evidence flag again inside the confirmation transaction."""
    store = ReviewStore(tmp_path / "review.db")
    store.initialize()
    article = make_ready_article()
    article.projects[0].fields["category"] = GoldField(state="present", value="风电")
    store.seed_article(article, snapshot=make_snapshot("S001"))
    store.save_article_status("S001", "valid", actor="fixture")

    with pytest.raises(ValueError, match="evidence_required must match"):
        store.confirm_article("S001", actor="human")


def test_confirm_rejects_required_evidence_from_a_different_article_snapshot(tmp_path):
    """Reject evidence whose hash does not match the immutable article addressed by confirmation."""
    store = seeded_store(tmp_path)
    mismatched = GoldField(
        state="present",
        value="风电",
        evidence_required=True,
        evidence=[EvidenceSpan(quote="风电", start=0, end=2, article_sha256="b" * 64)],
    )
    store.save_field_decision("S001", "P01.category", mismatched, actor="human")

    with pytest.raises(ValueError, match="evidence article hash mismatch: P01.category"):
        store.confirm_article("S001", actor="human")


def test_confirm_rejects_an_article_that_is_already_reviewed(tmp_path):
    """Prevent a second confirmation from appending a duplicate confirmation audit event."""
    store = seeded_store(tmp_path)
    evidenced = GoldField(
        state="present",
        value="风电",
        evidence_required=True,
        evidence=[EvidenceSpan(quote="风电", start=0, end=2, article_sha256=ARTICLE_SHA256)],
    )
    store.save_field_decision("S001", "P01.category", evidenced, actor="human")
    store.confirm_article("S001", actor="human")

    with pytest.raises(ReviewedRecordError, match="reviewed records are immutable"):
        store.confirm_article("S001", actor="human")


def test_reviewed_draft_rejects_all_mutations_without_changing_persisted_state(tmp_path):
    """Keep a confirmed draft, revision, and audit tail immutable across every human draft mutation route."""
    store = seeded_store(tmp_path)
    evidenced = GoldField(
        state="present",
        value="风电",
        evidence_required=True,
        evidence=[EvidenceSpan(quote="风电", start=0, end=2, article_sha256=ARTICLE_SHA256)],
    )
    store.save_field_decision("S001", "P01.category", evidenced, actor="human")
    store.confirm_article("S001", actor="human")
    before_article = store.get_article("S001").model_dump(mode="json")
    before_revision = store.current_structure_revision("S001")
    before_tail = store.latest_audit_id()

    with pytest.raises(ReviewedRecordError, match="reviewed records are immutable"):
        store.save_field_decision("S001", "P01.category", make_gold_field("光伏"), actor="human")
    with pytest.raises(ReviewedRecordError, match="reviewed records are immutable"):
        store.save_structure("S001", [], base_revision=before_revision, actor="human")
    with pytest.raises(ReviewedRecordError, match="reviewed records are immutable"):
        store.adjudicate_field("S001", "P01.category", make_gold_field("光伏"), actor="human")
    with pytest.raises(ReviewedRecordError, match="reviewed records are immutable"):
        store.start_review("S001", actor="human")

    assert store.get_article("S001").model_dump(mode="json") == before_article
    assert store.current_structure_revision("S001") == before_revision
    assert store.latest_audit_id() == before_tail


def test_candidate_bundle_remains_observational_after_confirmation(tmp_path):
    """Allow candidate provenance after confirmation because it does not alter the reviewed gold draft."""
    store = seeded_store(tmp_path)
    evidenced = GoldField(
        state="present",
        value="风电",
        evidence_required=True,
        evidence=[EvidenceSpan(quote="风电", start=0, end=2, article_sha256=ARTICLE_SHA256)],
    )
    store.save_field_decision("S001", "P01.category", evidenced, actor="human")
    store.confirm_article("S001", actor="human")
    before_article = store.get_article("S001")

    store.save_candidate_bundle(
        CandidateBundle(
            sample_id="S001",
            article_sha256=ARTICLE_SHA256,
            sources={"database": CandidateValue(source_name="database")},
        )
    )

    assert store.get_article("S001") == before_article


def test_candidate_scoring_is_observational_persistent_and_reorders_the_queue(tmp_path):
    """Persist candidate-derived scoring independently of gold drafts, including after human confirmation."""
    store = ReviewStore(tmp_path / "review.db")
    store.initialize()
    store.seed_article(
        RichGoldRecord(
            sample_id="S001",
            article_sha256=ARTICLE_SHA256,
            sampling_provenance=SamplingProvenance(
                sampling_seed=20260806,
                inclusion_reasons=["scoring_fixture"],
                original_category_strata=[],
                risk_tags=[],
                negative_proxy=True,
            ),
        ),
        snapshot=make_snapshot("S001"),
    )
    store.seed_article(
        RichGoldRecord(
            sample_id="S002",
            article_sha256=ARTICLE_SHA256,
            sampling_provenance=SamplingProvenance(
                sampling_seed=20260806,
                inclusion_reasons=["scoring_fixture"],
                original_category_strata=[],
                risk_tags=[],
                negative_proxy=True,
            ),
        ),
        snapshot=make_snapshot("S002"),
    )
    store.confirm_negative("S001", "irrelevant", actor="human")
    before_gold = store.get_article("S001").model_dump(mode="json")

    store.update_sample_scoring(
        "S001", DifficultyScore(score=90, reason_codes=["category_disagreement"], risk_tags=["category_conflict"])
    )
    store.update_sample_scoring(
        "S002", DifficultyScore(score=10, reason_codes=["single_source_support"], risk_tags=["single_source_support"])
    )
    reopened = ReviewStore(store.path)

    assert reopened.get_article("S001").model_dump(mode="json") == before_gold
    assert [(item.sample_id, item.difficulty_score) for item in reopened.list_queue()] == [("S002", 10)]
    all_samples = {item.sample_id: item for item in reopened.list_all_samples()}
    assert all_samples["S001"].risk_tags == ("category_conflict",)
    assert all_samples["S001"].difficulty_score == 90
    assert reopened.list_audit("S001")[-1].action == "sample_scoring"


def test_adjudication_atomically_resolves_only_a_pending_field_and_audits_its_action(tmp_path):
    """Resolve a held field in one transaction while preserving an adjudication-specific audit event."""
    store = seeded_store(tmp_path, unresolved=True)

    updated = store.adjudicate_field("S001", "P01.category", make_gold_field("光伏"), actor="human")

    assert updated.projects[0].fields["category"].value == "光伏"
    assert store.list_audit("S001")[-1].action == "adjudication"
    with pytest.raises(ValueError, match="not awaiting adjudication"):
        store.adjudicate_field("S001", "P01.category", make_gold_field("风电"), actor="human")


def test_generic_field_decision_cannot_resolve_a_pending_field_but_can_keep_it_pending(tmp_path):
    """Reserve resolution for adjudication while allowing a reviewer to retain an unresolved pending state."""
    store = seeded_store(tmp_path, unresolved=True)

    store.save_field_decision("S001", "P01.category", make_gold_field("", state="needs_adjudication"), actor="human")
    with pytest.raises(ValueError, match="requires adjudication"):
        store.save_field_decision("S001", "P01.category", make_gold_field("光伏"), actor="human")

    assert store.get_article("S001").projects[0].fields["category"].state == "needs_adjudication"


def test_candidate_bundle_uses_canonical_json_and_replaces_same_source(tmp_path):
    """Store one canonical Pydantic bundle per article and source without duplicate source rows."""
    store = seeded_store(tmp_path)
    bundle = CandidateBundle(
        sample_id="S001",
        article_sha256=ARTICLE_SHA256,
        sources={"database": CandidateValue(source_name="database")},
    )

    store.save_candidate_bundle(bundle)
    stored = store.get_candidate_bundle("S001")

    assert stored == bundle
    with sqlite3.connect(store.path) as conn:
        payload = conn.execute("SELECT payload_json FROM candidate_bundles").fetchone()[0]
    assert payload == (
        f'{{"article_sha256":"{ARTICLE_SHA256}",'
        '"sample_id":"S001","sources":{"database":{"error_code":null,'
        '"error_detail":null,"metadata":{},"projects":[],"source_name":"database",'
        '"source_version":"","status":"ok"}}}'
    )


def test_candidate_bundle_update_audits_complete_previous_and_new_payloads(tmp_path):
    """Audit a replacement candidate bundle with exact decoded before-and-after bundle payloads."""
    store = seeded_store(tmp_path)
    first = CandidateBundle(
        sample_id="S001",
        article_sha256=ARTICLE_SHA256,
        sources={"database": CandidateValue(source_name="database", source_version="v1")},
    )
    second = CandidateBundle(
        sample_id="S001",
        article_sha256=ARTICLE_SHA256,
        sources={"database": CandidateValue(source_name="database", source_version="v2")},
    )

    store.save_candidate_bundle(first)
    store.save_candidate_bundle(second)

    candidate_audit = [entry for entry in store.list_audit("S001") if entry.action == "candidate_bundle"]
    assert candidate_audit[-1].old_payload == first
    assert candidate_audit[-1].new_payload == second
    assert candidate_audit[-1].old_value == first.model_dump(mode="json")
    assert candidate_audit[-1].new_value == second.model_dump(mode="json")


def test_field_audit_preserves_complete_payload_when_display_value_is_unchanged_or_none(tmp_path):
    """Expose field states and evidence even when old and new display values cannot show the decision change."""
    store = ReviewStore(tmp_path / "review.db")
    store.initialize()
    article = make_ready_article()
    article.projects[0].fields["location"] = GoldField(state="not_applicable")
    store.seed_article(article)
    evidenced = GoldField(
        state="present",
        value="风电",
        evidence=[EvidenceSpan(quote="风电", start=0, end=2, article_sha256=ARTICLE_SHA256)],
        evidence_required=True,
    )

    store.save_field_decision("S001", "P01.category", evidenced, actor="human")
    store.save_field_decision("S001", "P01.location", GoldField(state="uncertain"), actor="human")

    same_value, none_value = store.list_audit("S001")[-2:]
    assert same_value.old_value == same_value.new_value == "风电"
    assert same_value.old_payload == make_gold_field("风电")
    assert same_value.new_payload == evidenced
    assert none_value.old_value is None and none_value.new_value is None
    assert none_value.old_payload == GoldField(state="not_applicable")
    assert none_value.new_payload == GoldField(state="uncertain")


def test_structure_revision_requires_current_version_and_never_reuses_project_ids(tmp_path):
    """Save a full project structure atomically and reject an optimistic-concurrency conflict."""
    store = seeded_store(tmp_path)
    replacement = GoldProject(gold_project_id="P02", fields={"category": make_gold_field("光伏")})

    updated = store.save_structure("S001", [replacement], base_revision=1, actor="human")

    assert [project.gold_project_id for project in updated.projects] == ["P02"]
    assert store.current_structure_revision("S001") == 2
    with pytest.raises(ValueError, match="stale structure revision"):
        store.save_structure("S001", [replacement], base_revision=1, actor="human")
    revisions = store.list_structure_revisions("S001")
    assert revisions[-1].revision == 2
    assert revisions[-1].actor == "human"
    structure_audit = [entry for entry in store.list_audit("S001") if entry.action == "structure_decision"][-1]
    assert structure_audit.old_payload[0]["fields"]["category"]["value"] == "风电"
    assert structure_audit.new_payload[0]["fields"]["category"]["value"] == "光伏"


def test_structure_reintroducing_deleted_id_receives_fresh_project_id(tmp_path):
    """Allocate a new stable ID when a deleted project ID would otherwise be reused by a later split."""
    store = seeded_store(tmp_path)
    first_split = [
        GoldProject(gold_project_id="P01", fields={"category": make_gold_field("风电")}),
        GoldProject(gold_project_id="P02", fields={"category": make_gold_field("光伏")}),
    ]
    store.save_structure("S001", first_split, base_revision=1, actor="human")
    store.save_structure("S001", first_split[:1], base_revision=2, actor="human")

    restored = store.save_structure("S001", first_split, base_revision=3, actor="human")

    assert [project.gold_project_id for project in restored.projects] == ["P01", "P03"]


def test_latest_in_review_article_resumes_after_reopen(tmp_path):
    """Restore the most recently started article for an actor after a fresh store instance opens it."""
    store = seeded_store(tmp_path)
    store.start_review("S002", actor="human")

    reopened = ReviewStore(store.path)

    assert reopened.resume_target("human") == "S002"


def test_queue_excludes_reviewed_articles_and_sorts_by_difficulty_then_id(tmp_path):
    """Return only outstanding work in deterministic difficulty and stable-ID order."""
    store = seeded_store(tmp_path)
    low = make_ready_article("S003")
    low.difficulty = DifficultyScore(score=1.0)
    high = make_ready_article("S004")
    high.difficulty = DifficultyScore(score=5.0)
    store.seed_article(low, snapshot=make_snapshot("S003"))
    store.seed_article(high, snapshot=make_snapshot("S004"))
    store.save_field_decision(
        "S002",
        "P01.category",
        GoldField(
            state="present",
            value="风电",
            evidence_required=True,
            evidence=[EvidenceSpan(quote="风电", start=0, end=2, article_sha256=ARTICLE_SHA256)],
        ),
        actor="human",
    )
    store.confirm_article("S002", actor="human")

    assert [item.sample_id for item in store.list_queue()] == ["S001", "S003", "S004"]


def test_backup_is_readable_and_preserves_audit_tail_and_row_counts(tmp_path):
    """Create a SQLite backup that passes integrity and preserves each persisted table's contents."""
    store = seeded_store(tmp_path)
    store.save_field_decision("S001", "P01.category", make_gold_field("光伏"), actor="human")

    backup = store.backup()

    assert backup.exists()
    assert ReviewStore(backup).latest_audit_id() == store.latest_audit_id()
    assert ReviewStore(backup).table_row_counts() == store.table_row_counts()
