"""Build an article-only prompt for independent Studio review candidates."""

from __future__ import annotations

import hashlib

from gold_standard_studio.sources import ArticleSnapshot


REVIEW_PROMPT_VERSION = "gold_studio_review_prompt_v1"
REVIEW_INSTRUCTIONS = """You are an independent article reviewer. Use only the article below; do not infer facts
from any database or another model. Return one JSON object with exactly the documented response keys.
For every present field, quote exact supporting article text and use character offsets from the article content."""
REVIEW_RESPONSE_SCHEMA = """{"projects": [{"gold_project_id": "string", "fields": {"field_name":
{"state": "present|not_mentioned|not_applicable|uncertain|needs_adjudication",
"value": "JSON value or null", "evidence": [{"quote": "exact article quote",
"start": 0, "end": 1, "article_sha256": "__ARTICLE_SHA256__", "source": "article"}]},
"entities": [{"role": "string", "name": "string"}]}]}"""


def review_prompt_material() -> str:
    """Return all reusable prompt instructions and schema material excluding article-specific content."""
    return "\n\n".join((REVIEW_PROMPT_VERSION, REVIEW_INSTRUCTIONS, REVIEW_RESPONSE_SCHEMA))


def review_prompt_hash(material: str | None = None) -> str:
    """Hash the full static prompt behavior material while leaving per-article content to the article key."""
    return hashlib.sha256((material if material is not None else review_prompt_material()).encode("utf-8")).hexdigest()


def build_review_prompt(article: ArticleSnapshot) -> str:
    """Request evidence-backed review JSON using only an immutable article snapshot."""
    response_schema = REVIEW_RESPONSE_SCHEMA.replace("__ARTICLE_SHA256__", article.article_sha256)
    return f"""{REVIEW_INSTRUCTIONS}

Response schema:
{response_schema}

Title: {article.title}
Publication date: {article.publish_date}
Article content:
{article.content}
"""
