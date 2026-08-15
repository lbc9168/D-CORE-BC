"""Evaluate immutable Pilot Gold with type-aware, capability-honest layered diagnostics."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import uuid
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from .draft_store import REQUIRED_FIELDS
from .pilot_release import FACT_FIELDS, sha256_file
from .single_article import load_article

TOOL_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RELEASE_ROOT = TOOL_ROOT / "data" / "releases"
DEFAULT_EVALUATION_ROOT = TOOL_ROOT / "data" / "evaluations"
RUN_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]*\Z")
ABSENT_STATES = {"not_mentioned", "not_applicable"}
NUMERIC_FIELDS = {"storage_power_mw", "storage_energy_mwh", "generation_capacity_mw"}
DATE_FIELDS = {"planned_start_date", "planned_complete_date"}
SET_FIELDS = {"generation_sources"}
TEXT_FIELDS = set(REQUIRED_FIELDS) - NUMERIC_FIELDS - DATE_FIELDS - SET_FIELDS - {
    "entities", "normalized_entities", "investment",
}
EVALUATOR_SCHEMA = "layered-eval-v2"
NUMERIC_REL_TOLERANCE = 0.01
NUMERIC_ABS_TOLERANCE = 0.001


def _json_bytes(value: Any) -> bytes:
    """Serialize one deterministic JSON artifact with a trailing newline."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _normalize_text(value: Any) -> str:
    """Normalize Unicode-like text for strict semantic equality without fuzzy correctness."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def _effectively_empty(value: Any) -> bool:
    """Treat recursively empty structures as absent while retaining meaningful enum strings."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip() or value.strip().casefold() == "unknown"
    if isinstance(value, (list, tuple, set)):
        return not value or all(_effectively_empty(item) for item in value)
    if isinstance(value, dict):
        return not value or all(_effectively_empty(item) for item in value.values())
    return False


def _to_number(value: Any) -> float | None:
    """Parse a scalar number without inferring units from narrative text."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    text = str(value).strip().replace(",", "")
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
        return float(text)
    return None


def _normalize_date(value: Any) -> str | None:
    """Normalize explicit year, year-month, or full ISO dates without inventing components."""
    if value is None:
        return None
    match = re.fullmatch(r"\s*(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?\s*", str(value))
    if not match:
        return None
    output = f"{int(match[1]):04d}"
    if match[2] is not None:
        output += f"-{int(match[2]):02d}"
    if match[3] is not None:
        output += f"-{int(match[3]):02d}"
    return output


def _entity_set(value: Any, normalized: bool) -> set[tuple[str, ...]]:
    """Convert raw or normalized entity lists to order-independent semantic tuples."""
    output: set[tuple[str, ...]] = set()
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        if normalized:
            output.add((
                _normalize_text(item.get("canonical_entity") or item.get("name")),
                _normalize_text(item.get("parent_company")),
                _normalize_text(item.get("role") or item.get("involvement_type")),
            ))
        else:
            output.add((
                _normalize_text(item.get("name")),
                _normalize_text(item.get("role") or item.get("involvement_type")),
            ))
    return {item for item in output if item[0]}


def _investment_slots(value: Any) -> dict[str, dict[str, Any]]:
    """Project heterogeneous investment values into total, contract, and financing slots."""
    slots = {name: {"number": None, "raw": ""} for name in ("total", "contract", "financing")}
    if _effectively_empty(value):
        return slots
    if isinstance(value, dict):
        for name, prefix in (("total", "total_project_investment"), ("contract", "contract_amount"),
                             ("financing", "financing_amount")):
            slots[name]["number"] = _to_number(value.get(f"{prefix}_million_usd"))
            slots[name]["raw"] = _normalize_text(value.get(f"{prefix}_raw"))
        return slots
    text = str(value).strip()
    parsed = _to_number(text)
    if parsed is not None:
        slots["total"]["number"] = parsed
        return slots
    for name, key in (("total", "total_project_investment_million_usd"),
                      ("contract", "contract_amount_million_usd"),
                      ("financing", "financing_amount_million_usd")):
        match = re.search(rf'"?{key}"?\s*:\s*([-+]?\d+(?:\.\d+)?)', text)
        if match:
            slots[name]["number"] = float(match.group(1))
    if not any(slot["number"] is not None for slot in slots.values()):
        slots["total"]["raw"] = _normalize_text(text)
    return slots


def _compare_investment(gold: Any, predicted: Any) -> tuple[bool | None, str]:
    """Compare compatible investment slots and mark currency/schema incompatibility unscorable."""
    gold_slots = _investment_slots(gold)
    predicted_slots = _investment_slots(predicted)
    comparable = 0
    mismatches: list[str] = []
    for name in gold_slots:
        gold_number = gold_slots[name]["number"]
        predicted_number = predicted_slots[name]["number"]
        if gold_number is not None:
            comparable += 1
            if predicted_number is None or not math.isclose(
                gold_number, predicted_number, rel_tol=NUMERIC_REL_TOLERANCE,
                abs_tol=NUMERIC_ABS_TOLERANCE,
            ):
                mismatches.append(name)
        elif gold_slots[name]["raw"]:
            if predicted_slots[name]["raw"]:
                comparable += 1
                if gold_slots[name]["raw"] != predicted_slots[name]["raw"]:
                    mismatches.append(name)
    if not comparable:
        return None, "investment_schema_or_currency_incompatible"
    return not mismatches, "investment_slots_match" if not mismatches else f"investment_slot_mismatch:{','.join(mismatches)}"


def compare_field(field_name: str, gold_envelope: dict[str, Any], predicted: Any) -> dict[str, Any]:
    """Compare one field by Gold state and type, distinguishing wrong from unscorable."""
    state = gold_envelope.get("value_state")
    gold = gold_envelope.get("value")
    if state in {"uncertain", "needs_adjudication"}:
        raise ValueError(f"已发布 Gold 不允许状态 {state}: {field_name}")
    if state in ABSENT_STATES:
        predicted_absent = _effectively_empty(predicted) or (
            field_name == "storage_status" and _normalize_text(predicted) == "not_mentioned"
        )
        return {"scorable": True, "correct": predicted_absent, "reason": (
            "coarse_absence_match" if predicted_absent else "unexpected_present_value"
        )}
    if state != "present":
        return {"scorable": False, "correct": None, "reason": f"unsupported_gold_state:{state}"}
    if _effectively_empty(gold):
        return {"scorable": False, "correct": None, "reason": "gold_present_without_normalized_value"}

    if field_name in NUMERIC_FIELDS:
        gold_number, predicted_number = _to_number(gold), _to_number(predicted)
        correct = gold_number is not None and predicted_number is not None and math.isclose(
            gold_number, predicted_number, rel_tol=NUMERIC_REL_TOLERANCE,
            abs_tol=NUMERIC_ABS_TOLERANCE,
        )
        reason = "numeric_match" if correct else "numeric_mismatch_or_missing"
    elif field_name in DATE_FIELDS:
        gold_date, predicted_date = _normalize_date(gold), _normalize_date(predicted)
        if gold_date is None:
            return {"scorable": False, "correct": None, "reason": "gold_date_not_normalized"}
        correct = gold_date == predicted_date
        reason = "date_match" if correct else "date_mismatch_or_missing"
    elif field_name in SET_FIELDS:
        gold_set = {_normalize_text(item) for item in gold if not _effectively_empty(item)}
        predicted_set = {_normalize_text(item) for item in predicted if not _effectively_empty(item)} if isinstance(predicted, list) else set()
        correct = gold_set == predicted_set
        reason = "set_match" if correct else f"set_mismatch:missing={sorted(gold_set-predicted_set)},extra={sorted(predicted_set-gold_set)}"
    elif field_name == "entities":
        gold_set, predicted_set = _entity_set(gold, False), _entity_set(predicted, False)
        correct = gold_set == predicted_set
        reason = "entity_pair_match" if correct else f"entity_pair_mismatch:missing={sorted(gold_set-predicted_set)},extra={sorted(predicted_set-gold_set)}"
    elif field_name == "normalized_entities":
        gold_set, predicted_set = _entity_set(gold, True), _entity_set(predicted, True)
        correct = gold_set == predicted_set
        reason = "normalized_entity_match" if correct else f"normalized_entity_mismatch:missing={sorted(gold_set-predicted_set)},extra={sorted(predicted_set-gold_set)}"
    elif field_name == "investment":
        correct, reason = _compare_investment(gold, predicted)
        if correct is None:
            return {"scorable": False, "correct": None, "reason": reason}
    elif field_name in TEXT_FIELDS:
        correct = _normalize_text(gold) == _normalize_text(predicted)
        reason = "text_match" if correct else "text_mismatch_or_missing"
    else:
        return {"scorable": False, "correct": None, "reason": "no_comparator"}
    return {"scorable": True, "correct": bool(correct), "reason": reason}


def _gold_value(project: dict[str, Any], field_name: str) -> Any:
    """Return one reviewed Gold field value from a rich project."""
    return (project.get("fields") or {}).get(field_name, {}).get("value")


def _project_name(project: dict[str, Any], gold: bool) -> str:
    """Return a comparable project name from a rich Gold or plain prediction project."""
    return str(_gold_value(project, "project_name") if gold else project.get("project_name") or "")


def _project_similarity(gold: dict[str, Any], predicted: dict[str, Any]) -> tuple[float, dict[str, float]]:
    """Score a project pair by name, country, and location without declaring fuzzy fields correct."""
    name = SequenceMatcher(None, _normalize_text(_project_name(gold, True)),
                           _normalize_text(_project_name(predicted, False))).ratio()
    country = float(bool(_normalize_text(_gold_value(gold, "country"))) and
                    _normalize_text(_gold_value(gold, "country")) == _normalize_text(predicted.get("country")))
    location = SequenceMatcher(None, _normalize_text(_gold_value(gold, "location")),
                               _normalize_text(predicted.get("location"))).ratio()
    return 0.8 * name + 0.1 * country + 0.1 * location, {
        "name": name, "country": country, "location": location,
    }


def align_projects(gold: list[dict[str, Any]], predicted: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a deterministic global greedy one-to-one project alignment with auditable scores."""
    candidates: list[tuple[float, int, int, dict[str, float]]] = []
    for gold_index, gold_project in enumerate(gold):
        for predicted_index, predicted_project in enumerate(predicted):
            score, signals = _project_similarity(gold_project, predicted_project)
            if score >= 0.42:
                candidates.append((score, gold_index, predicted_index, signals))
    used_gold: set[int] = set()
    used_predicted: set[int] = set()
    matches: list[dict[str, Any]] = []
    for score, gold_index, predicted_index, signals in sorted(candidates, key=lambda item: (-item[0], item[1], item[2])):
        if gold_index in used_gold or predicted_index in used_predicted:
            continue
        used_gold.add(gold_index)
        used_predicted.add(predicted_index)
        matches.append({"gold_index": gold_index, "predicted_index": predicted_index,
                        "score": score, "signals": signals})
    return {"matches": sorted(matches, key=lambda item: item["gold_index"]),
            "unmatched_gold": sorted(set(range(len(gold))) - used_gold),
            "unmatched_predicted": sorted(set(range(len(predicted))) - used_predicted)}


def _database_project(project: dict[str, Any]) -> dict[str, Any]:
    """Adapt one current main-database project to final Gold field names without inventing facts."""
    entities = project.get("entities") or []
    category = project.get("category")
    generation_capacity = None if category in {"储能", "新能源制造业", "输变电建设"} else project.get("capacity_mw")
    investment = {
        "total_project_investment_million_usd": project.get("total_investment_usd"),
        "total_project_investment_raw": project.get("total_investment_usd_raw"),
        "contract_amount_million_usd": project.get("contract_amount_usd"),
        "contract_amount_raw": project.get("contract_amount_usd_raw"),
        "financing_amount_million_usd": project.get("financing_amount_usd"),
        "financing_amount_raw": project.get("financing_amount_usd_raw"),
    }
    return {
        "project_name": project.get("project_name"), "category": category,
        "subcategory": project.get("subcategory"), "generation_sources": None,
        "storage_status": "confirmed" if (project.get("storage_power_mw") is not None or project.get("storage_energy_mwh") is not None) else "not_mentioned",
        "storage_power_mw": project.get("storage_power_mw"), "storage_energy_mwh": project.get("storage_energy_mwh"),
        "generation_capacity_mw": generation_capacity, "project_stage": project.get("project_stage"),
        "country": project.get("country"), "location": project.get("location"),
        "normalized_location": project.get("location_norm") or project.get("location"),
        "investment": investment, "financing_mode": None if _normalize_text(project.get("financing_model")) == "unknown" else project.get("financing_model"),
        "project_developer": project.get("project_developer"),
        "planned_start_date": project.get("planned_start_date"), "planned_complete_date": project.get("planned_complete_date"),
        "entities": [{"name": item.get("subsidiary_name") or item.get("implementing_entity"),
                      "role": item.get("involvement_type")} for item in entities],
        "normalized_entities": [
            {"canonical_entity": item.get("subsidiary_name") or item.get("implementing_entity"),
             "parent_company": item.get("implementing_entity"), "role": item.get("involvement_type")}
            for item in entities
        ],
    }


def _prf(gold_count: int, predicted_count: int, matched_count: int) -> dict[str, Any]:
    """Compute empty-safe project precision, recall, and F1."""
    precision = matched_count / predicted_count if predicted_count else float(gold_count == 0)
    recall = matched_count / gold_count if gold_count else float(predicted_count == 0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"gold": gold_count, "predicted": predicted_count, "matched": matched_count,
            "precision": precision, "recall": recall, "f1": f1}


def _evaluate_layer(
    layer: str,
    records: list[dict[str, Any]],
    predictions: dict[int, list[dict[str, Any]]],
    fields: Iterable[str],
    available_fields: set[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Evaluate project sets and typed fields for one supported layer."""
    field_stats = {name: {"correct": 0, "incorrect": 0, "unscorable": 0} for name in fields}
    errors: list[dict[str, Any]] = []
    unscorable: list[dict[str, Any]] = []
    total_gold = total_predicted = total_matched = 0
    article_rows: list[dict[str, Any]] = []
    for record in records:
        article_id = int(record["article_id"])
        gold_projects = record.get("projects") or []
        predicted_projects = predictions.get(article_id, [])
        alignment = align_projects(gold_projects, predicted_projects)
        total_gold += len(gold_projects); total_predicted += len(predicted_projects); total_matched += len(alignment["matches"])
        for index in alignment["unmatched_gold"]:
            errors.append({"layer": layer, "article_id": article_id, "kind": "missing_project",
                           "gold_project_id": gold_projects[index].get("gold_project_id"),
                           "gold_project_name": _project_name(gold_projects[index], True)})
        for index in alignment["unmatched_predicted"]:
            errors.append({"layer": layer, "article_id": article_id, "kind": "extra_project",
                           "predicted_project_name": _project_name(predicted_projects[index], False)})
        for match in alignment["matches"]:
            gold_project = gold_projects[match["gold_index"]]
            predicted_project = predicted_projects[match["predicted_index"]]
            for field_name in fields:
                envelope = (gold_project.get("fields") or {}).get(field_name) or {}
                if available_fields is not None and field_name not in available_fields:
                    field_stats[field_name]["unscorable"] += 1
                    unscorable.append({"layer": layer, "article_id": article_id,
                                       "gold_project_id": gold_project.get("gold_project_id"),
                                       "field": field_name,
                                       "reason": "prediction_field_unavailable_in_snapshot"})
                    continue
                result = compare_field(field_name, envelope, predicted_project.get(field_name))
                if not result["scorable"]:
                    field_stats[field_name]["unscorable"] += 1
                    unscorable.append({"layer": layer, "article_id": article_id,
                                       "gold_project_id": gold_project.get("gold_project_id"),
                                       "field": field_name, "reason": result["reason"]})
                elif result["correct"]:
                    field_stats[field_name]["correct"] += 1
                else:
                    field_stats[field_name]["incorrect"] += 1
                    errors.append({"layer": layer, "article_id": article_id, "kind": "field_mismatch",
                                   "gold_project_id": gold_project.get("gold_project_id"),
                                   "gold_project_name": _project_name(gold_project, True),
                                   "predicted_project_name": _project_name(predicted_project, False),
                                   "match_score": match["score"], "field": field_name,
                                   "gold_state": envelope.get("value_state"), "gold_value": envelope.get("value"),
                                   "predicted_value": predicted_project.get(field_name), "reason": result["reason"]})
        article_rows.append({"article_id": article_id,
                             "projects": _prf(len(gold_projects), len(predicted_projects), len(alignment["matches"])),
                             "alignment": alignment})
    for stats in field_stats.values():
        denominator = stats["correct"] + stats["incorrect"]
        stats["accuracy"] = stats["correct"] / denominator if denominator else None
        stats["support"] = "scored" if denominator else "unavailable_or_unscorable"
    correct = sum(stats["correct"] for stats in field_stats.values())
    incorrect = sum(stats["incorrect"] for stats in field_stats.values())
    return {
        "projects": _prf(total_gold, total_predicted, total_matched),
        "fields": {"correct": correct, "incorrect": incorrect,
                   "unscorable": sum(stats["unscorable"] for stats in field_stats.values()),
                   "micro_accuracy": correct / (correct + incorrect) if correct + incorrect else None,
                   "by_field": field_stats},
        "articles": article_rows,
    }, errors, unscorable


def _load_release(release_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load and hash-check an immutable release before using its rich Gold."""
    manifest_path = release_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"缺少 release manifest：{manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for relative, expected in manifest.get("artifacts", {}).items():
        path = release_dir / relative
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"release artifact hash 不匹配：{relative}")
    records = [json.loads(line) for line in (release_dir / "rich-gold.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(records) != manifest.get("article_count"):
        raise ValueError("rich Gold 行数与 manifest 不一致")
    return manifest, records


def build_report(release_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build a capability-honest stored snapshot v2 report from immutable Gold and read-only sources."""
    manifest, records = _load_release(release_dir)
    contexts = {int(record["article_id"]): load_article(int(record["article_id"])) for record in records}
    s01b_predictions = {article_id: context["historical_extraction_projects"] for article_id, context in contexts.items()}
    database_predictions = {article_id: [_database_project(item) for item in context["database_projects"]]
                            for article_id, context in contexts.items()}
    stored_s01b_available = set(FACT_FIELDS) - {"generation_sources", "storage_status", "subcategory"}
    s01b, s01b_errors, s01b_unscorable = _evaluate_layer(
        "s01b", records, s01b_predictions, FACT_FIELDS, stored_s01b_available
    )
    s02b, s02b_errors, s02b_unscorable = _evaluate_layer(
        "s02b", records, database_predictions, ("normalized_entities", "normalized_location")
    )
    end_to_end, end_errors, end_unscorable = _evaluate_layer(
        "end_to_end", records, database_predictions, sorted(REQUIRED_FIELDS),
        set(REQUIRED_FIELDS) - {"generation_sources"},
    )
    binary_correct = 0
    gate_errors: list[dict[str, Any]] = []
    for record in records:
        article_id = int(record["article_id"])
        gold_valid = record.get("article_status") == "valid"
        predicted_valid = bool(s01b_predictions[article_id])
        binary_correct += int(gold_valid == predicted_valid)
        if gold_valid != predicted_valid:
            gate_errors.append({"layer": "s01b", "article_id": article_id, "kind": "binary_gate_mismatch",
                                "gold_status": record.get("article_status"), "predicted_has_projects": predicted_valid})
    source_paths: dict[str, str] = {}
    for context in contexts.values():
        source_paths.update(context.get("source_paths") or {})
    evaluator_path = Path(__file__)
    report = {
        "schema_version": EVALUATOR_SCHEMA,
        "baseline_kind": "stored_pipeline_snapshot_v2",
        "release": {"version": manifest["release_version"],
                    "manifest_sha256": sha256_file(release_dir / "manifest.json"),
                    "rich_gold_sha256": sha256_file(release_dir / "rich-gold.jsonl")},
        "evaluator": {"sha256": sha256_file(evaluator_path), "numeric_rel_tolerance": NUMERIC_REL_TOLERANCE,
                      "numeric_abs_tolerance": NUMERIC_ABS_TOLERANCE},
        "source_databases": {name: {"sha256": sha256_file(Path(path))} for name, path in source_paths.items()},
        "capabilities": {
            "s01b_article_and_facts": "supported_stored_snapshot",
            "s02a_article_project_structure": "diagnostic_only_via_end_to_end_project_set",
            "s02a_cross_article_duplicate_merger": "unsupported_missing_canonical_cluster_gold",
            "s02b_entities_and_location": "supported_current_database_snapshot",
            "s03_admin1": "unsupported_missing_admin1_gold",
            "deterministic_taxonomy": "unsupported_inputs_and_policy_output_not_recorded_in_snapshot",
        },
        "article_gate_binary": {"correct": binary_correct, "total": len(records),
                                "accuracy": binary_correct / len(records) if records else None,
                                "note": "stored projects only support valid vs non-valid"},
        "s01b": s01b,
        "s02b": s02b,
        "end_to_end": end_to_end,
        "unscorable": s01b_unscorable + s02b_unscorable + end_unscorable,
        "limitations": [
            "No model was rerun; S01B predictions come from stored projects.db rows.",
            "S02A cross-article canonical clustering and S03 admin1 are not scored by this Gold release.",
            "Plain stored predictions cannot distinguish not_mentioned from not_applicable; absence is scored coarsely.",
            "Stored projects.db has no generation_sources, storage_status, or subcategory columns; those S01B fields are unscorable rather than wrong.",
            "End-to-end mismatches describe final divergence and do not by themselves prove the responsible stage.",
        ],
    }
    return report, gate_errors + s01b_errors + s02b_errors + end_errors


def _summary_markdown(report: dict[str, Any], errors: list[dict[str, Any]]) -> str:
    """Render a compact human-readable summary from the canonical JSON report."""
    project = report["s01b"]["projects"]
    top_fields = sorted(
        ((name, stats["accuracy"], stats["correct"] + stats["incorrect"], stats["unscorable"])
         for name, stats in report["s01b"]["fields"]["by_field"].items()),
        key=lambda item: (item[1] is None, item[1] if item[1] is not None else 2),
    )
    lines = [
        f"# {report['release']['version']} · stored snapshot v2", "",
        "## Supported results", "",
        f"- Binary valid/non-valid accuracy: {report['article_gate_binary']['accuracy']:.3f}",
        f"- S01B project precision/recall/F1: {project['precision']:.3f}/{project['recall']:.3f}/{project['f1']:.3f}",
        f"- S01B typed field micro accuracy: {report['s01b']['fields']['micro_accuracy']:.3f}",
        f"- S02B typed field micro accuracy: {report['s02b']['fields']['micro_accuracy']:.3f}",
        f"- End-to-end typed field micro accuracy: {report['end_to_end']['fields']['micro_accuracy']:.3f}",
        f"- Recorded mismatches: {len(errors)}; unscorable comparisons: {len(report['unscorable'])}", "",
        "## Lowest S01B fields", "",
    ]
    for name, accuracy, denominator, unscorable in top_fields[:8]:
        rendered = "n/a" if accuracy is None else f"{accuracy:.3f}"
        lines.append(f"- `{name}`: {rendered} (scored={denominator}, unscorable={unscorable})")
    lines.extend(["", "## Unsupported in this release", "",
                  "- Full S02A cross-article duplicate merger: missing canonical cluster Gold.",
                  "- S03 province/admin1: missing admin1 Gold field.",
                  "- Deterministic taxonomy: stored snapshot lacks a complete fact-input/policy-output trace.", "",
                  "This report did not call a model or modify Gold, Prompt, policy, registry, or upstream databases.", ""])
    return "\n".join(lines)


def run_evaluation(
    release_version: str = "pilot-v0.1.0",
    run_id: str = "stored-snapshot-v2",
    release_root: Path = DEFAULT_RELEASE_ROOT,
    evaluation_root: Path = DEFAULT_EVALUATION_ROOT,
) -> Path:
    """Atomically write one immutable evaluation run and refuse run-id reuse."""
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run id 只能包含小写字母、数字、点、下划线和连字符")
    release_dir = release_root / release_version
    final_dir = evaluation_root / release_version / run_id
    if final_dir.exists():
        raise FileExistsError(f"评估 run 已存在，禁止覆盖：{final_dir}")
    report, errors = build_report(release_dir)
    temporary = final_dir.parent / f".{run_id}.tmp-{uuid.uuid4().hex}"
    try:
        temporary.mkdir(parents=True)
        (temporary / "report.json").write_bytes(_json_bytes(report))
        (temporary / "errors.jsonl").write_bytes(b"".join(_json_bytes(item) for item in errors))
        (temporary / "summary.md").write_text(_summary_markdown(report, errors), encoding="utf-8", newline="\n")
        artifacts = {path.name: sha256_file(path) for path in sorted(temporary.iterdir()) if path.is_file()}
        run_manifest = {
            "schema_version": EVALUATOR_SCHEMA, "release_version": release_version,
            "run_id": run_id, "immutable": True, "artifacts": artifacts,
            "report_sha256": artifacts["report.json"],
        }
        (temporary / "run-manifest.json").write_bytes(_json_bytes(run_manifest))
        json.loads((temporary / "report.json").read_text(encoding="utf-8"))
        for line in (temporary / "errors.jsonl").read_text(encoding="utf-8").splitlines():
            json.loads(line)
        if final_dir.exists():
            raise FileExistsError(f"评估 run 已存在，禁止覆盖：{final_dir}")
        os.replace(temporary, final_dir)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return final_dir


def _main(argv: list[str] | None = None) -> int:
    """Run the immutable layered evaluator from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-version", default="pilot-v0.1.0")
    parser.add_argument("--run-id", default="stored-snapshot-v2")
    parser.add_argument("--release-root", type=Path, default=DEFAULT_RELEASE_ROOT)
    parser.add_argument("--evaluation-root", type=Path, default=DEFAULT_EVALUATION_ROOT)
    args = parser.parse_args(argv)
    try:
        path = run_evaluation(args.release_version, args.run_id, args.release_root, args.evaluation_root)
        print(json.dumps({"ok": True, "path": str(path)}, ensure_ascii=False, indent=2))
        return 0
    except (FileNotFoundError, FileExistsError, ValueError, OSError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
