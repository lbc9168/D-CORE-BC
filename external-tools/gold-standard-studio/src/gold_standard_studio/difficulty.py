"""Score candidate-review difficulty with transparent reasons and deterministic audit selection."""

from __future__ import annotations

import hashlib
import math
from typing import Iterable

from gold_standard_studio.evidence import compare_candidates
from gold_standard_studio.field_registry import FIELD_REGISTRY
from gold_standard_studio.schemas import CandidateBundle, CandidateValue, DifficultyScore


REASON_WEIGHTS = {
    "project_count_disagreement": 40,
    "category_disagreement": 20,
    "missing_required_evidence": 15,
    "multiple_amounts": 15,
    "multiple_entities": 10,
    "single_source_support": 10,
}

_AMOUNT_FIELDS = {name for name, spec in FIELD_REGISTRY.items() if spec.field_type == "number"}
_RISK_TAG_ORDER = (
    "source_failure",
    "multiple_projects",
    "category_conflict",
    "storage_context",
    "missing_evidence",
    "multiple_amounts",
    "multiple_entities",
    "single_source_support",
)


def score_difficulty(bundle: CandidateBundle) -> DifficultyScore:
    """Calculate a stable additive score and risk tags from visible candidate and evidence conditions."""
    successful = [candidate for candidate in bundle.sources.values() if candidate.status == "ok"]
    reasons: set[str] = set()
    tags: set[str] = set()
    conflicts = compare_candidates(bundle)
    if any(conflict.reason_code == "project_count_disagreement" for conflict in conflicts):
        reasons.add("project_count_disagreement")
        tags.add("multiple_projects")
    if any(conflict.field_name == "category" for conflict in conflicts):
        reasons.add("category_disagreement")
        tags.add("category_conflict")
    if _missing_required_evidence(successful):
        reasons.add("missing_required_evidence")
        tags.add("missing_evidence")
    if _has_multiple_amounts(successful):
        reasons.add("multiple_amounts")
        tags.add("multiple_amounts")
    if any(len(project.entities) > 1 for candidate in successful for project in candidate.projects):
        reasons.add("multiple_entities")
        tags.add("multiple_entities")
    if len(successful) <= 1:
        reasons.add("single_source_support")
        tags.add("single_source_support")
    if any(candidate.status == "error" for candidate in bundle.sources.values()):
        tags.add("source_failure")
    if _has_storage_context(successful):
        tags.add("storage_context")
    ordered_reasons = [reason for reason in REASON_WEIGHTS if reason in reasons]
    return DifficultyScore(
        score=float(sum(REASON_WEIGHTS[reason] for reason in ordered_reasons)),
        reason_codes=ordered_reasons,
        risk_tags=_ordered_risk_tags(tags),
    )


def select_for_audit(sample_id: str, *, seed: int, rate: float = 0.10) -> bool:
    """Choose an audit item from a reproducible SHA-256 draw at a bounded per-risk rate."""
    if not sample_id:
        raise ValueError("sample_id must not be blank")
    if not isinstance(rate, (int, float)) or not math.isfinite(rate) or not 0 <= rate <= 1:
        raise ValueError("audit rate must be a finite value between 0 and 1")
    digest = hashlib.sha256(f"{seed}:{sample_id}".encode("utf-8")).digest()
    draw = int.from_bytes(digest[:8], "big") / 2**64
    return draw < rate


def risk_audit_rates(store: object, risk_tags: Iterable[str], *, default_rate: float = 0.10) -> dict[str, float]:
    """Read stable per-tag rates from a ReviewStore-compatible metadata boundary for audit callers."""
    getter = getattr(store, "get_risk_audit_rate")
    return {tag: getter(tag, default_rate=default_rate) for tag in sorted(set(risk_tags))}


def _missing_required_evidence(candidates: list[CandidateValue]) -> bool:
    """Return whether any present registered critical field lacks candidate evidence for human validation."""
    for candidate in candidates:
        for project in candidate.projects:
            for field_name, field in project.fields.items():
                spec = FIELD_REGISTRY.get(field_name)
                if spec is not None and spec.evidence_required and field.state == "present" and not field.evidence:
                    return True
    return False


def _has_multiple_amounts(candidates: list[CandidateValue]) -> bool:
    """Return whether candidate sources expose more than one finite value for the same numeric concept."""
    values_by_field: dict[str, set[float]] = {}
    for candidate in candidates:
        for project in candidate.projects:
            for field_name, field in project.fields.items():
                if field_name not in _AMOUNT_FIELDS or field.state != "present":
                    continue
                try:
                    value = float(field.value)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value):
                    values_by_field.setdefault(field_name, set()).add(value)
    return any(len(values) > 1 for values in values_by_field.values())


def _has_storage_context(candidates: list[CandidateValue]) -> bool:
    """Return whether any candidate carries storage fields that require storage-specific review context."""
    storage_fields = {"storage_status", "storage_power_mw", "storage_energy_mwh"}
    return any(
        field_name in storage_fields and field.state == "present"
        for candidate in candidates
        for project in candidate.projects
        for field_name, field in project.fields.items()
    )


def _ordered_risk_tags(tags: set[str]) -> list[str]:
    """Order known tags by reviewer urgency and unknown future tags lexically for reproducible output."""
    ordered = [tag for tag in _RISK_TAG_ORDER if tag in tags]
    return ordered + sorted(tags.difference(_RISK_TAG_ORDER))
