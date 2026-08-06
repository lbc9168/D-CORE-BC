"""Serve explicit local Studio review routes without exposing source databases or arbitrary storage access."""

from __future__ import annotations

from typing import Any, Annotated

from fastapi import FastAPI, Header, Path, Request
from pydantic import BaseModel, Field

from gold_standard_studio.evidence import FieldConflict, compare_candidates
from gold_standard_studio.review_store import AuditEntry, QueueItem, ReviewStore
from gold_standard_studio.schemas import CandidateBundle, GoldField, GoldProject, RichGoldRecord
from gold_standard_studio.sources import SourceRepository


class QueueItemResponse(BaseModel):
    """Serialize one deterministic queue row without exposing SQLite implementation details."""

    sample_id: str
    review_status: str
    difficulty_score: float


class QueueResponse(BaseModel):
    """Serialize the current outstanding review queue."""

    items: list[QueueItemResponse]


class ConflictResponse(BaseModel):
    """Serialize one read-only candidate disagreement for review presentation."""

    field_name: str
    project_index: int | None
    source_values: dict[str, Any]
    normalized_values: dict[str, Any]
    reason_code: str
    error_sources: list[str]


class ArticleResponse(BaseModel):
    """Serialize an article draft and its review-only context in one stable response shape."""

    article: RichGoldRecord
    projects: list[GoldProject]
    structure_revision: int
    candidate_bundle: CandidateBundle | None
    conflicts: list[ConflictResponse]


class StructureRequest(BaseModel):
    """Require an entire ordered project structure and the revision it was based on."""

    base_revision: int
    projects: list[GoldProject]


class FieldDecisionRequest(BaseModel):
    """Carry one validated replacement formal field for an existing project field path."""

    field: GoldField


class AdjudicationRequest(FieldDecisionRequest):
    """Resolve one field that has explicitly been placed in the needs-adjudication state."""

    field_path: str = Field(min_length=3, max_length=160)


class StartResponse(BaseModel):
    """Acknowledge a durable review-session start event."""

    sample_id: str
    actor: str


class AuditEntryResponse(BaseModel):
    """Serialize append-only audit data including complete before-and-after payloads."""

    id: int
    sample_id: str
    action: str
    field_path: str | None
    old_value: Any
    new_value: Any
    old_payload: Any
    new_payload: Any
    actor: str
    created_at: str


class AuditResponse(BaseModel):
    """Serialize every audit event for one explicitly addressed article."""

    items: list[AuditEntryResponse]


LocalActorHeader = Annotated[str | None, Header(alias="X-Review-Actor")]


def create_app(store: ReviewStore, *, source_repository: SourceRepository | None = None) -> FastAPI:
    """Create a local FastAPI app that can mutate only the supplied isolated review store."""
    app = FastAPI(title="Golden Standard Studio", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.review_store = store
    app.state.source_repository = source_repository

    @app.exception_handler(ValueError)
    async def map_store_error(_request: Request, error: ValueError):
        """Map expected review-domain failures to deterministic public HTTP errors."""
        detail = str(error)
        status_code = 404 if detail.startswith("unknown sample '") else 409 if detail.startswith("stale structure revision:") else 422
        return _error_response(status_code, detail)

    @app.get("/api/queue", response_model=QueueResponse)
    def get_queue() -> QueueResponse:
        """Return outstanding draft items in deterministic low-difficulty order."""
        return QueueResponse(items=[_queue_item_response(item) for item in store.list_queue()])

    @app.get("/api/articles/{sample_id}", response_model=ArticleResponse)
    def get_article(sample_id: str = Path(min_length=1, max_length=128)) -> ArticleResponse:
        """Return one article together with its structure revision and read-only candidate conflicts."""
        return _article_response(store, sample_id)

    @app.post("/api/articles/{sample_id}/start", response_model=StartResponse)
    def start_article(sample_id: str, actor_header: LocalActorHeader = None) -> StartResponse:
        """Durably record that the local actor began reviewing the selected article."""
        actor = _local_actor(actor_header)
        store.start_review(sample_id, actor=actor)
        return StartResponse(sample_id=sample_id, actor=actor)

    @app.put("/api/articles/{sample_id}/structure", response_model=ArticleResponse)
    def replace_structure(
        payload: StructureRequest, sample_id: str, actor_header: LocalActorHeader = None
    ) -> ArticleResponse:
        """Atomically replace an article's full project structure at its expected revision."""
        store.save_structure(
            sample_id,
            payload.projects,
            base_revision=payload.base_revision,
            actor=_local_actor(actor_header),
        )
        return _article_response(store, sample_id)

    @app.put("/api/articles/{sample_id}/fields/{field_path:path}", response_model=ArticleResponse)
    def replace_field(
        payload: FieldDecisionRequest,
        sample_id: str,
        field_path: str,
        actor_header: LocalActorHeader = None,
    ) -> ArticleResponse:
        """Atomically save one validated field decision at an existing safe draft path."""
        store.save_field_decision(sample_id, field_path, payload.field, actor=_local_actor(actor_header))
        return _article_response(store, sample_id)

    @app.post("/api/articles/{sample_id}/confirm", response_model=ArticleResponse)
    def confirm_article(sample_id: str, actor_header: LocalActorHeader = None) -> ArticleResponse:
        """Confirm only a draft whose fields have evidence and no pending adjudication."""
        store.confirm_article(sample_id, actor=_local_actor(actor_header))
        return _article_response(store, sample_id)

    @app.post("/api/articles/{sample_id}/adjudication", response_model=ArticleResponse)
    def adjudicate_field(
        payload: AdjudicationRequest, sample_id: str, actor_header: LocalActorHeader = None
    ) -> ArticleResponse:
        """Resolve exactly one pending adjudication with an explicit human replacement field decision."""
        store.adjudicate_field(sample_id, payload.field_path, payload.field, actor=_local_actor(actor_header))
        return _article_response(store, sample_id)

    @app.get("/api/articles/{sample_id}/audit", response_model=AuditResponse)
    def get_audit(sample_id: str = Path(min_length=1, max_length=128)) -> AuditResponse:
        """Return complete serialized audit events for one requested article."""
        return AuditResponse(items=[_audit_entry_response(item) for item in store.list_audit(sample_id)])

    return app


def _error_response(status_code: int, detail: str):
    """Build the narrow JSON error envelope shared by all known local review-domain errors."""
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status_code, content={"detail": detail})


def _article_response(store: ReviewStore, sample_id: str) -> ArticleResponse:
    """Build the stable article payload from isolated persisted state and read-only candidate comparisons."""
    article = store.get_article(sample_id)
    bundle = store.get_candidate_bundle(sample_id)
    conflicts = compare_candidates(bundle) if bundle is not None else []
    return ArticleResponse(
        article=article,
        projects=article.projects,
        structure_revision=store.current_structure_revision(sample_id),
        candidate_bundle=bundle,
        conflicts=[_conflict_response(conflict) for conflict in conflicts],
    )


def _queue_item_response(item: QueueItem) -> QueueItemResponse:
    """Convert one store queue item into an explicit Pydantic response model."""
    return QueueItemResponse(
        sample_id=item.sample_id,
        review_status=item.review_status,
        difficulty_score=item.difficulty_score,
    )


def _conflict_response(conflict: FieldConflict) -> ConflictResponse:
    """Convert one candidate conflict dataclass into JSON-safe response content."""
    return ConflictResponse(
        field_name=conflict.field_name,
        project_index=conflict.project_index,
        source_values=dict(conflict.source_values),
        normalized_values=dict(conflict.normalized_values),
        reason_code=conflict.reason_code,
        error_sources=list(conflict.error_sources),
    )


def _audit_entry_response(entry: AuditEntry) -> AuditEntryResponse:
    """Convert complete audit data without discarding Pydantic field or candidate payload detail."""
    return AuditEntryResponse(
        id=entry.id,
        sample_id=entry.sample_id,
        action=entry.action,
        field_path=entry.field_path,
        old_value=_json_value(entry.old_value),
        new_value=_json_value(entry.new_value),
        old_payload=_json_value(entry.old_payload),
        new_payload=_json_value(entry.new_payload),
        actor=entry.actor,
        created_at=entry.created_at,
    )


def _json_value(value: Any) -> Any:
    """Turn Pydantic audit payloads into JSON-compatible data while leaving primitives unchanged."""
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _local_actor(value: str | None) -> str:
    """Return a bounded local actor label while rejecting control characters and spoofable blank values."""
    actor = value.strip() if value is not None else "local-reviewer"
    if not actor or len(actor) > 80 or any(character.isspace() and character not in {" "} for character in actor):
        raise ValueError("X-Review-Actor must be a nonblank single-line value up to 80 characters")
    return actor
