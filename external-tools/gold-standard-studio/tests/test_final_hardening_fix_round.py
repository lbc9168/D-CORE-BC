"""Reproduce final-core review findings before implementing the first hardening fix round."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from typer.testing import CliRunner

import gold_standard_studio.review_store as review_store_module
import gold_standard_studio.config as config_module
from gold_standard_studio.config import StudioConfig
from gold_standard_studio.exporter import publish_gold
from gold_standard_studio.field_registry import FIELD_REGISTRY
from gold_standard_studio.review_store import ReviewStore
from gold_standard_studio.schemas import (
    CandidateBundle,
    CandidateValue,
    EvidenceSpan,
    GoldEntity,
    GoldField,
    GoldProject,
    RichGoldRecord,
    SamplingProvenance,
)
from gold_standard_studio.sources import ArticleSnapshot, SourceRepository, article_sha256
from gold_standard_studio.web.app import StructureRequest, create_app

from test_final_hardening import _complete_project, _source_files, _span, _snapshot, _valid_record


def _seed_valid(store: ReviewStore, snapshot: ArticleSnapshot, sample_id: str = "S001") -> None:
    """Seed one full valid draft and create its explicit audited status decision."""
    store.seed_article(_valid_record(snapshot, sample_id), snapshot=snapshot)
    store.save_article_status(sample_id, "valid", actor="human")


def _legacy_payload(snapshot: ArticleSnapshot, *, projects: list[GoldProject], eval_status: str) -> dict:
    """Build one data-bearing pre-v3 reviewed payload with only non-authoritative legacy hints."""
    payload = RichGoldRecord(
        sample_id="placeholder",
        source_link=snapshot.source_link,
        article_sha256=snapshot.article_sha256,
        article_review_status="reviewed",
        projects=projects,
        audit_metadata={
            "eval_status": eval_status,
            "sampling": {
                "seed": 17,
                "inclusion_reasons": ["legacy_quota"],
                "category_tags": ["光伏"],
                "negative_proxy": eval_status != "valid",
            },
        },
    ).model_dump(mode="json")
    payload.pop("article_status")
    payload.pop("sampling_provenance")
    return payload


def _create_data_bearing_v1(path: Path, rows: list[tuple[str, ArticleSnapshot, list[GoldProject], str]]) -> None:
    """Create an authentic v1 review database containing already-reviewed positive and negative rows."""
    with sqlite3.connect(path) as conn:
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
        for sample_id, snapshot, projects, status in rows:
            payload = _legacy_payload(snapshot, projects=projects, eval_status=status)
            payload["sample_id"] = sample_id
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            conn.execute(
                "INSERT INTO samples VALUES (?, ?, ?, ?)",
                (sample_id, snapshot.article_sha256, snapshot.source_link, "2026-08-01T00:00:00+00:00"),
            )
            conn.execute(
                "INSERT INTO article_drafts VALUES (?, ?, 'reviewed', 0, ?)",
                (sample_id, encoded, "2026-08-01T00:00:00+00:00"),
            )
            conn.execute(
                "INSERT INTO structure_revisions VALUES (?, 1, ?, 'legacy', ?)",
                (
                    sample_id,
                    json.dumps([project.model_dump(mode="json") for project in projects], ensure_ascii=False),
                    "2026-08-01T00:00:00+00:00",
                ),
            )


def _source_repository(tmp_path: Path, snapshots: list[ArticleSnapshot]) -> tuple[StudioConfig, SourceRepository]:
    """Create a configured read-only repository capable of rehydrating migrated source snapshots."""
    raw, projects, main = (tmp_path / name for name in ("raw.db", "projects.db", "main.db"))
    with sqlite3.connect(raw) as conn:
        conn.execute(
            "CREATE TABLE raw_news (id INTEGER PRIMARY KEY, url TEXT, title TEXT, content TEXT, publish_date TEXT)"
        )
        conn.executemany(
            "INSERT INTO raw_news VALUES (?, ?, ?, ?, ?)",
            [
                (index, item.source_link, item.title, item.content, item.publish_date)
                for index, item in enumerate(snapshots, start=1)
            ],
        )
    with sqlite3.connect(projects) as conn:
        conn.execute(
            "CREATE TABLE extracted_projects (id INTEGER PRIMARY KEY, source_link TEXT, imported_to_s02a INTEGER, category TEXT)"
        )
    with sqlite3.connect(main) as conn:
        conn.executescript(
            """CREATE TABLE canonical_projects (id TEXT PRIMARY KEY, project_name TEXT, category TEXT);
            CREATE TABLE project_events (id TEXT PRIMARY KEY, canonical_id TEXT, source_links TEXT);
            CREATE TABLE chinese_entities (id INTEGER PRIMARY KEY, event_id TEXT);"""
        )
    config = StudioConfig.from_paths(
        raw_news_db=raw,
        projects_db=projects,
        main_db=main,
        data_dir=tmp_path / "studio",
    )
    return config, SourceRepository(config)


def test_data_bearing_v1_migration_revokes_authority_then_rehydrates_rereviews_and_publishes(
    tmp_path: Path,
) -> None:
    """Never infer migrated authority; require snapshot recovery and fresh audited decisions before publication."""
    valid_snapshot = _snapshot()
    negative_snapshot = _snapshot("报道未涉及中国企业。")
    negative_snapshot = ArticleSnapshot(
        source_link="https://example.test/negative",
        title=negative_snapshot.title,
        content=negative_snapshot.content,
        publish_date=negative_snapshot.publish_date,
        article_sha256=negative_snapshot.article_sha256,
        raw_values={},
        display_values={},
    )
    path = tmp_path / "legacy.db"
    _create_data_bearing_v1(
        path,
        [
            ("LEGACY-VALID", valid_snapshot, [_complete_project(valid_snapshot)], "valid"),
            ("LEGACY-NEGATIVE", negative_snapshot, [], "irrelevant"),
        ],
    )
    config, repository = _source_repository(tmp_path, [valid_snapshot, negative_snapshot])
    store = ReviewStore(path, source_paths=config.source_paths)

    store.initialize()

    for sample_id in ("LEGACY-VALID", "LEGACY-NEGATIVE"):
        migrated = store.get_article(sample_id)
        assert migrated.article_status == "unreviewed"
        assert migrated.article_review_status == "unreviewed"
        assert migrated.audit_metadata["legacy_migration_hint"]["declared_status"] in {"valid", "irrelevant"}
        assert store.get_article_snapshot(sample_id) is None
        assert store.list_audit(sample_id)[-1].action == "migration_rereview_required"
        store.attach_article_snapshot(
            sample_id,
            repository.load_article(migrated.source_link),
            actor="recovery",
        )
        store.replace_sampling_provenance(
            sample_id,
            SamplingProvenance(
                sampling_seed=73,
                inclusion_reasons=["operator_reconstructed_manifest"],
                original_category_strata=[],
                risk_tags=["legacy_recovery"],
                negative_proxy=sample_id == "LEGACY-NEGATIVE",
                verified=True,
                verification_source="operator_review",
            ),
            actor="recovery",
        )

    store.save_article_status("LEGACY-VALID", "valid", actor="reviewer")
    store.confirm_article("LEGACY-VALID", actor="reviewer")
    store.confirm_negative("LEGACY-NEGATIVE", "irrelevant", actor="reviewer")

    paths = publish_gold(store, tmp_path / "gold", "v2.0.0")
    statuses = {
        json.loads(line)["article_status"]
        for line in paths.rich_gold.read_text(encoding="utf-8").splitlines()
    }
    assert statuses == {"valid", "irrelevant"}
    repository.close()


def test_normal_open_fails_closed_when_schema_metadata_is_missing(tmp_path: Path) -> None:
    """Permit metadata absence only while initializing a truly brand-new empty database."""
    malformed = tmp_path / "missing-meta.db"
    with sqlite3.connect(malformed) as conn:
        conn.execute("CREATE TABLE samples (sample_id TEXT PRIMARY KEY)")

    with pytest.raises(RuntimeError, match="schema version metadata"):
        ReviewStore(malformed).list_all_samples()


@pytest.mark.parametrize(("stored", "message"), [("corrupt", "invalid schema version"), ("999", "future review schema")])
def test_normal_open_fails_closed_for_corrupt_or_future_schema_metadata(
    tmp_path: Path, stored: str, message: str
) -> None:
    """Reject corrupt and future metadata during an ordinary store open without attempting repair."""
    path = tmp_path / f"schema-{stored}.db"
    store = ReviewStore(path)
    store.initialize()
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE studio_meta SET value=? WHERE key='schema_version'", (stored,))

    with pytest.raises(RuntimeError, match=message):
        store.list_all_samples()


def test_secured_review_open_detects_swap_before_transaction_and_preserves_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Detect a review-path swap after the claimed handle but before SQLite open without touching source bytes."""
    sources = _source_files(tmp_path)
    review_path = tmp_path / "review.db"
    store = ReviewStore(review_path, source_paths=sources)
    store.initialize()
    before = sources[0].read_bytes()
    displaced = tmp_path / "review-original.db"
    swapped = False

    def swap_before_open(path: Path) -> None:
        """Replace the claimed review path with a hardlink to a forbidden source exactly once."""
        nonlocal swapped
        if path == review_path and not swapped:
            os.replace(review_path, displaced)
            os.link(sources[0], review_path)
            swapped = True

    monkeypatch.setattr(review_store_module, "_before_sqlite_open", swap_before_open, raising=False)

    with pytest.raises(ValueError, match="changed during secured open|source database"):
        store.list_all_samples()
    assert sources[0].read_bytes() == before


def test_atomic_artifact_write_rechecks_a_swapped_destination_before_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Abort an artifact commit whose destination becomes a source hardlink after the temporary file is durable."""
    from gold_standard_studio.config import atomic_write_text

    sources = _source_files(tmp_path)
    destination = tmp_path / "report.html"
    destination.write_text("old", encoding="utf-8")
    displaced = tmp_path / "old-report.html"
    before = sources[0].read_bytes()

    def swap_before_replace(path: Path) -> None:
        """Replace the artifact destination with a forbidden hardlink at the deterministic commit seam."""
        os.replace(path, displaced)
        os.link(sources[0], path)

    monkeypatch.setattr(config_module, "_before_atomic_replace", swap_before_replace, raising=False)

    with pytest.raises(ValueError, match="source database"):
        atomic_write_text(destination, "new", source_paths=sources)
    assert sources[0].read_bytes() == before


def test_atomic_artifact_write_rejects_any_destination_identity_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse to overwrite an unrelated file introduced at the destination during the atomic-write window."""
    from gold_standard_studio.config import atomic_write_text

    destination = tmp_path / "report.html"
    destination.write_text("old", encoding="utf-8")
    displaced = tmp_path / "old-report.html"

    def swap_for_unrelated_file(path: Path) -> None:
        """Move the expected target aside and create a different destination inode at the race seam."""
        os.replace(path, displaced)
        path.write_text("racer", encoding="utf-8")

    monkeypatch.setattr(config_module, "_before_atomic_replace", swap_for_unrelated_file, raising=False)

    with pytest.raises(ValueError, match="destination changed"):
        atomic_write_text(destination, "new")
    assert destination.read_text(encoding="utf-8") == "racer"


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (EvidenceSpan, {"quote": "x", "start": "0", "end": 1, "article_sha256": "a" * 64}),
        (EvidenceSpan, {"quote": "x", "start": False, "end": 1, "article_sha256": "a" * 64}),
        (
            SamplingProvenance,
            {
                "sampling_seed": "17",
                "inclusion_reasons": [],
                "original_category_strata": [],
                "risk_tags": [],
                "negative_proxy": False,
            },
        ),
        (StructureRequest, {"base_revision": True, "projects": []}),
    ],
)
def test_formal_and_api_models_reject_coerced_scalar_types(model: type, payload: dict) -> None:
    """Reject strings and booleans that Pydantic would otherwise coerce at formal persistence/API boundaries."""
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_candidate_models_remain_permissive_for_observational_ingestion() -> None:
    """Keep external candidate envelopes tolerant while formal human decisions remain strict."""
    candidate = CandidateValue.model_validate(
        {"source_name": "review", "source_version": 7, "provider_extra": "ignored"}
    )

    assert candidate.source_name == "review"
    assert candidate.source_version == "7"


def test_confirmation_requires_sampling_provenance_registry_evidence_flag_and_named_role(
    tmp_path: Path,
) -> None:
    """Require formal provenance, registry-derived evidence flags, and a named non-Unknown Chinese role."""
    snapshot = _snapshot()
    store = ReviewStore(tmp_path / "review.db")
    store.initialize()
    no_provenance = _valid_record(snapshot, "NO-PROVENANCE")
    no_provenance.sampling_provenance = None
    store.seed_article(no_provenance, snapshot=snapshot)
    store.save_article_status("NO-PROVENANCE", "valid", actor="human")
    with pytest.raises(ValueError, match="sampling provenance"):
        store.confirm_article("NO-PROVENANCE", actor="human")

    _seed_valid(store, snapshot, "EVIDENCE-FLAG")
    with pytest.raises(ValueError, match="evidence_required"):
        store.save_field_decision(
            "EVIDENCE-FLAG",
            "P01.category",
            GoldField(state="not_mentioned", evidence_required=False),
            actor="human",
        )

    unknown_role = _valid_record(snapshot, "UNKNOWN-ROLE")
    unknown_role.projects[0].entities = [
        GoldEntity(name="中国电建", role="Unknown", evidence=[_span(snapshot, "中国电建")])
    ]
    store.seed_article(unknown_role, snapshot=snapshot)
    store.save_article_status("UNKNOWN-ROLE", "valid", actor="human")
    with pytest.raises(ValueError, match="non-Unknown|Unknown"):
        store.confirm_article("UNKNOWN-ROLE", actor="human")

    no_entity = _valid_record(snapshot, "NO-ENTITY")
    no_entity.projects[0].entities = []
    store.seed_article(no_entity, snapshot=snapshot)
    store.save_article_status("NO-ENTITY", "valid", actor="human")
    with pytest.raises(ValueError, match="named Chinese entity"):
        store.confirm_article("NO-ENTITY", actor="human")


def test_projection_emits_every_registry_key_and_preserves_exact_required_provenance(tmp_path: Path) -> None:
    """Emit explicit nulls for every not-mentioned field while retaining provenance without transformation."""
    from gold_standard_studio.eval_contract import load_projection

    snapshot = _snapshot()
    store = ReviewStore(tmp_path / "review.db")
    store.initialize()
    _seed_valid(store, snapshot)
    store.confirm_article("S001", actor="human")

    paths = publish_gold(store, tmp_path / "gold", "v3.0.0")
    raw = json.loads(paths.eval_projection.read_text(encoding="utf-8").splitlines()[0])
    loaded = load_projection(paths.eval_projection)[0]
    project = raw["projects"][0]

    assert set(FIELD_REGISTRY).issubset(project)
    assert project["country"] is None
    assert project["field_eligibility"]["country"] is True
    assert loaded["sampling_provenance"] == _valid_record(snapshot).sampling_provenance.model_dump(mode="json")


def test_atomic_negative_confirmation_clears_false_positive_structure_and_resolves_gate(tmp_path: Path) -> None:
    """Persist an explicit empty structure decision before confirming a negative despite candidate count conflict."""
    snapshot = _snapshot()
    store = ReviewStore(tmp_path / "review.db")
    store.initialize()
    record = _valid_record(snapshot)
    store.seed_article(record, snapshot=snapshot)
    store.save_candidate_bundle(
        CandidateBundle(
            sample_id="S001",
            article_sha256=snapshot.article_sha256,
            sources={
                "database": CandidateValue(source_name="database", projects=record.projects),
                "review": CandidateValue(source_name="review", projects=[]),
            },
        )
    )

    response = TestClient(
        create_app(store),
        headers={"X-Gold-Studio-Request": "1", "X-Review-Actor": "test-reviewer"},
    ).post(
        "/api/articles/S001/confirm-negative",
        json={"article_status": "chinese_unnamed"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["article"]["projects"] == []
    assert response.json()["article"]["article_status"] == "chinese_unnamed"
    assert store.structure_gate_status("S001") == (True, True)
    assert [entry.action for entry in store.list_audit("S001")][-3:] == [
        "structure_decision",
        "article_status_decision",
        "article_confirmed",
    ]


def test_public_api_reviews_every_registry_field_entity_and_publishes_through_cli(tmp_path: Path) -> None:
    """Complete one formal review exclusively through public API mutations and the public publish CLI."""
    from gold_standard_studio.cli import app

    snapshot = _snapshot()
    config, repository = _source_repository(tmp_path, [snapshot])
    repository.close()
    runner = CliRunner()
    initialized = runner.invoke(
        app,
        [
            "init",
            "--raw-news-db",
            str(config.raw_news_db),
            "--projects-db",
            str(config.projects_db),
            "--main-db",
            str(config.main_db),
            "--data-dir",
            str(config.data_dir),
        ],
    )
    assert initialized.exit_code == 0, initialized.output
    store = ReviewStore(config.data_dir / "review.db", source_paths=config.source_paths)
    store.seed_article(
        RichGoldRecord(
            sample_id="PUBLIC-E2E",
            source_link=snapshot.source_link,
            article_sha256=snapshot.article_sha256,
            sampling_provenance=SamplingProvenance(
                sampling_seed=20260806,
                inclusion_reasons=["public_e2e"],
                original_category_strata=["光伏"],
                risk_tags=[],
                negative_proxy=False,
                verified=True,
                verification_source="sample_selection",
            ),
        ),
        snapshot=snapshot,
    )
    client = TestClient(
        create_app(store),
        headers={"X-Gold-Studio-Request": "1", "X-Review-Actor": "test-reviewer"},
    )
    opened = client.get("/api/articles/PUBLIC-E2E")
    assert opened.status_code == 200, opened.text
    revision = opened.json()["structure_revision"]
    structured = client.put(
        "/api/articles/PUBLIC-E2E/structure",
        json={
            "base_revision": revision,
            "projects": [{"gold_project_id": "NEW-1", "fields": {}, "entities": []}],
        },
    )
    assert structured.status_code == 200, structured.text
    project_id = structured.json()["article"]["projects"][0]["gold_project_id"]

    for field_name, specification in FIELD_REGISTRY.items():
        saved = client.put(
            f"/api/articles/PUBLIC-E2E/fields/{project_id}.{field_name}",
            json={
                "field": {
                    "state": "not_mentioned",
                    "value": None,
                    "evidence": [],
                    "evidence_required": specification.evidence_required,
                }
            },
        )
        assert saved.status_code == 200, f"{field_name}: {saved.text}"

    pending = client.put(
        f"/api/articles/PUBLIC-E2E/fields/{project_id}.category",
        json={
            "field": {
                "state": "needs_adjudication",
                "value": None,
                "evidence": [],
                "evidence_required": True,
            }
        },
    )
    assert pending.status_code == 200, pending.text
    adjudicated = client.post(
        "/api/articles/PUBLIC-E2E/adjudication",
        json={
            "field_path": f"{project_id}.category",
            "field": {
                "state": "present",
                "value": "光伏",
                "evidence": [
                    _span(snapshot, "光伏").model_dump(mode="json"),
                    _span(snapshot, "100MW").model_dump(mode="json"),
                ],
                "evidence_required": True,
            },
        },
    )
    assert adjudicated.status_code == 200, adjudicated.text
    entities = client.put(
        f"/api/articles/PUBLIC-E2E/projects/{project_id}/entities",
        json={
            "entities": [
                {
                    "name": "中国电建",
                    "normalized_name": "中国电力建设集团有限公司",
                    "role": "EPC总承包",
                    "evidence": [_span(snapshot, "中国电建以EPC总承包商").model_dump(mode="json")],
                }
            ]
        },
    )
    assert entities.status_code == 200, entities.text
    status = client.put(
        "/api/articles/PUBLIC-E2E/status",
        json={"article_status": "valid"},
    )
    assert status.status_code == 200, status.text
    confirmed = client.post("/api/articles/PUBLIC-E2E/confirm")
    assert confirmed.status_code == 200, confirmed.text

    output = tmp_path / "published"
    published = runner.invoke(
        app,
        [
            "publish",
            "--version",
            "v9.0.0",
            "--output",
            str(output),
            "--data-dir",
            str(config.data_dir),
        ],
    )
    assert published.exit_code == 0, published.output
    projection = json.loads(
        (output / "v9.0.0" / "eval_projection.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert set(FIELD_REGISTRY).issubset(projection["projects"][0])
    assert projection["projects"][0]["category"] == "光伏"
    assert projection["sampling_provenance"]["sampling_seed"] == 20260806
