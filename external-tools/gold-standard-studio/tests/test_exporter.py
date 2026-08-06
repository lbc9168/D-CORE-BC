"""Exercise immutable rich-gold publication and legacy evaluation projection."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from gold_standard_studio.review_store import ReviewStore
from gold_standard_studio.schemas import EvidenceSpan, GoldField, GoldProject, RichGoldRecord


ARTICLE_SHA256 = "a" * 64


def make_store(tmp_path: pytest.TempPathFactory, *, reviewed: bool) -> ReviewStore:
    """Seed a review store with one article whose confirmation state is explicit."""
    store = ReviewStore(tmp_path / "review.db")
    store.initialize()
    field = GoldField(
        state="present",
        value=120,
        evidence_required=True,
        evidence=[EvidenceSpan(quote="120 MW", start=0, end=6, article_sha256=ARTICLE_SHA256)],
    )
    store.seed_article(
        RichGoldRecord(
            sample_id="S002",
            article_sha256=ARTICLE_SHA256,
            source_link="https://example.test/S002",
            dataset_split="regression",
            risk_tags=["capacity"],
            projects=[GoldProject(gold_project_id="P01", fields={"capacity_mw": field})],
        )
    )
    if reviewed:
        store.confirm_article("S002", actor="reviewer")
    return store


def test_publish_rejects_unreviewed_or_adjudication_records(tmp_path):
    """Reject every store snapshot containing a draft or unresolved formal field."""
    from gold_standard_studio.exporter import PublishError, publish_gold

    with pytest.raises(PublishError, match="not publishable"):
        publish_gold(make_store(tmp_path, reviewed=False), tmp_path / "gold_sets", "v0.1.0")


def test_publish_refuses_existing_version(tmp_path):
    """Keep a successfully published version immutable by rejecting a second target path."""
    from gold_standard_studio.exporter import publish_gold

    store = make_store(tmp_path, reviewed=True)
    publish_gold(store, tmp_path / "gold_sets", "v0.1.0")

    with pytest.raises(FileExistsError):
        publish_gold(store, tmp_path / "gold_sets", "v0.1.0")


def test_project_field_preserves_exact_eligibility_semantics():
    """Map every resolved semantic field state to its required eval value and denominator flag."""
    from gold_standard_studio.exporter import PublishError, project_field

    assert project_field(GoldField(state="present", value=12)) == (12, True)
    assert project_field(GoldField(state="not_mentioned")) == (None, True)
    assert project_field(GoldField(state="not_applicable")) == (None, False)
    assert project_field(GoldField(state="uncertain")) == (None, False)
    with pytest.raises(PublishError, match="not publishable"):
        project_field(GoldField(state="needs_adjudication"))


def test_projection_is_deterministic_loadable_and_preserves_review_store(tmp_path):
    """Write canonical artifacts without changing review records, audit history, or row counts."""
    from eval.gold_loader import load as load_projection
    from gold_standard_studio.exporter import publish_gold

    store = make_store(tmp_path, reviewed=True)
    before_article = store.get_article("S002").model_dump(mode="json")
    before_audit = store.latest_audit_id()
    before_counts = store.table_row_counts()

    paths = publish_gold(store, tmp_path / "gold_sets", "v0.1.0")

    first = paths.eval_projection.read_bytes()
    loaded = load_projection(paths.eval_projection)
    assert loaded[0]["article_id"] == "S002"
    assert loaded[0]["field_eligibility"] == {"capacity_mw": True}
    assert loaded[0]["projects"][0]["capacity_mw"] == 120
    assert all(path.is_file() for path in (
        paths.rich_gold, paths.manifest, paths.schema, paths.distribution, paths.changelog
    ))
    assert first == paths.eval_projection.read_bytes()
    assert json.loads(paths.manifest.read_text(encoding="utf-8"))["version"] == "v0.1.0"
    assert store.get_article("S002").model_dump(mode="json") == before_article
    assert store.latest_audit_id() == before_audit
    assert store.table_row_counts() == before_counts


def test_publish_rejects_unsafe_versions_and_cleans_temporary_output(tmp_path, monkeypatch):
    """Reject path traversal and remove every sibling staging directory when writing raises."""
    from gold_standard_studio import exporter

    store = make_store(tmp_path, reviewed=True)
    with pytest.raises(ValueError, match="version"):
        exporter.publish_gold(store, tmp_path / "gold_sets", "../escape")

    original = exporter._write_jsonl

    def fail_on_projection(path, records):
        """Simulate an interruption after the rich artifact has already been written."""
        if path.name == "eval_projection.jsonl":
            raise OSError("simulated write failure")
        return original(path, records)

    monkeypatch.setattr(exporter, "_write_jsonl", fail_on_projection)
    root = tmp_path / "gold_sets"
    with pytest.raises(OSError, match="simulated write failure"):
        exporter.publish_gold(store, root, "v0.1.0")
    assert not (root / "v0.1.0").exists()
    assert not list(root.glob(".v0.1.0.*.tmp"))


def test_rich_output_keeps_evidence_and_redacts_operational_secrets(tmp_path):
    """Retain useful audit provenance while preventing a credential-like metadata value from publication."""
    from gold_standard_studio.exporter import publish_gold

    store = make_store(tmp_path, reviewed=True)
    article = store.get_article("S002")
    # Seeding a separate reviewed article models an already persisted historical review snapshot.
    article.sample_id = "S003"
    article.source_link = "https://example.test/S003"
    article.audit_metadata = {"review_batch": "pilot", "api_token": "must-not-publish"}
    store.seed_article(article)

    paths = publish_gold(store, tmp_path / "gold_sets", "v0.1.0")

    records = [json.loads(line) for line in paths.rich_gold.read_text(encoding="utf-8").splitlines()]
    published = next(record for record in records if record["sample_id"] == "S003")
    assert published["projects"][0]["fields"]["capacity_mw"]["evidence"]
    assert published["audit_metadata"] == {"api_token": "[redacted]", "review_batch": "pilot"}
