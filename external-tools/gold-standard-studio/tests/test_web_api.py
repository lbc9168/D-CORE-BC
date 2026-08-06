"""Exercise the local Studio API through its real review-store boundary."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gold_standard_studio.config import StudioConfig
from gold_standard_studio.field_registry import FIELD_REGISTRY
import gold_standard_studio.review_store as review_store_module
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
from gold_standard_studio.web.app import create_app


ARTICLE_TITLE = "复核测试"
ARTICLE_CONTENT = "风电光伏"
ARTICLE_DATE = "2026-08-06"
ARTICLE_SHA256 = article_sha256(ARTICLE_TITLE, ARTICLE_CONTENT, ARTICLE_DATE)


def make_snapshot(source_link: str) -> ArticleSnapshot:
    """Build the canonical immutable article snapshot used by API fixtures."""
    return ArticleSnapshot(
        source_link=source_link,
        title=ARTICLE_TITLE,
        content=ARTICLE_CONTENT,
        publish_date=ARTICLE_DATE,
        article_sha256=ARTICLE_SHA256,
        raw_values={},
        display_values={},
    )


def make_field(value: str, *, state: str = "present") -> GoldField:
    """Build one registry-compliant critical fixture field with exact current-article evidence."""
    evidence = []
    if state == "present":
        quote = value if value in ARTICLE_CONTENT else "风电"
        start = ARTICLE_CONTENT.index(quote)
        evidence = [EvidenceSpan(quote=quote, start=start, end=start + len(quote), article_sha256=ARTICLE_SHA256)]
    return GoldField(
        state=state,
        value=value if state == "present" else None,
        evidence=evidence,
        evidence_required=True,
    )


def make_project_json(project_id: str) -> dict[str, object]:
    """Build one complete project payload accepted by the structure endpoint."""
    fields = {
        name: GoldField(state="not_mentioned", evidence_required=spec.evidence_required).model_dump(mode="json")
        for name, spec in FIELD_REGISTRY.items()
    }
    fields["category"] = make_field("风电").model_dump(mode="json")
    return {
        "gold_project_id": project_id,
        "fields": fields,
        "entities": [],
    }


def make_article(sample_id: str, *, difficulty: float = 0) -> RichGoldRecord:
    """Build a reviewable persisted draft with an explicit stable ID and score."""
    fields = {
        name: GoldField(state="not_mentioned", evidence_required=spec.evidence_required)
        for name, spec in FIELD_REGISTRY.items()
    }
    fields["category"] = make_field("风电")
    return RichGoldRecord(
        sample_id=sample_id,
        article_sha256=ARTICLE_SHA256,
        source_link=f"https://example.test/{sample_id}",
        article_status="valid",
        sampling_provenance=SamplingProvenance(
            sampling_seed=20260806,
            inclusion_reasons=["web_fixture"],
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
        difficulty={"score": difficulty},
    )


@pytest.fixture
def store(tmp_path: Path) -> ReviewStore:
    """Create a real isolated review database populated in deterministic queue order."""
    review_store = ReviewStore(tmp_path / "review.db")
    review_store.initialize()
    for article in (make_article("S001", difficulty=1), make_article("S002", difficulty=0)):
        review_store.seed_article(article, snapshot=make_snapshot(article.source_link))
        review_store.save_article_status(article.sample_id, "valid", actor="fixture")
    return review_store


@pytest.fixture
def client(store: ReviewStore) -> TestClient:
    """Expose the application using the real SQLite store rather than mocks."""
    return TestClient(create_app(store))


@pytest.fixture
def configured_source_client(tmp_path: Path) -> tuple[TestClient, SourceRepository]:
    """Build the actual configured source repositories used to derive an API review draft."""
    raw_news_db, projects_db, main_db = (tmp_path / name for name in ("raw_news.db", "projects.db", "main.db"))
    with sqlite3.connect(raw_news_db) as conn:
        conn.execute("CREATE TABLE raw_news (url TEXT PRIMARY KEY, title TEXT, content TEXT, publish_date TEXT)")
        conn.execute(
            "INSERT INTO raw_news VALUES (?, ?, ?, ?)",
            ("https://example.test/source-backed", "标题", "光伏项目证据", "2026-08-06"),
        )
    with sqlite3.connect(projects_db) as conn:
        conn.execute(
            "CREATE TABLE extracted_projects (id INTEGER PRIMARY KEY, source_link TEXT, category TEXT, imported_to_s02a INTEGER)"
        )
    with sqlite3.connect(main_db) as conn:
        conn.executescript(
            """CREATE TABLE canonical_projects (id TEXT PRIMARY KEY, project_name TEXT, category TEXT);
            CREATE TABLE project_events (id TEXT PRIMARY KEY, canonical_id TEXT, source_links TEXT);
            CREATE TABLE chinese_entities (id INTEGER PRIMARY KEY, event_id TEXT);"""
        )
        conn.execute("INSERT INTO canonical_projects VALUES (?, ?, ?)", ("P-1", "光伏项目", "光伏"))
        conn.execute(
            "INSERT INTO project_events VALUES (?, ?, ?)",
            ("E-1", "P-1", '["https://example.test/source-backed"]'),
        )
    config = StudioConfig.from_paths(
        raw_news_db=raw_news_db,
        projects_db=projects_db,
        main_db=main_db,
        data_dir=tmp_path / "studio",
    )
    repository = SourceRepository(config)
    snapshot = repository.load_article("https://example.test/source-backed")
    labels = repository.load_current_labels(snapshot.source_link)
    review_store = ReviewStore(tmp_path / "studio" / "review.db", source_paths=config.source_paths)
    review_store.initialize()
    review_store.seed_article(
        RichGoldRecord(
            sample_id="SRC001",
            article_sha256=snapshot.article_sha256,
            source_link=snapshot.source_link,
            article_status="valid",
            sampling_provenance=SamplingProvenance(
                sampling_seed=20260806,
                inclusion_reasons=["source_fixture"],
                original_category_strata=["光伏"],
                risk_tags=[],
                negative_proxy=False,
            ),
            projects=[
                GoldProject(
                    gold_project_id="P01",
                    fields={
                        **{
                            name: GoldField(
                                state="not_mentioned",
                                evidence_required=specification.evidence_required,
                            )
                            for name, specification in FIELD_REGISTRY.items()
                        },
                        "category": GoldField(
                            state="present",
                            value=labels[0].category,
                            evidence_required=True,
                            evidence=[
                                EvidenceSpan(
                                    quote="光伏",
                                    start=0,
                                    end=2,
                                    article_sha256=snapshot.article_sha256,
                                )
                            ],
                        ),
                    },
                    entities=[
                        GoldEntity(
                            name="光伏",
                            role="EPC总承包",
                            evidence=[
                                EvidenceSpan(
                                    quote="光伏",
                                    start=0,
                                    end=2,
                                    article_sha256=snapshot.article_sha256,
                                )
                            ],
                        )
                    ],
                )
            ],
        ),
        snapshot=snapshot,
    )
    review_store.save_article_status("SRC001", "valid", actor="fixture")
    return TestClient(create_app(review_store, source_repository=repository)), repository


def test_queue_defaults_to_ready_low_difficulty(client: TestClient) -> None:
    """Return outstanding articles in the store's deterministic review order."""
    response = client.get("/api/queue")

    assert response.status_code == 200
    assert [row["sample_id"] for row in response.json()["items"]] == ["S002", "S001"]


def test_article_response_includes_revision_candidates_and_conflicts(client: TestClient) -> None:
    """Expose the article review context without accepting client-controlled database access."""
    response = client.get("/api/articles/S001")

    assert response.status_code == 200
    assert response.json()["structure_revision"] == 1
    assert response.json()["article"]["sample_id"] == "S001"
    assert response.json()["candidate_bundle"] is None
    assert response.json()["conflicts"] == []
    assert set(response.json()["field_registry"]) == set(FIELD_REGISTRY)
    assert "EPC总承包" in response.json()["entity_roles"]


def test_structure_revision_can_split_project(client: TestClient) -> None:
    """Replace the complete project list through one optimistic-concurrency write."""
    payload = {"base_revision": 1, "projects": [make_project_json("P01"), make_project_json("P02")]}

    response = client.put("/api/articles/S001/structure", json=payload)

    assert response.status_code == 200
    assert [project["gold_project_id"] for project in response.json()["projects"]] == ["P01", "P02"]
    assert response.json()["structure_revision"] == 2


def test_stale_revision_is_rejected(client: TestClient) -> None:
    """Map the store's optimistic-concurrency conflict to the stable HTTP 409 contract."""
    response = client.put("/api/articles/S001/structure", json={"base_revision": 0, "projects": []})

    assert response.status_code == 409
    assert response.json()["detail"] == "stale structure revision: expected 1, received 0"


def test_field_decision_records_default_local_actor_and_audit(client: TestClient) -> None:
    """Persist a field decision atomically and serialize its complete audit payload."""
    response = client.put(
        "/api/articles/S001/fields/P01.category",
        json={"field": make_field("光伏").model_dump(mode="json")},
    )

    assert response.status_code == 200
    assert response.json()["article"]["projects"][0]["fields"]["category"]["value"] == "光伏"
    audit = client.get("/api/articles/S001/audit").json()["items"][-1]
    assert audit["action"] == "field_decision"
    assert audit["actor"] == "local-reviewer"
    assert audit["old_payload"]["value"] == "风电"
    assert audit["new_payload"]["value"] == "光伏"


def test_unknown_article_is_not_found_and_unsafe_field_path_is_invalid(client: TestClient) -> None:
    """Reject unknown records and malformed nested paths without exposing storage internals."""
    missing = client.get("/api/articles/unknown")
    unsafe = client.put(
        "/api/articles/S001/fields/P01.category.extra",
        json={"field": make_field("光伏").model_dump(mode="json")},
    )

    assert missing.status_code == 404
    assert unsafe.status_code == 422


def test_confirmation_rejects_needs_adjudication(client: TestClient) -> None:
    """Block confirmation until an explicit adjudication clears the held field state."""
    client.put(
        "/api/articles/S001/fields/P01.category",
        json={"field": make_field("", state="needs_adjudication").model_dump(mode="json")},
    )

    rejected = client.post("/api/articles/S001/confirm")
    resolved_field = GoldField(
        state="present",
        value="光伏",
        evidence_required=True,
        evidence=[EvidenceSpan(quote="光伏", start=2, end=4, article_sha256=ARTICLE_SHA256)],
    )
    resolved = client.post(
        "/api/articles/S001/adjudication",
        json={"field_path": "P01.category", "field": resolved_field.model_dump(mode="json")},
    )
    confirmed = client.post("/api/articles/S001/confirm")

    assert rejected.status_code == 422
    assert resolved.status_code == 200
    assert resolved.json()["article"]["projects"][0]["fields"]["category"]["value"] == "光伏"
    assert client.get("/api/articles/S001/audit").json()["items"][-2]["action"] == "adjudication"
    assert confirmed.status_code == 200
    assert confirmed.json()["article"]["article_review_status"] == "reviewed"


def test_api_rejects_registered_evidence_flag_mismatch(client: TestClient) -> None:
    """Reject an API field decision whose evidence flag disagrees with the server registry."""
    response = client.put(
        "/api/articles/S001/fields/P01.category",
        json={
            "field": {
                "state": "not_mentioned",
                "value": None,
                "evidence": [],
                "evidence_required": False,
            }
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "category evidence_required must match the field registry"


def test_adjudication_requires_a_field_held_for_adjudication(client: TestClient) -> None:
    """Prevent the adjudication route from becoming a second unrestricted field editor."""
    response = client.post(
        "/api/articles/S001/adjudication",
        json={"field_path": "P01.category", "field": make_field("光伏").model_dump(mode="json")},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "field is not awaiting adjudication: P01.category"


def test_generic_api_field_route_cannot_bypass_adjudication(client: TestClient) -> None:
    """Reject an ordinary API field write that attempts to resolve a pending adjudication field."""
    client.put(
        "/api/articles/S001/fields/P01.category",
        json={"field": make_field("", state="needs_adjudication").model_dump(mode="json")},
    )

    response = client.put(
        "/api/articles/S001/fields/P01.category",
        json={"field": make_field("光伏").model_dump(mode="json")},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "field resolution requires adjudication: P01.category"


def test_reviewed_article_mutation_routes_return_conflict_and_leave_store_unchanged(
    store: ReviewStore, client: TestClient
) -> None:
    """Reject post-confirmation field, structure, adjudication, and session mutations with HTTP 409."""
    evidenced = GoldField(
        state="present",
        value="风电",
        evidence_required=True,
        evidence=[EvidenceSpan(quote="风电", start=0, end=2, article_sha256=ARTICLE_SHA256)],
    )
    store.save_field_decision("S001", "P01.category", evidenced, actor="human")
    assert client.post("/api/articles/S001/confirm").status_code == 200
    before_article = store.get_article("S001").model_dump(mode="json")
    before_revision = store.current_structure_revision("S001")
    before_tail = store.latest_audit_id()

    responses = [
        client.put("/api/articles/S001/fields/P01.category", json={"field": make_field("光伏").model_dump(mode="json")}),
        client.put("/api/articles/S001/structure", json={"base_revision": before_revision, "projects": []}),
        client.post(
            "/api/articles/S001/adjudication",
            json={"field_path": "P01.category", "field": make_field("光伏").model_dump(mode="json")},
        ),
        client.post("/api/articles/S001/start"),
    ]

    assert [response.status_code for response in responses] == [409, 409, 409, 409]
    assert all(response.json()["detail"] == "reviewed records are immutable" for response in responses)
    assert store.get_article("S001").model_dump(mode="json") == before_article
    assert store.current_structure_revision("S001") == before_revision
    assert store.latest_audit_id() == before_tail


def test_api_actions_leave_configured_source_repository_hashes_unchanged(
    configured_source_client: tuple[TestClient, SourceRepository],
) -> None:
    """Keep every API action away from the configured source databases that seeded its review draft."""
    source_client, repository = configured_source_client
    before = repository.snapshot_hashes()
    category = GoldField(
        state="present",
        value="光伏",
        evidence_required=True,
        evidence=[
            EvidenceSpan(
                quote="光伏",
                start=0,
                end=2,
                article_sha256=repository.load_article("https://example.test/source-backed").article_sha256,
            )
        ],
    ).model_dump(mode="json")

    assert source_client.get("/api/queue").status_code == 200
    assert source_client.get("/api/articles/SRC001").status_code == 200
    assert source_client.post("/api/articles/SRC001/start").status_code == 200
    source_project = make_project_json("P01")
    source_project["fields"]["category"] = category
    source_project["entities"] = source_client.get("/api/articles/SRC001").json()["article"]["projects"][0]["entities"]
    assert source_client.put(
        "/api/articles/SRC001/structure",
        json={"base_revision": 1, "projects": [source_project]},
    ).status_code == 200
    assert source_client.put("/api/articles/SRC001/fields/P01.category", json={"field": category}).status_code == 200
    assert source_client.put(
        "/api/articles/SRC001/fields/P01.category",
        json={"field": make_field("", state="needs_adjudication").model_dump(mode="json")},
    ).status_code == 200
    assert source_client.post(
        "/api/articles/SRC001/adjudication", json={"field_path": "P01.category", "field": category}
    ).status_code == 200
    confirmation = source_client.post("/api/articles/SRC001/confirm")
    assert confirmation.status_code == 200, confirmation.text
    assert source_client.get("/api/articles/SRC001/audit").status_code == 200

    assert repository.snapshot_hashes() == before


def test_article_conflicts_are_read_only_candidate_context(store: ReviewStore, client: TestClient) -> None:
    """Report candidate disagreement without allowing it to mutate the human draft automatically."""
    store.save_structure(
        "S001",
        [
            GoldProject(
                gold_project_id="P01",
                fields={"project_name": make_field("示例项目"), "category": make_field("风电")},
            )
        ],
        base_revision=1,
        actor="human",
    )
    store.save_candidate_bundle(
        CandidateBundle(
            sample_id="S001",
            article_sha256=ARTICLE_SHA256,
            sources={
                "database": CandidateValue(
                    source_name="database",
                    projects=[
                        GoldProject(
                            gold_project_id="D1",
                            fields={"project_name": make_field("示例项目"), "category": make_field("风电")},
                        )
                    ],
                ),
                "review": CandidateValue(
                    source_name="review",
                    projects=[
                        GoldProject(
                            gold_project_id="R1",
                            fields={"project_name": make_field("示例项目"), "category": make_field("光伏")},
                        )
                    ],
                ),
            },
        )
    )

    response = client.get("/api/articles/S001")

    assert response.status_code == 200
    assert response.json()["conflicts"][0]["field_name"] == "category"


def test_article_projects_receive_server_aligned_candidates_when_sources_are_reversed(
    store: ReviewStore, client: TestClient
) -> None:
    """Project candidate provenance follows stable project identity instead of every source's list order."""
    article = RichGoldRecord(
        sample_id="S003",
        article_sha256=ARTICLE_SHA256,
        projects=[
            GoldProject(
                gold_project_id="P01",
                fields={"project_name": make_field("风电项目"), "category": make_field("风电")},
            ),
            GoldProject(
                gold_project_id="P02",
                fields={"project_name": make_field("光伏项目"), "category": make_field("风电")},
            ),
        ],
    )
    store.seed_article(article)
    store.save_candidate_bundle(
        CandidateBundle(
            sample_id="S003",
            article_sha256=ARTICLE_SHA256,
            sources={
                "database": CandidateValue(
                    source_name="database",
                    projects=[
                        GoldProject(gold_project_id="D-PV", fields={"project_name": make_field("光伏项目"), "category": make_field("光伏")}),
                        GoldProject(gold_project_id="D-WIND", fields={"project_name": make_field("风电项目"), "category": make_field("风电")}),
                    ],
                ),
                "review": CandidateValue(
                    source_name="review",
                    projects=[
                        GoldProject(gold_project_id="R-PV", fields={"project_name": make_field("光伏项目"), "category": make_field("光伏")}),
                        GoldProject(gold_project_id="R-WIND", fields={"project_name": make_field("风电项目"), "category": make_field("风电")}),
                    ],
                ),
            },
        )
    )

    response = client.get("/api/articles/S003")

    assert response.status_code == 200
    pv_suggestion = next(item for item in response.json()["field_candidates"]["P02.category"] if item["source_name"] == "review")
    assert pv_suggestion["source_project_id"] == "R-PV"
    assert pv_suggestion["source_project_index"] == 0
    assert pv_suggestion["field"]["value"] == "光伏"


def test_confirmation_requires_newer_persisted_structure_decision_for_structural_conflict(
    store: ReviewStore, client: TestClient
) -> None:
    """Block direct or shortcut confirmation until a structural candidate conflict has an audited resolution."""
    store.save_candidate_bundle(
        CandidateBundle(
            sample_id="S001",
            article_sha256=ARTICLE_SHA256,
            sources={
                "database": CandidateValue(source_name="database", projects=[GoldProject(gold_project_id="D1")]),
                "review": CandidateValue(
                    source_name="review",
                    projects=[GoldProject(gold_project_id="R1"), GoldProject(gold_project_id="R2")],
                ),
            },
        )
    )

    before = client.get("/api/articles/S001").json()
    rejected = client.post("/api/articles/S001/confirm")
    saved = client.put("/api/articles/S001/structure", json={"base_revision": 1, "projects": before["projects"]})
    after = client.get("/api/articles/S001").json()

    assert before["structure_gate"] == {"required": True, "resolved": False}
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "unresolved structure conflicts prevent article confirmation"
    assert saved.status_code == 200
    assert after["structure_gate"] == {"required": True, "resolved": True}


def test_structure_gate_uses_monotonic_audit_order_not_wall_clock(store: ReviewStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve only candidate-to-structure audit order even when every write shares the same timestamp."""
    monkeypatch.setattr(review_store_module, "_utc_now", lambda: "2026-08-06T00:00:00+00:00")
    structural_bundle = CandidateBundle(
        sample_id="S001",
        article_sha256=ARTICLE_SHA256,
        sources={
            "database": CandidateValue(source_name="database", projects=[GoldProject(gold_project_id="D1")]),
            "review": CandidateValue(source_name="review", projects=[GoldProject(gold_project_id="R1"), GoldProject(gold_project_id="R2")]),
        },
    )

    store.save_candidate_bundle(structural_bundle)
    assert store.structure_gate_status("S001") == (True, False)
    store.save_structure("S001", store.get_article("S001").projects, base_revision=1, actor="human")
    assert store.structure_gate_status("S001") == (True, True)
    store.save_candidate_bundle(structural_bundle)
    assert store.structure_gate_status("S001") == (True, False)


def test_candidate_projection_filters_wrong_hash_evidence_and_marks_warning(store: ReviewStore, client: TestClient) -> None:
    """Never expose a candidate's stale evidence span as valid evidence for the current article snapshot."""
    store.save_structure(
        "S001",
        [GoldProject(gold_project_id="P01", fields={"project_name": make_field("项目"), "category": make_field("风电")})],
        base_revision=1,
        actor="human",
    )
    store.save_candidate_bundle(
        CandidateBundle(
            sample_id="S001",
            article_sha256=ARTICLE_SHA256,
            sources={
                "review": CandidateValue(source_name="review", projects=[GoldProject(
                    gold_project_id="R01",
                    fields={
                        "project_name": make_field("项目"),
                        "category": GoldField(
                            state="present", value="光伏",
                            evidence=[EvidenceSpan(quote="光伏", start=0, end=2, article_sha256="b" * 64)],
                        ),
                    },
                )]),
            },
        )
    )

    candidate = client.get("/api/articles/S001").json()["field_candidates"]["P01.category"][0]

    assert candidate["field"]["evidence"] == []
    assert candidate["evidence_warning"] is True


def test_present_and_not_mentioned_aligned_candidates_are_a_field_state_conflict(store: ReviewStore, client: TestClient) -> None:
    """Treat a present candidate against a missing-state candidate as reviewable disagreement, not consensus."""
    store.save_structure(
        "S001", [GoldProject(gold_project_id="P01", fields={"project_name": make_field("项目"), "category": make_field("风电")})], base_revision=1, actor="human"
    )
    store.save_candidate_bundle(
        CandidateBundle(sample_id="S001", article_sha256=ARTICLE_SHA256, sources={
            "database": CandidateValue(source_name="database", projects=[GoldProject(gold_project_id="D1", fields={"project_name": make_field("项目"), "category": make_field("", state="not_mentioned")})]),
            "review": CandidateValue(source_name="review", projects=[GoldProject(gold_project_id="R1", fields={"project_name": make_field("项目"), "category": make_field("光伏")})]),
        })
    )

    conflicts = client.get("/api/articles/S001").json()["conflicts"]

    assert [(item["field_name"], item["reason_code"]) for item in conflicts] == [("category", "field_state_disagreement")]
