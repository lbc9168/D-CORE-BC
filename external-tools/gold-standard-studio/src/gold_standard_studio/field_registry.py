"""Define and validate the single reviewed-field registry used by Studio drafts and publications."""

from dataclasses import dataclass
from datetime import date
import json
import math
from pathlib import Path
import re
from typing import Literal


FieldType = Literal["text", "enum", "set", "number", "date"]


@dataclass(frozen=True)
class FieldSpec:
    """Describe one reviewed field's value type, evidence requirement, and evaluation mapping."""

    field_type: FieldType
    evidence_required: bool
    eval_metric: str | None
    allowed_values: frozenset[str] | None = None


def _repository_enums() -> dict[str, frozenset[str]]:
    """Load controlled production enums from the repository's canonical mapping without mutating import paths."""
    path = Path(__file__).resolve().parents[4] / "enums_mapping.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        name: frozenset(section.keys())
        for name, section in payload.items()
        if isinstance(section, dict)
    }


_ENUMS = _repository_enums()
_GENERATION_SOURCES = frozenset({"光伏", "风电", "水电", "地热及余热", "生物质", "核电", "火电"})
_STORAGE_STATUSES = frozenset({"confirmed", "planned", "optional", "unclear", "not_mentioned"})
_DATE_PATTERN = re.compile(r"\d{4}(?:-\d{2}(?:-\d{2})?)?\Z")


FIELD_REGISTRY = {
    "project_name": FieldSpec("text", evidence_required=True, eval_metric="name_similarity"),
    "country": FieldSpec("text", evidence_required=False, eval_metric="exact"),
    "location": FieldSpec("text", evidence_required=False, eval_metric="normalized_exact"),
    "category": FieldSpec("enum", evidence_required=True, eval_metric="exact", allowed_values=_ENUMS["category"]),
    "generation_sources": FieldSpec(
        "set", evidence_required=True, eval_metric="set_f1", allowed_values=_GENERATION_SOURCES
    ),
    "storage_status": FieldSpec(
        "enum", evidence_required=True, eval_metric="exact", allowed_values=_STORAGE_STATUSES
    ),
    "capacity_mw": FieldSpec("number", evidence_required=True, eval_metric="numeric_5pct"),
    "storage_power_mw": FieldSpec("number", evidence_required=True, eval_metric="numeric_5pct"),
    "storage_energy_mwh": FieldSpec("number", evidence_required=True, eval_metric="numeric_5pct"),
    "project_stage": FieldSpec(
        "enum", evidence_required=True, eval_metric="exact", allowed_values=_ENUMS["project_stage"]
    ),
    "project_developer": FieldSpec("text", evidence_required=True, eval_metric="normalized_name"),
    "planned_start_date": FieldSpec("date", evidence_required=True, eval_metric="date_exact"),
    "planned_complete_date": FieldSpec("date", evidence_required=True, eval_metric="date_exact"),
    "investment_date": FieldSpec("date", evidence_required=True, eval_metric="date_exact"),
    "total_investment_usd": FieldSpec("number", evidence_required=True, eval_metric="numeric_5pct"),
    "contract_amount_usd": FieldSpec("number", evidence_required=True, eval_metric="numeric_5pct"),
    "financing_amount_usd": FieldSpec("number", evidence_required=True, eval_metric="numeric_5pct"),
    "financing_model": FieldSpec(
        "enum", evidence_required=True, eval_metric="exact", allowed_values=_ENUMS["financing_model"]
    ),
    "project_summary": FieldSpec("text", evidence_required=False, eval_metric=None),
}


ENTITY_ROLES = _ENUMS["involvement_type"]


def validate_field_value(field_name: str, state: str, value: object) -> None:
    """Reject unknown fields and present values that do not exactly match the registry type and control set."""
    specification = FIELD_REGISTRY.get(field_name)
    if specification is None:
        raise ValueError(f"unknown registry field: {field_name}")
    if state != "present":
        return
    if specification.field_type in {"text", "enum", "date"}:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} requires a nonblank string")
    if specification.field_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{field_name} requires a finite non-negative number")
    elif specification.field_type == "set":
        if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
            raise ValueError(f"{field_name} requires a nonempty list of strings")
        if len(value) != len(set(value)):
            raise ValueError(f"{field_name} requires unique set values")
        if specification.allowed_values is not None and not set(value).issubset(specification.allowed_values):
            raise ValueError(f"{field_name} contains a value outside its controlled set")
    elif specification.field_type == "enum" and specification.allowed_values is not None:
        if value not in specification.allowed_values:
            raise ValueError(f"{field_name} is outside its controlled set")
    elif specification.field_type == "date":
        if not _DATE_PATTERN.fullmatch(value):
            raise ValueError(f"{field_name} requires an ISO year, month, or date")
        parts = value.split("-")
        if len(parts) == 2 and not 1 <= int(parts[1]) <= 12:
            raise ValueError(f"{field_name} requires a valid ISO month")
        if len(parts) == 3:
            try:
                date.fromisoformat(value)
            except ValueError as error:
                raise ValueError(f"{field_name} requires a valid ISO date") from error


def validate_entity_role(role: str) -> None:
    """Reject a Chinese-entity role that is not one of the canonical involvement values."""
    if role not in ENTITY_ROLES:
        raise ValueError("entity role is outside its controlled set")
