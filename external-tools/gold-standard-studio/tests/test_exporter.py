"""Exercise immutable rich-gold publication and legacy evaluation projection."""

from __future__ import annotations

import json
import sqlite3
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
    assert loaded[0]["projects"][0]["field_eligibility"] == {"capacity_mw": True}
    assert loaded[0]["projects"][0]["capacity_mw"] == 120
    assert all(path.is_file() for path in (
        paths.rich_gold, paths.manifest, paths.schema, paths.distribution, paths.changelog
    ))
    assert first == paths.eval_projection.read_bytes()
    assert json.loads(paths.manifest.read_text(encoding="utf-8"))["version"] == "v0.1.0"
    assert store.get_article("S002").model_dump(mode="json") == before_article
    assert store.latest_audit_id() == before_audit
    assert store.table_row_counts() == before_counts


def test_projection_uses_per_project_eligibility_and_loads_through_eval_loader(tmp_path):
    """Keep mixed field eligibility attached to each project rather than collapsing article denominators."""
    from eval.gold_loader import load as load_projection
    from gold_standard_studio.exporter import publish_gold

    store = make_store(tmp_path, reviewed=True)
    article = store.get_article("S002")
    article.sample_id = "S003"
    article.source_link = "https://example.test/S003"
    article.projects = [
        GoldProject(gold_project_id="P01", fields={"capacity_mw": GoldField(state="present", value=12)}),
        GoldProject(gold_project_id="P02", fields={"capacity_mw": GoldField(state="uncertain")}),
    ]
    store.seed_article(article)
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "INSERT INTO audit_log(sample_id, action, field_path, old_json, new_json, actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("S003", "article_confirmed", "article_review_status", '"unreviewed"', '"reviewed"', "forged", "2026-08-06T00:00:00+00:00"),
        )

    paths = publish_gold(store, tmp_path / "gold_sets", "v0.1.0")
    record = next(row for row in load_projection(paths.eval_projection) if row["article_id"] == "S003")

    assert "field_eligibility" not in record
    assert [project["field_eligibility"] for project in record["projects"]] == [
        {"capacity_mw": True}, {"capacity_mw": False}
    ]


def test_publish_requires_matching_confirmation_audit_and_consistent_indexed_status(tmp_path):
    """Reject forged reviewed payloads and indexed-status mismatches while accepting normal confirmation."""
    from gold_standard_studio.exporter import PublishError, publish_gold

    forged = make_store(tmp_path / "forged", reviewed=False)
    with sqlite3.connect(forged.path) as conn:
        payload = json.loads(conn.execute("SELECT payload_json FROM article_drafts WHERE sample_id = 'S002'").fetchone()[0])
        payload["article_review_status"] = "reviewed"
        conn.execute(
            "UPDATE article_drafts SET payload_json = ?, review_status = 'reviewed' WHERE sample_id = 'S002'",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")),),
        )
    with pytest.raises(PublishError, match="confirmation audit"):
        publish_gold(forged, tmp_path / "forged-output", "v0.1.0")

    mismatched = make_store(tmp_path / "mismatch", reviewed=True)
    with sqlite3.connect(mismatched.path) as conn:
        conn.execute("UPDATE article_drafts SET review_status = 'unreviewed' WHERE sample_id = 'S002'")
    with pytest.raises(PublishError, match="status mismatch"):
        publish_gold(mismatched, tmp_path / "mismatch-output", "v0.1.0")

    valid = make_store(tmp_path / "valid", reviewed=True)
    assert publish_gold(valid, tmp_path / "valid-output", "v0.1.0").version_dir.is_dir()


def test_publish_rejects_later_gold_mutation_but_allows_later_candidate_observation(tmp_path):
    """Treat later candidate observations as safe while blocking any forged post-confirmation gold edit."""
    from gold_standard_studio.exporter import PublishError, publish_gold

    store = make_store(tmp_path, reviewed=True)
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "INSERT INTO audit_log(sample_id, action, field_path, old_json, new_json, actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("S002", "candidate_bundle", None, None, None, "system", "2026-08-06T00:01:00+00:00"),
        )
    assert publish_gold(store, tmp_path / "candidate-ok", "v0.1.0").version_dir.is_dir()

    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "INSERT INTO audit_log(sample_id, action, field_path, old_json, new_json, actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("S002", "field_decision", "P01.capacity_mw", None, None, "forged", "2026-08-06T00:02:00+00:00"),
        )
    with pytest.raises(PublishError, match="later gold-draft mutation"):
        publish_gold(store, tmp_path / "mutation-blocked", "v0.1.0")


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


def test_publish_does_not_clobber_a_version_created_during_commit(tmp_path, monkeypatch):
    """Leave a concurrent final directory untouched and remove this publisher's staged directory on conflict."""
    from gold_standard_studio import exporter

    store = make_store(tmp_path, reviewed=True)
    root = tmp_path / "gold_sets"
    original = exporter._commit_version_directory

    def create_competitor(temporary, final):
        """Create the final directory immediately before the no-replace commit helper runs."""
        final.mkdir()
        (final / "competitor.txt").write_text("winner", encoding="utf-8")
        return original(temporary, final)

    monkeypatch.setattr(exporter, "_commit_version_directory", create_competitor)
    with pytest.raises(FileExistsError):
        exporter.publish_gold(store, root, "v0.1.0")
    assert (root / "v0.1.0" / "competitor.txt").read_text(encoding="utf-8") == "winner"
    assert not list(root.glob(".v0.1.0.*.tmp"))


def test_rich_output_exports_only_allowlisted_confirmation_metadata(tmp_path):
    """Omit arbitrary nested credential-like metadata while retaining only publish-audit provenance."""
    from gold_standard_studio.exporter import publish_gold

    store = make_store(tmp_path, reviewed=True)
    article = store.get_article("S002")
    # Seeding a separate reviewed article models an already persisted historical review snapshot.
    article.sample_id = "S003"
    article.source_link = "https://example.test/S003"
    article.audit_metadata = {
        "review_batch": "leak-batch", "api key": "leak-a", "api_key": "leak-b", "access key": "leak-c",
        "private-key": "leak-d", "token": "leak-e", "secret": "leak-f", "password": "leak-g",
        "credential": "leak-h", "auth": "leak-i", "cookie": "leak-j", "nested": {"private key": "leak-k"},
    }
    # A seeded historical record needs a matching confirmation proof before publication.
    store.seed_article(article)
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "INSERT INTO audit_log(sample_id, action, field_path, old_json, new_json, actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("S003", "article_confirmed", "article_review_status", '"unreviewed"', '"reviewed"', "forged", "2026-08-06T00:00:00+00:00"),
        )

    paths = publish_gold(store, tmp_path / "gold_sets", "v0.1.0")

    records = [json.loads(line) for line in paths.rich_gold.read_text(encoding="utf-8").splitlines()]
    published = next(record for record in records if record["sample_id"] == "S003")
    assert published["projects"][0]["fields"]["capacity_mw"]["evidence"]
    assert set(published["audit_metadata"]) == {"confirmation"}
    assert set(published["audit_metadata"]["confirmation"]) == {"audit_id", "action", "created_at", "field_path"}
    output = paths.rich_gold.read_text(encoding="utf-8")
    assert all(f"leak-{letter}" not in output for letter in "abcdefghijk")


def _seed_confirmed_projection_case(store, *, field_name, value):
    """Insert one reviewed historical record whose rich field value exercises projection validation."""
    article = store.get_article("S002")
    article.sample_id = "S003"
    article.source_link = "https://example.test/S003"
    article.projects = [
        GoldProject(gold_project_id="P01", fields={field_name: GoldField(state="present", value=value)})
    ]
    store.seed_article(article)
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "INSERT INTO audit_log(sample_id, action, field_path, old_json, new_json, actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("S003", "article_confirmed", "article_review_status", '"unreviewed"', '"reviewed"', "fixture", "2026-08-06T00:00:00+00:00"),
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("category", 1),
        ("category", "not-a-category"),
        ("generation_sources", ["solar"]),
        ("planned_start_date", 20260101),
        ("storage_power_mw", "unknown"),
        ("storage_power_mw", True),
    ],
)
def test_publish_rejects_loader_invalid_projection_values_before_rename(tmp_path, field_name, value):
    """Fail closed and remove staging when a reviewed rich value violates the authoritative eval contract."""
    from gold_standard_studio.exporter import PublishError, publish_gold

    store = make_store(tmp_path, reviewed=True)
    _seed_confirmed_projection_case(store, field_name=field_name, value=value)
    root = tmp_path / "gold_sets"

    with pytest.raises(PublishError, match="eval projection"):
        publish_gold(store, root, "v0.1.0")

    assert not (root / "v0.1.0").exists()
    assert not list(root.glob(".v0.1.0.*.tmp"))


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("category", "风电"),
        ("generation_sources", ["光伏"]),
        ("planned_start_date", "2026-01-01"),
        ("storage_power_mw", 50.0),
    ],
)
def test_publish_accepts_strictly_typed_loader_projection_values(tmp_path, field_name, value):
    """Accept rich values whose JSON types and controlled enums satisfy the shared eval loader contract."""
    from gold_standard_studio.exporter import publish_gold

    store = make_store(tmp_path, reviewed=True)
    _seed_confirmed_projection_case(store, field_name=field_name, value=value)

    assert publish_gold(store, tmp_path / "gold_sets", "v0.1.0").version_dir.is_dir()
