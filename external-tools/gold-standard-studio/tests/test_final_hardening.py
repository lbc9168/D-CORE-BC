"""Lock the final Studio gold-state, evidence, migration, and path-isolation contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from gold_standard_studio.candidate_cache import CandidateCache, CandidateCacheKey
from gold_standard_studio.config import StudioConfig
from gold_standard_studio.evidence import locate_evidence, relocate_quote
from gold_standard_studio.exporter import publish_gold
from gold_standard_studio.field_registry import FIELD_REGISTRY
from gold_standard_studio.review_store import ReviewStore
from gold_standard_studio.schemas import (
    CandidateValue,
    EvidenceSpan,
    GoldEntity,
    GoldField,
    GoldProject,
    RichGoldRecord,
    SamplingProvenance,
)
from gold_standard_studio.sources import ArticleSnapshot, article_sha256
from gold_standard_studio.web.app import create_app


def _snapshot(content: str = "中国电建以EPC总承包商建设100MW光伏项目。") -> ArticleSnapshot:
    """Create one canonical title-content-date article snapshot for evidence tests."""
    title = "肯尼亚光伏项目"
    publish_date = "2026-08-06"
    return ArticleSnapshot(
        source_link="https://example.test/final",
        title=title,
        content=content,
        publish_date=publish_date,
        article_sha256=article_sha256(title, content, publish_date),
        raw_values={},
        display_values={},
    )


def _span(snapshot: ArticleSnapshot, quote: str) -> EvidenceSpan:
    """Create an independently indexed exact evidence span in the supplied snapshot."""
    start = snapshot.content.index(quote)
    return EvidenceSpan(
        quote=quote,
        start=start,
        end=start + len(quote),
        article_sha256=snapshot.article_sha256,
    )


def _complete_project(snapshot: ArticleSnapshot) -> GoldProject:
    """Build an exact-registry draft with one genuine typed field and one evidenced Chinese role."""
    fields = {
        name: GoldField(state="not_mentioned", evidence_required=spec.evidence_required)
        for name, spec in FIELD_REGISTRY.items()
    }
    fields["project_name"] = GoldField(
        state="present",
        value="肯尼亚光伏项目",
        evidence=[_span(snapshot, "光伏项目")],
        evidence_required=True,
    )
    fields["category"] = GoldField(
        state="present",
        value="光伏",
        evidence=[_span(snapshot, "光伏")],
        evidence_required=True,
    )
    fields["capacity_mw"] = GoldField(
        state="present",
        value=100.0,
        evidence=[_span(snapshot, "100MW")],
        evidence_required=True,
    )
    return GoldProject(
        gold_project_id="P01",
        fields=fields,
        entities=[
            GoldEntity(
                name="中国电建",
                role="EPC总承包",
                evidence=[_span(snapshot, "中国电建以EPC总承包商")],
            )
        ],
    )


def _valid_record(snapshot: ArticleSnapshot, sample_id: str = "S001") -> RichGoldRecord:
    """Build one unconfirmed valid article with explicit sampling provenance and full project state."""
    return RichGoldRecord(
        sample_id=sample_id,
        source_link=snapshot.source_link,
        article_sha256=snapshot.article_sha256,
        article_status="valid",
        sampling_provenance=SamplingProvenance(
            sampling_seed=20260806,
            inclusion_reasons=["risk_weighted_fill"],
            original_category_strata=["光伏"],
            risk_tags=["multiple_entities"],
            negative_proxy=False,
        ),
        risk_tags=["multiple_entities"],
        projects=[_complete_project(snapshot)],
    )


def _source_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create three real SQLite source files for mutable-path identity checks."""
    paths = tuple(tmp_path / name for name in ("raw.db", "projects.db", "main.db"))
    for path in paths:
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE marker (id INTEGER PRIMARY KEY)")
    return paths


def test_article_gold_status_and_sampling_provenance_are_explicit_strict_contracts() -> None:
    """Keep authoritative gold classification separate from workflow state and reject unknown payload keys."""
    snapshot = _snapshot()
    record = _valid_record(snapshot)

    assert record.article_status == "valid"
    assert record.article_review_status == "unreviewed"
    assert record.sampling_provenance.original_category_strata == ["光伏"]
    with pytest.raises(ValidationError):
        RichGoldRecord.model_validate({**record.model_dump(mode="json"), "unexpected": True})


def test_valid_confirmation_requires_a_project_and_current_atomic_snapshot(tmp_path: Path) -> None:
    """Reject empty valid shells and confirmation when no immutable source snapshot was atomically seeded."""
    snapshot = _snapshot()
    store = ReviewStore(tmp_path / "review.db")
    store.initialize()
    store.seed_article(
        RichGoldRecord(
            sample_id="EMPTY",
            source_link=snapshot.source_link,
            article_sha256=snapshot.article_sha256,
            article_status="valid",
        ),
        snapshot=snapshot,
    )
    store.seed_article(_valid_record(snapshot, "NOSNAPSHOT"))
    store.save_article_status("EMPTY", "valid", actor="human")
    store.save_article_status("NOSNAPSHOT", "valid", actor="human")

    with pytest.raises(ValueError, match="at least one project"):
        store.confirm_article("EMPTY", actor="human")
    with pytest.raises(ValueError, match="immutable article snapshot"):
        store.confirm_article("NOSNAPSHOT", actor="human")


def test_confirmation_validates_registry_types_and_every_exact_evidence_span(tmp_path: Path) -> None:
    """Reject wrong field types and quotes even when hashes and bounds otherwise look plausible."""
    snapshot = _snapshot()
    wrong_type = _valid_record(snapshot, "TYPE")
    wrong_type.projects[0].fields["capacity_mw"] = GoldField(
        state="present",
        value="100",
        evidence=[_span(snapshot, "100MW")],
        evidence_required=True,
    )
    wrong_quote = _valid_record(snapshot, "QUOTE")
    wrong_quote.projects[0].entities[0].evidence = [
        EvidenceSpan(
            quote="中国电建",
            start=snapshot.content.index("中国电建"),
            end=snapshot.content.index("中国电建") + 3,
            article_sha256=snapshot.article_sha256,
        )
    ]
    store = ReviewStore(tmp_path / "review.db")
    store.initialize()
    store.seed_article(wrong_type, snapshot=snapshot)
    store.seed_article(wrong_quote, snapshot=snapshot)
    store.save_article_status("TYPE", "valid", actor="human")
    store.save_article_status("QUOTE", "valid", actor="human")

    with pytest.raises(ValueError, match="capacity_mw.*number"):
        store.confirm_article("TYPE", actor="human")
    with pytest.raises(ValueError, match="exact quote"):
        store.confirm_article("QUOTE", actor="human")


def test_structure_hydrates_exact_registry_and_entity_status_api_is_audited(tmp_path: Path) -> None:
    """Initialize every structure field explicitly and expose audited status/entity review mutations."""
    snapshot = _snapshot()
    store = ReviewStore(tmp_path / "review.db")
    store.initialize()
    store.seed_article(
        RichGoldRecord(sample_id="S001", source_link=snapshot.source_link, article_sha256=snapshot.article_sha256),
        snapshot=snapshot,
    )
    client = TestClient(create_app(store))

    structured = client.put(
        "/api/articles/S001/structure",
        json={"base_revision": 1, "projects": [{"gold_project_id": "NEW", "fields": {}, "entities": []}]},
    )
    status = client.put("/api/articles/S001/status", json={"article_status": "valid"})
    entities = client.put(
        "/api/articles/S001/projects/P01/entities",
        json={
            "entities": [
                {
                    "name": "中国电建",
                    "role": "EPC总承包",
                    "evidence": [_span(snapshot, "中国电建以EPC总承包商").model_dump(mode="json")],
                }
            ]
        },
    )

    assert structured.status_code == 200, structured.text
    fields = structured.json()["article"]["projects"][0]["fields"]
    assert set(fields) == set(FIELD_REGISTRY)
    assert {field["state"] for field in fields.values()} == {"unreviewed"}
    assert status.status_code == 200
    assert entities.status_code == 200
    assert [entry.action for entry in store.list_audit("S001")][-2:] == [
        "article_status_decision",
        "entity_decision",
    ]


@pytest.mark.parametrize("negative_status", ["no_chinese", "irrelevant", "chinese_unnamed"])
def test_negative_one_click_sets_authoritative_status_and_confirms_server_side(
    tmp_path: Path, negative_status: str
) -> None:
    """Classify and confirm a no-project negative only through one audited server-side gate."""
    snapshot = _snapshot()
    store = ReviewStore(tmp_path / f"{negative_status}.db")
    store.initialize()
    store.seed_article(
        RichGoldRecord(
            sample_id="N001",
            source_link=snapshot.source_link,
            article_sha256=snapshot.article_sha256,
            sampling_provenance=SamplingProvenance(
                sampling_seed=20260806,
                inclusion_reasons=["negative_fixture"],
                original_category_strata=[],
                risk_tags=[],
                negative_proxy=True,
            ),
        ),
        snapshot=snapshot,
    )
    response = TestClient(create_app(store)).post(
        "/api/articles/N001/confirm-negative", json={"article_status": negative_status}
    )

    assert response.status_code == 200, response.text
    assert response.json()["article"]["article_status"] == negative_status
    assert response.json()["article"]["article_review_status"] == "reviewed"
    assert [entry.action for entry in store.list_audit("N001")][-3:] == [
        "structure_decision",
        "article_status_decision",
        "article_confirmed",
    ]


def test_rich_and_projection_preserve_only_authoritative_status_and_explicit_provenance(tmp_path: Path) -> None:
    """Regenerate projection status and provenance exactly from the rich contract, never audit metadata or structure."""
    snapshot = _snapshot()
    store = ReviewStore(tmp_path / "review.db")
    store.initialize()
    record = _valid_record(snapshot)
    record.audit_metadata = {"eval_status": "irrelevant"}
    store.seed_article(record, snapshot=snapshot)
    store.save_article_status("S001", "valid", actor="human")
    store.confirm_article("S001", actor="human")

    paths = publish_gold(store, tmp_path / "gold", "v1.0.0")
    rich = json.loads(paths.rich_gold.read_text(encoding="utf-8").splitlines()[0])
    projection = json.loads(paths.eval_projection.read_text(encoding="utf-8").splitlines()[0])

    assert rich["article_status"] == projection["status"] == "valid"
    assert rich["sampling_provenance"] == projection["sampling_provenance"]
    assert "sampling" not in rich["audit_metadata"]


def test_evidence_relocation_uses_canonical_article_snapshot_hash() -> None:
    """Bind relocation to title, content, and date so equal content under another article identity is stale."""
    snapshot = _snapshot("前文新增。建设100MW光伏电站。")
    quote = "100MW"
    relocated = relocate_quote(snapshot, quote, expected_start=0)
    changed_title = ArticleSnapshot(
        source_link=snapshot.source_link,
        title="另一标题",
        content=snapshot.content,
        publish_date=snapshot.publish_date,
        article_sha256=article_sha256("另一标题", snapshot.content, snapshot.publish_date),
        raw_values={},
        display_values={},
    )

    assert relocated.article_sha256 == snapshot.article_sha256
    assert locate_evidence(changed_title, relocated).article_sha256 == changed_title.article_sha256


def test_config_rejects_same_resolved_symlink_and_hardlink_mutable_paths_before_creation(tmp_path: Path) -> None:
    """Reject every filesystem identity alias between a source DB and a derived mutable Studio path."""
    raw_db, projects_db, main_db = _source_files(tmp_path)
    with pytest.raises(ValueError, match="source database"):
        StudioConfig.from_paths(
            raw_news_db=raw_db,
            projects_db=projects_db,
            main_db=main_db,
            data_dir=raw_db,
        )

    hardlink_dir = tmp_path / "hardlink-studio"
    hardlink_dir.mkdir()
    os.link(raw_db, hardlink_dir / "review.db")
    with pytest.raises(ValueError, match="source database"):
        StudioConfig.from_paths(
            raw_news_db=raw_db,
            projects_db=projects_db,
            main_db=main_db,
            data_dir=hardlink_dir,
        )

    symlink = tmp_path / "source-alias.db"
    try:
        symlink.symlink_to(raw_db)
    except OSError:
        pytest.skip("filesystem does not permit test symlinks")
    with pytest.raises(ValueError, match="source database"):
        StudioConfig.from_paths(
            raw_news_db=raw_db,
            projects_db=projects_db,
            main_db=main_db,
            data_dir=symlink,
        )


def test_candidate_cache_rechecks_a_hardlinked_destination_immediately_before_write(tmp_path: Path) -> None:
    """Block a cache destination swapped to a source hardlink after initial configuration checks."""
    sources = _source_files(tmp_path)
    cache_dir = tmp_path / "model_cache"
    cache_dir.mkdir()
    key = CandidateCacheKey(
        article_sha256="a" * 64,
        source_name="deterministic",
        source_version="v1",
        prompt_hash="",
        parser_version="v1",
    )
    os.link(sources[0], cache_dir / f"{key.digest()}.json")
    cache = CandidateCache(cache_dir, source_paths=sources)

    with pytest.raises(ValueError, match="source database"):
        cache.store(key, CandidateValue(source_name="deterministic", source_version="v1"))


def test_review_store_runs_ordered_idempotent_migrations_and_rejects_future_versions(tmp_path: Path) -> None:
    """Upgrade an existing v1 database transactionally exactly once and fail closed on unknown future schemas."""
    legacy = tmp_path / "legacy.db"
    with sqlite3.connect(legacy) as conn:
        conn.executescript(
            """CREATE TABLE studio_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO studio_meta VALUES ('schema_version', '1');
            CREATE TABLE samples (sample_id TEXT PRIMARY KEY, article_sha256 TEXT NOT NULL, source_link TEXT, created_at TEXT NOT NULL);
            CREATE TABLE article_drafts (sample_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, review_status TEXT NOT NULL, difficulty_score REAL NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE candidate_bundles (sample_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE sample_scoring (sample_id TEXT PRIMARY KEY, difficulty_score REAL NOT NULL, reason_codes_json TEXT NOT NULL, risk_tags_json TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE field_decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, sample_id TEXT NOT NULL, field_path TEXT NOT NULL, field_json TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE structure_revisions (sample_id TEXT NOT NULL, revision INTEGER NOT NULL, projects_json TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (sample_id, revision));
            CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, sample_id TEXT NOT NULL, action TEXT NOT NULL, field_path TEXT, old_json TEXT, new_json TEXT, actor TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE review_sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, sample_id TEXT NOT NULL, actor TEXT NOT NULL, started_at TEXT NOT NULL);"""
        )
    store = ReviewStore(legacy)
    store.initialize()
    first_counts = store.table_row_counts()
    store.initialize()
    with sqlite3.connect(legacy) as conn:
        version = int(conn.execute("SELECT value FROM studio_meta WHERE key='schema_version'").fetchone()[0])
        columns = {row[1] for row in conn.execute("PRAGMA table_info(article_drafts)")}
    assert version == store.schema_version
    assert "article_snapshots" in first_counts
    assert "article_status" in columns
    assert store.table_row_counts() == first_counts

    future = tmp_path / "future.db"
    with sqlite3.connect(future) as conn:
        conn.execute("CREATE TABLE studio_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO studio_meta VALUES ('schema_version', '999')")
    with pytest.raises(RuntimeError, match="future review schema"):
        ReviewStore(future).initialize()
