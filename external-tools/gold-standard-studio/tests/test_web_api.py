"""Exercise the local Studio API through its real review-store boundary."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gold_standard_studio.review_store import ReviewStore
from gold_standard_studio.schemas import (
    CandidateBundle,
    CandidateValue,
    EvidenceSpan,
    GoldField,
    GoldProject,
    RichGoldRecord,
)
from gold_standard_studio.web.app import create_app


ARTICLE_SHA256 = "a" * 64


def make_field(value: str, *, state: str = "present") -> GoldField:
    """Build one noncritical test field with a hand-checked literal value."""
    return GoldField(state=state, value=value if state == "present" else None)


def make_project_json(project_id: str) -> dict[str, object]:
    """Build one complete project payload accepted by the structure endpoint."""
    return {
        "gold_project_id": project_id,
        "fields": {"category": make_field("风电").model_dump(mode="json")},
        "entities": [],
    }


def make_article(sample_id: str, *, difficulty: float = 0) -> RichGoldRecord:
    """Build a reviewable persisted draft with an explicit stable ID and score."""
    return RichGoldRecord(
        sample_id=sample_id,
        article_sha256=ARTICLE_SHA256,
        source_link=f"https://example.test/{sample_id}",
        projects=[GoldProject(gold_project_id="P01", fields={"category": make_field("风电")})],
        difficulty={"score": difficulty},
    )


@pytest.fixture
def store(tmp_path: Path) -> ReviewStore:
    """Create a real isolated review database populated in deterministic queue order."""
    review_store = ReviewStore(tmp_path / "review.db")
    review_store.initialize()
    review_store.seed_article(make_article("S001", difficulty=1))
    review_store.seed_article(make_article("S002", difficulty=0))
    return review_store


@pytest.fixture
def client(store: ReviewStore) -> TestClient:
    """Expose the application using the real SQLite store rather than mocks."""
    return TestClient(create_app(store))


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
        evidence=[EvidenceSpan(quote="光伏", start=0, end=2, article_sha256=ARTICLE_SHA256)],
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


def test_confirmation_rejects_registered_field_without_required_evidence(client: TestClient) -> None:
    """Require article evidence for a present field marked critical by the field registry."""
    response = client.post("/api/articles/S001/confirm")

    assert response.status_code == 422
    assert response.json()["detail"] == "missing required evidence: P01.category"


def test_adjudication_requires_a_field_held_for_adjudication(client: TestClient) -> None:
    """Prevent the adjudication route from becoming a second unrestricted field editor."""
    response = client.post(
        "/api/articles/S001/adjudication",
        json={"field_path": "P01.category", "field": make_field("光伏").model_dump(mode="json")},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "field is not awaiting adjudication: P01.category"


def test_api_actions_leave_all_source_database_hashes_unchanged(tmp_path: Path, client: TestClient) -> None:
    """Keep API mutations confined to the review store while three source SQLite files stay byte-identical."""
    source_paths = [tmp_path / name for name in ("raw_news.db", "projects.db", "main.db")]
    for path in source_paths:
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE source_rows (value TEXT)")
            conn.execute("INSERT INTO source_rows VALUES ('immutable')")
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}

    assert client.post("/api/articles/S001/start").status_code == 200
    assert client.put(
        "/api/articles/S001/fields/P01.category",
        json={"field": make_field("光伏").model_dump(mode="json")},
    ).status_code == 200
    after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}

    assert after == before


def test_article_conflicts_are_read_only_candidate_context(store: ReviewStore, client: TestClient) -> None:
    """Report candidate disagreement without allowing it to mutate the human draft automatically."""
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
