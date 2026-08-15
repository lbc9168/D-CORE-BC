"""Combine the immutable Pilot and Stage 4E selections for the local review queue."""

from __future__ import annotations

from typing import Any

from .pilot_manifest import PILOT_BY_ID, pilot_article_ids
from .stage4e_manifest import STAGE4E_BY_ID, stage4e_article_ids


def _pilot_item(article_id: int) -> dict[str, Any]:
    """Add explicit cohort and split metadata to one legacy Pilot item."""
    return {
        **PILOT_BY_ID[article_id],
        "cohort": "pilot-v0.1.1",
        "split": "development",
        "primary_stratum": "pilot",
        "layer_tags": ["s01b", "s02b", "end_to_end_candidate"],
        "family_id": None,
    }


REVIEW_ARTICLES: tuple[dict[str, Any], ...] = tuple(
    [_pilot_item(article_id) for article_id in pilot_article_ids()]
    + [STAGE4E_BY_ID[article_id] for article_id in stage4e_article_ids()]
)
REVIEW_BY_ID = {item["article_id"]: item for item in REVIEW_ARTICLES}


def review_article_ids() -> list[int]:
    """Return all reviewable IDs with the ten Pilot articles first and Stage 4E following."""
    return [item["article_id"] for item in REVIEW_ARTICLES]


def review_navigation(article_id: int) -> dict[str, Any]:
    """Return deterministic previous and next links across the complete local review queue."""
    ids = review_article_ids()
    index = ids.index(article_id)
    return {
        "position": index + 1,
        "total": len(ids),
        "previous": ids[index - 1] if index else None,
        "next": ids[index + 1] if index + 1 < len(ids) else None,
    }
