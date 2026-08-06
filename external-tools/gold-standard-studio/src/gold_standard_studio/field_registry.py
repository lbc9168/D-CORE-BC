"""Define the single reviewed-field registry used by Studio contracts and projections."""

from dataclasses import dataclass
from typing import Literal


FieldType = Literal["text", "enum", "set", "number", "date"]


@dataclass(frozen=True)
class FieldSpec:
    """Describe one reviewed field's value type, evidence requirement, and evaluation mapping."""

    field_type: FieldType
    evidence_required: bool
    eval_metric: str | None


FIELD_REGISTRY = {
    "project_name": FieldSpec("text", evidence_required=True, eval_metric="name_similarity"),
    "country": FieldSpec("enum", evidence_required=False, eval_metric="exact"),
    "location": FieldSpec("text", evidence_required=False, eval_metric="normalized_exact"),
    "category": FieldSpec("enum", evidence_required=True, eval_metric="exact"),
    "generation_sources": FieldSpec("set", evidence_required=True, eval_metric="set_f1"),
    "storage_status": FieldSpec("enum", evidence_required=True, eval_metric="exact"),
    "capacity_mw": FieldSpec("number", evidence_required=True, eval_metric="numeric_5pct"),
    "storage_power_mw": FieldSpec("number", evidence_required=True, eval_metric="numeric_5pct"),
    "storage_energy_mwh": FieldSpec("number", evidence_required=True, eval_metric="numeric_5pct"),
    "project_stage": FieldSpec("enum", evidence_required=True, eval_metric="exact"),
    "project_developer": FieldSpec("text", evidence_required=True, eval_metric="normalized_name"),
    "planned_start_date": FieldSpec("date", evidence_required=True, eval_metric="date_exact"),
    "planned_complete_date": FieldSpec("date", evidence_required=True, eval_metric="date_exact"),
    "investment_date": FieldSpec("date", evidence_required=True, eval_metric="date_exact"),
    "total_investment_usd": FieldSpec("number", evidence_required=True, eval_metric="numeric_5pct"),
    "contract_amount_usd": FieldSpec("number", evidence_required=True, eval_metric="numeric_5pct"),
    "financing_amount_usd": FieldSpec("number", evidence_required=True, eval_metric="numeric_5pct"),
    "financing_model": FieldSpec("enum", evidence_required=True, eval_metric="exact"),
    "project_summary": FieldSpec("text", evidence_required=False, eval_metric=None),
}
