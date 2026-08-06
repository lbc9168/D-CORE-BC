"""Build and hash the canonical article-only prompt for independent Studio review candidates."""

from __future__ import annotations

import hashlib

from gold_standard_studio.sources import ArticleSnapshot


REVIEW_PROMPT_VERSION = "gold_studio_review_prompt_v1"
REVIEW_PROMPT_TEMPLATE = """You are an independent article reviewer. Use only the article below; do not infer facts
from any database or another model. Return one JSON object with exactly the documented response keys.
For every present field, quote exact supporting article text and use character offsets from the article content.

Response schema:
{"projects": [{"gold_project_id": "string", "fields": {"field_name":
{"state": "present|not_mentioned|not_applicable|uncertain|needs_adjudication",
"value": "JSON value or null", "evidence": [{"quote": "exact article quote",
"start": 0, "end": 1, "article_sha256": "__ARTICLE_SHA256__", "source": "article"}]},
"entities": [{"role": "string", "name": "string"}]}]}

Title: __ARTICLE_TITLE__
Publication date: __ARTICLE_PUBLISH_DATE__
Article content:
__ARTICLE_CONTENT__
"""


def review_prompt_material(template: str | None = None) -> str:
    """Return the complete versioned static prompt material excluding article-specific substitutions."""
    return "\n\n".join((REVIEW_PROMPT_VERSION, template if template is not None else REVIEW_PROMPT_TEMPLATE))


def review_prompt_hash(template: str | None = None) -> str:
    """Hash the exact versioned canonical template while article content remains covered by the article key."""
    return hashlib.sha256(review_prompt_material(template).encode("utf-8")).hexdigest()


def build_review_prompt(article: ArticleSnapshot) -> str:
    """Render the canonical template with an immutable article and no other candidate material."""
    replacements = {
        "__ARTICLE_SHA256__": article.article_sha256,
        "__ARTICLE_TITLE__": article.title,
        "__ARTICLE_PUBLISH_DATE__": article.publish_date,
        "__ARTICLE_CONTENT__": article.content,
    }
    prompt = REVIEW_PROMPT_TEMPLATE
    for placeholder, value in replacements.items():
        prompt = prompt.replace(placeholder, value)
    return prompt
