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
    """Return identity-aligned structural and field conflicts while excluding errored source values."""
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
    reference_source = min(successful, key=_source_sort_key)
    reference_projects = successful[reference_source].projects
    alignments: dict[str, dict[int, int]] = {
        reference_source: {index: index for index in range(len(reference_projects))}
    }
    for source_name in sorted(successful, key=_source_sort_key):
        if source_name == reference_source:
            continue
        alignment, alignment_conflicts = _align_projects(
            reference_source,
            reference_projects,
            source_name,
            successful[source_name].projects,
            error_sources,
        )
        alignments[source_name] = alignment
        conflicts.extend(alignment_conflicts)
    for project_index in range(len(reference_projects)):
        for field_name in FIELD_REGISTRY:
            values = {
                source_name: _field_value(candidate.projects[alignments[source_name][project_index]], field_name)
                for source_name, candidate in successful.items()
                if project_index in alignments[source_name]
            }
            if len(values) < 2:
                continue
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
    """Return every exact quote occurrence, including overlaps, in deterministic text order."""
    return [match.start() for match in re.finditer(f"(?={re.escape(quote)})", article_text)]


def _align_projects(
    reference_source: str,
    reference_projects: list[GoldProject],
    source_name: str,
    projects: list[GoldProject],
    error_sources: tuple[str, ...],
) -> tuple[dict[int, int], list[FieldConflict]]:
    """Map one source's project positions to reference identities and report every unsafe mapping explicitly."""
    alignment: dict[int, int] = {}
    conflicts: list[FieldConflict] = []
    remaining = set(range(len(projects)))
    unmatched_references: list[tuple[int, GoldProject]] = []
    for reference_index, reference_project in enumerate(reference_projects):
        reference_name = _project_name(reference_project)
        matches = _candidate_project_matches(reference_name, projects, remaining)
        if len(matches) == 1:
            matched_index = matches[0]
            alignment[reference_index] = matched_index
            remaining.remove(matched_index)
            continue
        if not matches:
            unmatched_references.append((reference_index, reference_project))
        else:
            conflicts.append(
                _alignment_conflict(
                    reference_source,
                    reference_index,
                    reference_project,
                    source_name,
                    [projects[index] for index in matches],
                    "project_alignment_ambiguous",
                    error_sources,
                )
            )
    if unmatched_references:
        reference_index, _reference_project = unmatched_references[0]
        conflicts.append(
            FieldConflict(
                field_name="projects",
                project_index=reference_index,
                source_values={
                    reference_source: tuple(_project_identity(project) for _, project in unmatched_references),
                    source_name: tuple(_project_identity(projects[index]) for index in sorted(remaining)),
                },
                normalized_values={
                    reference_source: tuple(_project_identity(project) for _, project in unmatched_references),
                    source_name: tuple(_project_identity(projects[index]) for index in sorted(remaining)),
                },
                reason_code="project_alignment_unmatched",
                error_sources=error_sources,
            )
        )
    elif remaining and not conflicts:
        conflicts.append(
            _alignment_conflict(
                reference_source,
                None,
                None,
                source_name,
                [projects[index] for index in sorted(remaining)],
                "project_alignment_unmatched",
                error_sources,
            )
        )
    return alignment, conflicts


def _candidate_project_matches(
    reference_name: str | None, projects: list[GoldProject], remaining: set[int]
) -> list[int]:
    """Find exact normalized project-name matches first, then only unambiguous conservative fuzzy matches."""
    if reference_name is None:
        return []
    exact = [
        index
        for index in sorted(remaining)
        if (candidate_name := _project_name(projects[index])) is not None
        and _normalize_text(candidate_name) == _normalize_text(reference_name)
    ]
    if exact:
        return exact
    return [
        index
        for index in sorted(remaining)
        if (candidate_name := _project_name(projects[index])) is not None
        and _values_equivalent("project_name", reference_name, candidate_name)
    ]


def _alignment_conflict(
    reference_source: str,
    reference_index: int | None,
    reference_project: GoldProject | None,
    source_name: str,
    candidate_projects: list[GoldProject],
    reason_code: str,
    error_sources: tuple[str, ...],
) -> FieldConflict:
    """Describe an unsafe project identity mapping without emitting misleading positional field conflicts."""
    source_values: dict[str, Any] = {
        source_name: tuple(_project_identity(project) for project in candidate_projects)
    }
    if reference_project is not None:
        source_values[reference_source] = _project_identity(reference_project)
    return FieldConflict(
        field_name="projects",
        project_index=reference_index,
        source_values=source_values,
        normalized_values=source_values,
        reason_code=reason_code,
        error_sources=error_sources,
    )


def _project_name(project: GoldProject) -> str | None:
    """Return a nonblank present project name used as the only cross-source identity evidence."""
    name = _field_value(project, "project_name")
    return str(name) if isinstance(name, str) and name.strip() else None


def _project_identity(project: GoldProject) -> str:
    """Return a readable local identifier for structural alignment diagnostics without treating it as shared identity."""
    return _project_name(project) or f"<unnamed:{project.gold_project_id}>"


def _source_sort_key(source_name: str) -> tuple[int, str]:
    """Order known candidate sources predictably so reference identity selection never depends on mapping insertion."""
    priority = ("database", "production", "review", "deterministic")
    return (priority.index(source_name) if source_name in priority else len(priority), source_name)


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
