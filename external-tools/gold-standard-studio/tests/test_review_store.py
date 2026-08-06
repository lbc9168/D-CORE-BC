"""Exercise the durable, transactional review-store boundary with a real SQLite database."""

from __future__ import annotations

import sqlite3

import pytest

from gold_standard_studio.review_store import ReviewStore
from gold_standard_studio.schemas import (
    CandidateBundle,
    CandidateValue,
    DifficultyScore,
    GoldField,
    GoldProject,
    RichGoldRecord,
)


ARTICLE_SHA256 = "a" * 64


def make_gold_field(value: str, *, state: str = "present") -> GoldField:
    """Build a non-critical field whose literal value makes decision assertions independent."""
    return GoldField(state=state, value=value if state == "present" else None)


def make_ready_article(sample_id: str = "S001") -> RichGoldRecord:
    """Build a reviewable single-project draft with a stable project identifier."""
    return RichGoldRecord(
        sample_id=sample_id,
        article_sha256=ARTICLE_SHA256,
        source_link=f"https://example.test/{sample_id}",
        projects=[GoldProject(gold_project_id="P01", fields={"category": make_gold_field("风电")})],
    )


def seeded_store(tmp_path, *, unresolved: bool = False) -> ReviewStore:
    """Seed two drafts so queue ordering, resumption, and review guards use real persisted data."""
    store = ReviewStore(tmp_path / "review.db")
    store.initialize()
    first = make_ready_article("S001")
    if unresolved:
        first.projects[0].fields["category"] = make_gold_field("", state="needs_adjudication")
    store.seed_article(first)
    store.seed_article(make_ready_article("S002"))
    return store


def test_initialize_is_idempotent_and_persists_schema_metadata(tmp_path):
    """Create the review schema twice while retaining the one durable schema-version marker."""
    store = ReviewStore(tmp_path / "review.db")

    store.initialize()
    store.initialize()

    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT value FROM studio_meta WHERE key = 'schema_version'").fetchone() == ("1",)


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

    with pytest.raises(ValueError, match="unknown field"):
        store.save_field_decision("S001", "P01.missing", make_gold_field("光伏"), actor="human")

    assert store.get_article("S001").projects[0].fields["category"].value == "风电"
    assert store.list_audit("S001") == []


def test_confirm_rejects_unresolved_conflicts(tmp_path):
    """Block review completion until no formal field remains in needs-adjudication state."""
    store = seeded_store(tmp_path, unresolved=True)

    with pytest.raises(ValueError, match="unresolved conflicts"):
        store.confirm_article("S001", actor="human")

    assert store.get_article("S001").review_status == "unreviewed"


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
        '{"article_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        '"sample_id":"S001","sources":{"database":{"error_code":null,'
        '"error_detail":null,"metadata":{},"projects":[],"source_name":"database",'
        '"source_version":"","status":"ok"}}}'
    )


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
    store.seed_article(low)
    store.seed_article(high)
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
