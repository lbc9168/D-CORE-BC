"""Build an article-only prompt for independent Studio review candidates."""

from __future__ import annotations

import hashlib

from gold_standard_studio.sources import ArticleSnapshot


REVIEW_PROMPT_VERSION = "gold_studio_review_prompt_v1"


def review_prompt_hash() -> str:
    """Return a stable digest for the independent review prompt instructions."""
    return hashlib.sha256(REVIEW_PROMPT_VERSION.encode("utf-8")).hexdigest()


def build_review_prompt(article: ArticleSnapshot) -> str:
    """Request evidence-backed review JSON using only an immutable article snapshot."""
    return f"""You are an independent article reviewer. Use only the article below; do not infer facts
from any database or another model. Return one JSON object with exactly these keys:
{{"projects": [{{"gold_project_id": "string", "fields": {{"field_name":
{{"state": "present|not_mentioned|not_applicable|uncertain|needs_adjudication",
"value": "JSON value or null", "evidence": [{{"quote": "exact article quote",
"start": 0, "end": 1, "article_sha256": "{article.article_sha256}", "source": "article"}}]}},
"entities": [{{"role": "string", "name": "string"}}]}}]}}.
For every present field, quote exact supporting article text and use character offsets from the article content.

Title: {article.title}
Publication date: {article.publish_date}
Article content:
{article.content}
"""
