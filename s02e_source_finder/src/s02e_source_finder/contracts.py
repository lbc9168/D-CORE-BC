"""Validated single-event inputs and evidence-based reference decisions."""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Reject unexpected fields instead of silently accepting malformed model output."""

    model_config = ConfigDict(extra="forbid")


class EventContext(StrictModel):
    """Read-only project/event facts and the source articles used for matching."""

    project_id: str
    event_id: str
    project: dict
    event: dict
    entities: list[dict]
    source_articles: list[dict]

    def fingerprint(self) -> str:
        """Bind a decision to the complete input facts rather than a news URL alone."""
        payload = self.model_dump()
        # Candidate changes must not invalidate facts that have not changed.
        for key in ("official_website_links", "non_official_website_links"):
            payload["event"].pop(key, None)
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class SourceStatement(StrictModel):
    """A visible source label outside the extracted article body, with its HTML locator."""

    name: str = Field(min_length=1, max_length=160)
    evidence: str = Field(min_length=1, max_length=300)
    selector: str = Field(min_length=1, max_length=100)
    link: str | None = None


class Page(StrictModel):
    """Fetched content and transport outcome; a successful status alone is not evidence."""

    url: str
    final_url: str
    checked_at: str
    http_status: int | None = None
    title: str = ""
    site_name: str = ""
    identity_text: str = ""  # Visible masthead/footer identity, separate from article facts.
    publish_date: str | None = None
    text: str = ""
    text_hash: str = ""
    problem: str | None = None
    source_statements: list[SourceStatement] = Field(default_factory=list)


class Judgement(StrictModel):
    """Model verdict with directly locatable evidence for identity and milestone."""

    decision: Literal["event_reference", "project_reference", "pending", "rejected"]
    reason: str = Field(min_length=1, max_length=1600)
    language: Literal["zh", "other", "unknown"]
    publisher: str
    source_type: Literal["official", "media", "unknown"]
    publisher_evidence: str
    same_project: bool
    project_evidence: list[str]
    same_stage: bool
    same_time: bool
    event_evidence: list[str]
    conflicts: list[str]
    is_reprint: bool
    original_publisher: str | None


def validate_evidence(verdict: Judgement, page: Page) -> list[str]:
    """Fail closed when accepted verdicts lack evidence or contain conflicting facts."""
    if verdict.decision not in {"event_reference", "project_reference"}:
        return []
    errors = []
    if page.problem or not page.text or not page.title:
        errors.append("page_not_readable")
    if verdict.language != "zh" or not verdict.same_project or verdict.conflicts:
        errors.append("identity_language_or_conflict")
    if verdict.source_type == "unknown" or not verdict.publisher.strip():
        errors.append("publisher_unconfirmed")
    visible_text = "\n".join([page.title, page.site_name, page.identity_text, page.text] + [item.evidence for item in page.source_statements])
    if not verdict.publisher_evidence.strip() or verdict.publisher_evidence not in visible_text:
        errors.append("publisher_evidence_not_located")
    # HTML inline elements can insert layout whitespace inside otherwise exact quotations.
    body = re.sub(r"\s+", "", page.text)
    if not verdict.project_evidence or any(not quote.strip() or re.sub(r"\s+", "", quote) not in body for quote in verdict.project_evidence):
        errors.append("project_evidence_not_located")
    if verdict.is_reprint and not (verdict.original_publisher or "").strip():
        errors.append("reprint_origin_unknown")
    elif verdict.is_reprint and verdict.original_publisher not in visible_text:
        errors.append("reprint_origin_not_located")
    if verdict.decision == "event_reference":
        if not verdict.same_stage or not verdict.same_time:
            errors.append("milestone_not_supported")
        if not verdict.event_evidence or any(not quote.strip() or re.sub(r"\s+", "", quote) not in body for quote in verdict.event_evidence):
            errors.append("event_evidence_not_located")
    return errors


def make_reference(context: EventContext, page: Page, verdict: Judgement, stage_en: str) -> dict | None:
    """Project only validated metadata into the prototype UI; never export cached text."""
    if verdict.decision not in {"event_reference", "project_reference"} or validate_evidence(verdict, page):
        return None
    event_scope = verdict.decision == "event_reference"
    checked = datetime.fromisoformat(page.checked_at).astimezone(timezone.utc)
    ref_key = f"{context.project_id}|{context.event_id}|{page.final_url}"
    return {
        "reference_id": hashlib.sha256(ref_key.encode()).hexdigest()[:20],
        "url": page.final_url,
        "title": page.title,
        "publisher": verdict.publisher,
        "source_type": verdict.source_type,
        "scope": "event" if event_scope else "project",
        "event_id": context.event_id if event_scope else None,
        "stage": context.event.get("project_stage") if event_scope else None,
        "stage_en": stage_en if event_scope else None,
        # Show the source event's known date granularity, not an invented award day.
        "event_date": (context.event.get("publish_date") or "")[:4] or None,
        "checked_at": checked.isoformat(timespec="milliseconds"),
        "valid_until": (checked + timedelta(days=90)).isoformat(timespec="milliseconds"),
        "is_reprint": verdict.is_reprint,
        "original_publisher": verdict.original_publisher,
    }
