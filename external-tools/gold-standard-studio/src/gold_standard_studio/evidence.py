"""Locate immutable article evidence and explain field disagreements between candidate sources."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from difflib import SequenceMatcher
from typing import Any, Mapping
import unicodedata

from gold_standard_studio.field_registry import FIELD_REGISTRY
from gold_standard_studio.schemas import CandidateBundle, EvidenceSpan, GoldField, GoldProject
from gold_standard_studio.sources import ArticleSnapshot, article_sha256


_MAX_MATCHING_COMPONENT_SIDE = 8


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


@dataclass(frozen=True)
class ProjectFieldCandidate:
    """Represent one source field safely aligned to a human gold-project identity."""

    source_name: str
    source_project_id: str | None
    source_project_index: int | None
    field: GoldField | None
    source_status: str
    evidence_warning: bool = False


def relocate_quote(
    article: ArticleSnapshot, quote: str, *, expected_start: int | None
) -> EvidenceSpan:
    """Safely relocate a quote against one canonical title-content-date ArticleSnapshot identity."""
    _validate_article_snapshot(article)
    if not quote:
        raise EvidenceNotFoundError("evidence quote must not be empty")
    matches = _quote_offsets(article.content, quote)
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
    return EvidenceSpan(
        quote=quote,
        start=start,
        end=start + len(quote),
        article_sha256=article.article_sha256,
    )


def locate_evidence(article: ArticleSnapshot, evidence: EvidenceSpan) -> EvidenceSpan:
    """Return exact evidence or relocate it within the current canonical ArticleSnapshot."""
    _validate_article_snapshot(article)
    if (
        evidence.article_sha256 == article.article_sha256
        and evidence.end <= len(article.content)
        and article.content[evidence.start : evidence.end] == evidence.quote
    ):
        return evidence
    return relocate_quote(
        article,
        evidence.quote,
        expected_start=evidence.start,
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


def project_field_candidates(
    projects: list[GoldProject], bundle: CandidateBundle | None, article_sha256: str
) -> dict[str, list[ProjectFieldCandidate]]:
    """Project every source field onto gold project IDs using identity matching, never list position."""
    if bundle is None:
        return {}
    result: dict[str, list[ProjectFieldCandidate]] = {
        f"{project.gold_project_id}.{field_name}": []
        for project in projects
        for field_name in project.fields
    }
    successful = {name: value for name, value in bundle.sources.items() if value.status == "ok"}
    error_sources = tuple(sorted(name for name, value in bundle.sources.items() if value.status == "error"))
    alignments: dict[str, dict[int, int]] = {}
    for source_name, candidate in successful.items():
        alignment, _conflicts = _align_projects("gold", projects, source_name, candidate.projects, error_sources)
        alignments[source_name] = alignment
    for project_index, project in enumerate(projects):
        for field_name in project.fields:
            path = f"{project.gold_project_id}.{field_name}"
            for source_name in sorted(bundle.sources, key=_source_sort_key):
                candidate = bundle.sources[source_name]
                candidate_index = alignments.get(source_name, {}).get(project_index)
                candidate_project = (
                    candidate.projects[candidate_index]
                    if candidate_index is not None and candidate.status == "ok"
                    else None
                )
                field, evidence_warning = _projection_field_with_current_evidence(
                    _field_for_projection(candidate_project, field_name), article_sha256
                )
                result[path].append(
                    ProjectFieldCandidate(
                        source_name=source_name,
                        source_project_id=candidate_project.gold_project_id if candidate_project else None,
                        source_project_index=candidate_index,
                        field=field,
                        source_status=candidate.status,
                        evidence_warning=evidence_warning,
                    )
                )
    return result


def structural_conflicts(bundle: CandidateBundle | None) -> list[FieldConflict]:
    """Return every project-count or unsafe-alignment conflict for the persisted structure gate."""
    if bundle is None:
        return []
    return [conflict for conflict in compare_candidates(bundle) if conflict.field_name == "projects"]


def aligned_field_conflicts(projects: list[GoldProject], bundle: CandidateBundle | None) -> list[FieldConflict]:
    """Report only disagreements whose source values have first been aligned to the same gold project."""
    if bundle is None:
        return []
    projection = project_field_candidates(projects, bundle, bundle.article_sha256)
    conflicts: list[FieldConflict] = []
    for project_index, project in enumerate(projects):
        for field_name in project.fields:
            candidates = [item for item in projection[f"{project.gold_project_id}.{field_name}"] if item.field is not None]
            states = {item.source_name: item.field.state for item in candidates}
            values = {item.source_name: item.field.value for item in candidates if item.field.state == "present"}
            state_disagreement = len(set(states.values())) > 1
            value_disagreement = len(values) >= 2 and not _all_equivalent(field_name, list(values.values()))
            if not state_disagreement and not value_disagreement:
                continue
            conflicts.append(
                FieldConflict(
                    field_name=field_name,
                    project_index=project_index,
                    source_values=values,
                    normalized_values={name: _normalize_value(field_name, value) for name, value in values.items()},
                    reason_code="field_state_disagreement" if state_disagreement else "field_disagreement",
                    error_sources=tuple(
                        sorted(item.source_name for item in projection[f"{project.gold_project_id}.{field_name}"] if item.source_status == "error")
                    ),
                )
            )
    return conflicts


def _validate_article_snapshot(article: ArticleSnapshot) -> None:
    """Reject snapshots whose declared digest does not match their canonical title-content-date identity."""
    expected = article_sha256(article.title, article.content, article.publish_date)
    if article.article_sha256 != expected:
        raise EvidenceError("article_sha256 does not match the supplied canonical article snapshot")


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
    """Map one source globally to reference identities and report every unsafe one-to-one match explicitly."""
    alignment: dict[int, int] = {}
    conflicts: list[FieldConflict] = []
    remaining_references = set(range(len(reference_projects)))
    remaining_candidates = set(range(len(projects)))
    exact_pairs = _unique_exact_pairs(reference_projects, projects, remaining_references, remaining_candidates)
    alignment.update(exact_pairs)
    remaining_references.difference_update(exact_pairs)
    remaining_candidates.difference_update(exact_pairs.values())
    fuzzy_edges = _fuzzy_edges(reference_projects, projects, remaining_references, remaining_candidates)
    unsafe_references: set[int] = set()
    unsafe_candidates: set[int] = set()
    for component_references, component_candidates in _fuzzy_components(fuzzy_edges, reference_projects, projects):
        assignments = _maximum_assignments(component_references, component_candidates, fuzzy_edges, reference_projects, projects)
        if assignments is None:
            unsafe_references.update(component_references)
            unsafe_candidates.update(component_candidates)
            conflicts.append(
                _alignment_group_conflict(
                    reference_source,
                    component_references,
                    reference_projects,
                    source_name,
                    component_candidates,
                    projects,
                    "project_alignment_complex",
                    error_sources,
                )
            )
        elif len(assignments) != 1:
            unsafe_references.update(component_references)
            unsafe_candidates.update(component_candidates)
            conflicts.append(
                _alignment_group_conflict(
                    reference_source,
                    component_references,
                    reference_projects,
                    source_name,
                    component_candidates,
                    projects,
                    "project_alignment_ambiguous",
                    error_sources,
                )
            )
        else:
            alignment.update(assignments[0])
    unmatched_references = remaining_references.difference(alignment).difference(unsafe_references)
    unmatched_candidates = remaining_candidates.difference(alignment.values()).difference(unsafe_candidates)
    if unmatched_references or unmatched_candidates:
        conflicts.append(
            _alignment_group_conflict(
                reference_source,
                unmatched_references,
                reference_projects,
                source_name,
                unmatched_candidates,
                projects,
                "project_alignment_unmatched",
                error_sources,
            )
        )
    return alignment, conflicts


def _unique_exact_pairs(
    reference_projects: list[GoldProject],
    projects: list[GoldProject],
    reference_indices: set[int],
    candidate_indices: set[int],
) -> dict[int, int]:
    """Reserve only normalized exact names that occur once on each side before fuzzy graph construction."""
    references_by_name = _indices_by_normalized_name(reference_projects, reference_indices)
    candidates_by_name = _indices_by_normalized_name(projects, candidate_indices)
    return {
        references_by_name[name][0]: candidates_by_name[name][0]
        for name in sorted(set(references_by_name).intersection(candidates_by_name))
        if len(references_by_name[name]) == len(candidates_by_name[name]) == 1
    }


def _indices_by_normalized_name(projects: list[GoldProject], indices: set[int]) -> dict[str, list[int]]:
    """Group named projects by their normalized identity while retaining a deterministic local index order."""
    grouped: dict[str, list[int]] = {}
    for index in sorted(indices, key=lambda value: _project_index_sort_key(projects, value)):
        name = _project_name(projects[index])
        if name is not None:
            grouped.setdefault(_normalize_text(name), []).append(index)
    return grouped


def _fuzzy_edges(
    reference_projects: list[GoldProject],
    projects: list[GoldProject],
    reference_indices: set[int],
    candidate_indices: set[int],
) -> dict[int, tuple[int, ...]]:
    """Build every conservative fuzzy-name edge left after exact reservations without choosing a traversal order."""
    edges: dict[int, tuple[int, ...]] = {}
    ordered_candidates = sorted(candidate_indices, key=lambda value: _project_index_sort_key(projects, value))
    for reference_index in sorted(reference_indices, key=lambda value: _project_index_sort_key(reference_projects, value)):
        reference_name = _project_name(reference_projects[reference_index])
        if reference_name is None:
            continue
        matches = tuple(
            candidate_index
            for candidate_index in ordered_candidates
            if (candidate_name := _project_name(projects[candidate_index])) is not None
            and _values_equivalent("project_name", reference_name, candidate_name)
        )
        if matches:
            edges[reference_index] = matches
    return edges


def _fuzzy_components(
    edges: Mapping[int, tuple[int, ...]], reference_projects: list[GoldProject], projects: list[GoldProject]
) -> list[tuple[set[int], set[int]]]:
    """Split the fuzzy bipartite graph into independent components so matching search stays bounded."""
    candidate_edges: dict[int, set[int]] = {}
    for reference_index, candidate_indices in edges.items():
        for candidate_index in candidate_indices:
            candidate_edges.setdefault(candidate_index, set()).add(reference_index)
    components: list[tuple[set[int], set[int]]] = []
    visited_references: set[int] = set()
    for start in sorted(edges, key=lambda value: _project_index_sort_key(reference_projects, value)):
        if start in visited_references:
            continue
        references: set[int] = set()
        candidates: set[int] = set()
        pending_references = [start]
        while pending_references:
            reference_index = pending_references.pop()
            if reference_index in visited_references:
                continue
            visited_references.add(reference_index)
            references.add(reference_index)
            for candidate_index in edges[reference_index]:
                if candidate_index not in candidates:
                    candidates.add(candidate_index)
                    pending_references.extend(candidate_edges[candidate_index].difference(visited_references))
        components.append((references, candidates))
    return components


def _maximum_assignments(
    reference_indices: set[int],
    candidate_indices: set[int],
    edges: Mapping[int, tuple[int, ...]],
    reference_projects: list[GoldProject],
    projects: list[GoldProject],
) -> list[dict[int, int]] | None:
    """Enumerate up to two maximum one-to-one assignments or safely defer oversized components to review."""
    if len(reference_indices) > _MAX_MATCHING_COMPONENT_SIDE or len(candidate_indices) > _MAX_MATCHING_COMPONENT_SIDE:
        return None
    ordered_references = sorted(reference_indices, key=lambda value: _project_index_sort_key(reference_projects, value))
    ordered_options = {
        reference_index: tuple(
            candidate_index
            for candidate_index in edges[reference_index]
            if candidate_index in candidate_indices
        )
        for reference_index in ordered_references
    }
    best_size = -1
    best_assignments: list[dict[int, int]] = []

    def visit(position: int, assignment: dict[int, int], used_candidates: set[int]) -> None:
        """Search one bounded component with branch-and-bound while retaining only assignment uniqueness evidence."""
        nonlocal best_size, best_assignments
        if len(assignment) + len(ordered_references) - position < best_size:
            return
        if position == len(ordered_references):
            size = len(assignment)
            if size > best_size:
                best_size = size
                best_assignments = [dict(assignment)]
            elif size == best_size and len(best_assignments) < 2:
                best_assignments.append(dict(assignment))
            return
        reference_index = ordered_references[position]
        for candidate_index in ordered_options[reference_index]:
            if candidate_index not in used_candidates:
                assignment[reference_index] = candidate_index
                visit(position + 1, assignment, used_candidates | {candidate_index})
                del assignment[reference_index]
        visit(position + 1, assignment, used_candidates)

    visit(0, {}, set())
    return best_assignments


def _alignment_group_conflict(
    reference_source: str,
    reference_indices: set[int],
    reference_projects: list[GoldProject],
    source_name: str,
    candidate_indices: set[int],
    projects: list[GoldProject],
    reason_code: str,
    error_sources: tuple[str, ...],
) -> FieldConflict:
    """Describe one unsafe alignment graph component without emitting misleading positional field conflicts."""
    reference_values = tuple(
        _project_identity(reference_projects[index])
        for index in sorted(reference_indices, key=lambda value: _project_index_sort_key(reference_projects, value))
    )
    candidate_values = tuple(
        _project_identity(projects[index])
        for index in sorted(candidate_indices, key=lambda value: _project_index_sort_key(projects, value))
    )
    source_values: dict[str, Any] = {
        reference_source: reference_values,
        source_name: candidate_values,
    }
    return FieldConflict(
        field_name="projects",
        project_index=min(reference_indices) if reference_indices else None,
        source_values=source_values,
        normalized_values=source_values,
        reason_code=reason_code,
        error_sources=error_sources,
    )


def _project_index_sort_key(projects: list[GoldProject], index: int) -> tuple[str, int]:
    """Provide source-order-independent project traversal by normalized name with local index only as a final tie-break."""
    return (_normalize_text(_project_name(projects[index]) or ""), index)


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
    """Read one canonical present field from a candidate project."""
    field = project.fields.get(field_name)
    return field.value if field is not None and field.state == "present" else None


def _field_for_projection(project: GoldProject | None, field_name: str) -> GoldField | None:
    """Return one canonical source field object while preserving its evidence."""
    if project is None:
        return None
    return project.fields.get(field_name)


def _projection_field_with_current_evidence(field: GoldField | None, article_sha256: str) -> tuple[GoldField | None, bool]:
    """Drop stale evidence spans from a presentation field and flag their source without mutating cached candidates."""
    if field is None:
        return None, False
    valid_evidence = [evidence for evidence in field.evidence if evidence.article_sha256 == article_sha256]
    warning = len(valid_evidence) != len(field.evidence)
    return field.model_copy(update={"evidence": valid_evidence}), warning


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
