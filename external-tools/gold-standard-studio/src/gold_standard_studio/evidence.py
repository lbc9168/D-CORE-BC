"""Locate immutable article evidence and explain field disagreements between candidate sources."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re
from difflib import SequenceMatcher
from typing import Any, Mapping
import unicodedata

from gold_standard_studio.field_registry import FIELD_REGISTRY
from gold_standard_studio.schemas import CandidateBundle, EvidenceSpan, GoldProject


class EvidenceError(ValueError):
    """Base error for evidence that cannot be safely tied to an immutable article snapshot."""


class EvidenceNotFoundError(EvidenceError):
    """Raise when a quoted evidence string is absent from the supplied article text."""


class AmbiguousEvidenceError(EvidenceError):
    """Raise when multiple equally plausible quote locations would require human adjudication."""


@dataclass(frozen=True)
class FieldConflict:
    """Describe one normalized candidate disagreement with raw values and unavailable-source context."""

    field_name: str
    project_index: int | None
    source_values: Mapping[str, Any]
    normalized_values: Mapping[str, Any]
    reason_code: str
    error_sources: tuple[str, ...] = ()


def article_sha256(text: str) -> str:
    """Return the UTF-8 SHA-256 digest that identifies one immutable article text snapshot."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def relocate_quote(
    article_text: str, quote: str, *, expected_start: int | None, article_sha256: str
) -> EvidenceSpan:
    """Safely relocate a quote by accepting only one exact or uniquely nearest occurrence."""
    _validate_article_hash(article_text, article_sha256)
    if not quote:
        raise EvidenceNotFoundError("evidence quote must not be empty")
    matches = _quote_offsets(article_text, quote)
    if not matches:
        raise EvidenceNotFoundError("evidence quote was not found in article text")
    if expected_start in matches:
        start = expected_start
    elif len(matches) == 1:
        start = matches[0]
    elif expected_start is None:
        raise AmbiguousEvidenceError("evidence quote has multiple locations and no expected offset")
    else:
        distances = [(abs(offset - expected_start), offset) for offset in matches]
        minimum = min(distance for distance, _offset in distances)
        nearest = [offset for distance, offset in distances if distance == minimum]
        if len(nearest) != 1:
            raise AmbiguousEvidenceError("evidence quote has multiple equally near locations")
        start = nearest[0]
    return EvidenceSpan(quote=quote, start=start, end=start + len(quote), article_sha256=article_sha256)


def locate_evidence(article_text: str, evidence: EvidenceSpan) -> EvidenceSpan:
    """Return exact evidence when valid or a uniquely relocated span carrying the current article hash."""
    current_hash = article_sha256(article_text)
    if (
        evidence.article_sha256 == current_hash
        and evidence.end <= len(article_text)
        and article_text[evidence.start : evidence.end] == evidence.quote
    ):
        return evidence
    return relocate_quote(
        article_text,
        evidence.quote,
        expected_start=evidence.start,
        article_sha256=current_hash,
    )


def compare_candidates(bundle: CandidateBundle) -> list[FieldConflict]:
    """Return stable structural and reviewed-field conflicts while excluding errored source values."""
    successful = {name: value for name, value in bundle.sources.items() if value.status == "ok"}
    error_sources = tuple(sorted(name for name, value in bundle.sources.items() if value.status == "error"))
    if not successful:
        return []
    conflicts: list[FieldConflict] = []
    counts = {name: len(value.projects) for name, value in successful.items()}
    if len(set(counts.values())) > 1:
        conflicts.append(
            FieldConflict(
                field_name="projects",
                project_index=None,
                source_values=counts,
                normalized_values=counts,
                reason_code="project_count_disagreement",
                error_sources=error_sources,
            )
        )
    max_projects = max(counts.values(), default=0)
    for project_index in range(max_projects):
        for field_name in FIELD_REGISTRY:
            values = {
                source_name: _field_value(candidate.projects[project_index], field_name)
                if project_index < len(candidate.projects)
                else None
                for source_name, candidate in successful.items()
            }
            if not any(value is not None for value in values.values()):
                continue
            normalized = {source_name: _normalize_value(field_name, value) for source_name, value in values.items()}
            if _all_equivalent(field_name, list(values.values())):
                continue
            conflicts.append(
                FieldConflict(
                    field_name=field_name,
                    project_index=project_index,
                    source_values=values,
                    normalized_values=normalized,
                    reason_code="field_disagreement",
                    error_sources=error_sources,
                )
            )
    return conflicts


def _validate_article_hash(article_text: str, expected_hash: str) -> None:
    """Reject caller-supplied hashes that do not describe the article whose offsets are being created."""
    if article_sha256(article_text) != expected_hash:
        raise EvidenceError("article_sha256 does not match the supplied article text")


def _quote_offsets(article_text: str, quote: str) -> list[int]:
    """Return every non-overlapping exact quote occurrence in deterministic text order."""
    return [match.start() for match in re.finditer(re.escape(quote), article_text)]


def _field_value(project: GoldProject, field_name: str) -> Any:
    """Read one present field while recognizing the deterministic candidate's proposed-category alias."""
    field = project.fields.get(field_name)
    if field is None and field_name == "category":
        field = project.fields.get("proposed_category")
    return field.value if field is not None and field.state == "present" else None


def _all_equivalent(field_name: str, values: list[Any]) -> bool:
    """Compare every candidate value using the field registry's explicit type-specific policy."""
    first = values[0]
    return all(_values_equivalent(field_name, first, value) for value in values[1:])


def _values_equivalent(field_name: str, left: Any, right: Any) -> bool:
    """Apply exact, tolerance, set, or normalized-name equality without treating zero as missing."""
    if left is None or right is None:
        return left is right
    spec = FIELD_REGISTRY[field_name]
    if spec.field_type in {"enum", "date"}:
        return left == right
    if spec.field_type == "number":
        return _numbers_equivalent(left, right)
    if spec.field_type == "set":
        return _normalize_set(left) == _normalize_set(right)
    if field_name in {"project_name", "project_developer"}:
        return _name_similarity(str(left), str(right)) >= 0.88
    return _normalize_text(str(left)) == _normalize_text(str(right))


def _numbers_equivalent(left: Any, right: Any) -> bool:
    """Compare finite numeric values within five percent while requiring zero to match exactly."""
    try:
        left_number, right_number = float(left), float(right)
    except (TypeError, ValueError):
        return left == right
    if not math.isfinite(left_number) or not math.isfinite(right_number):
        return False
    if left_number == 0 or right_number == 0:
        return left_number == right_number
    return abs(left_number - right_number) / max(abs(left_number), abs(right_number)) <= 0.05


def _normalize_value(field_name: str, value: Any) -> Any:
    """Render one comparison value in the same stable form used to explain its equality policy."""
    if value is None:
        return None
    spec = FIELD_REGISTRY[field_name]
    if spec.field_type == "set":
        return tuple(sorted(_normalize_set(value)))
    if spec.field_type == "number":
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    if field_name in {"project_name", "project_developer"} or spec.field_type == "text":
        return _normalize_text(str(value))
    return value


def _normalize_set(value: Any) -> frozenset[str]:
    """Normalize list-like category values into order-insensitive, duplicate-free display sets."""
    values = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
    return frozenset(_normalize_text(str(item)) for item in values)


def _normalize_text(value: str) -> str:
    """Normalize Unicode case, punctuation, and whitespace before human-name or set comparison."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = normalized.replace("company", "co").replace("limited", "ltd")
    return "".join(character for character in normalized if character.isalnum())


def _name_similarity(left: str, right: str) -> float:
    """Return a deterministic normalized-character similarity for names with minor spelling variation."""
    return SequenceMatcher(None, _normalize_text(left), _normalize_text(right)).ratio()
