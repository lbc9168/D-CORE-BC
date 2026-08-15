"""Compare frozen Terra Provider and Sol Reviewer candidates against one immutable Gold release."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

from .layered_evaluator import RUN_ID_PATTERN, _evaluate_layer, _json_bytes, _load_release
from .pilot_release import FACT_FIELDS, sha256_file

TOOL_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RELEASE_ROOT = TOOL_ROOT / "data" / "releases"
DEFAULT_CANDIDATE_ROOT = TOOL_ROOT / "data" / "candidates"
DEFAULT_RUN_ROOT = TOOL_ROOT / "data" / "runs"
DEFAULT_EXPERIMENT_ROOT = TOOL_ROOT / "data" / "experiments"
EXPERIMENT_SCHEMA = "frozen-candidate-experiment-v1"
PRIMARY_FIELDS = tuple(field for field in FACT_FIELDS if field not in {"category", "subcategory"})
EXPECTED_MODELS = {"provider": "gpt-5.6-terra", "reviewer": "gpt-5.6-sol"}
STATUS_MAP = {
    "valid_project": "valid",
    "valid": "valid",
    "no_chinese_participation": "no_chinese",
    "no_chinese": "no_chinese",
    "irrelevant": "irrelevant",
    "other_non_project": "other_non_project",
}


def _unwrap(envelope: Any) -> Any:
    """Return the semantic value from one candidate envelope without inventing absent values."""
    if isinstance(envelope, dict) and "value" in envelope:
        return envelope.get("value")
    return envelope


def _adapt_entities(value: Any) -> list[dict[str, Any]]:
    """Map nested candidate entity envelopes to the raw Gold name-role contract."""
    entities: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        name = _unwrap(item.get("entity"))
        role = _unwrap(item.get("role"))
        if name:
            entities.append({"name": name, "role": role})
    return entities


def adapt_project(project: dict[str, Any]) -> dict[str, Any]:
    """Adapt one frozen model project to the evaluator's plain S01B prediction contract."""
    if not isinstance(project, dict):
        raise ValueError("candidate project 必须是 JSON object")
    adapted = {field: _unwrap(project.get(field)) for field in PRIMARY_FIELDS}
    adapted["entities"] = _adapt_entities(project.get("chinese_entities"))
    adapted["candidate_category"] = _unwrap(project.get("category"))
    adapted["subcategory_raw_fact"] = _unwrap(project.get("subcategory_raw_fact"))
    return adapted


def _candidate_status(candidate: dict[str, Any]) -> str:
    """Normalize one candidate article status and reject unknown labels."""
    raw = _unwrap(candidate.get("article_status"))
    if raw not in STATUS_MAP:
        raise ValueError(f"不支持的候选文章状态：{raw!r}")
    return STATUS_MAP[raw]


def _load_candidates(
    role: str,
    records: list[dict[str, Any]],
    candidate_root: Path,
) -> tuple[dict[int, dict[str, Any]], dict[str, str]]:
    """Load and strictly validate one role's complete frozen candidate set."""
    if role not in EXPECTED_MODELS:
        raise ValueError(f"不支持的实验角色：{role}")
    prefix = "terra-provider" if role == "provider" else "sol-reviewer"
    expected_ids = {int(record["article_id"]) for record in records}
    gold_hashes = {int(record["article_id"]): record["article"]["content_sha256"] for record in records}
    paths = sorted(candidate_root.glob(f"{prefix}-*.json"))
    candidates: dict[int, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    for path in paths:
        candidate = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(candidate, dict):
            raise ValueError(f"candidate 顶层必须是 JSON object：{path.name}")
        article_id = candidate.get("article_id")
        if not isinstance(article_id, int):
            raise ValueError(f"candidate article_id 非法：{path.name}")
        if article_id not in expected_ids:
            continue
        if article_id in candidates:
            raise ValueError(f"candidate article_id 非法或重复：{path.name}")
        if candidate.get("execution_role") != role:
            raise ValueError(f"candidate role 不匹配：{path.name}")
        if candidate.get("model") != EXPECTED_MODELS[role]:
            raise ValueError(f"candidate model 不匹配：{path.name}")
        if candidate.get("article_sha256") != gold_hashes.get(article_id):
            raise ValueError(f"candidate article hash 不匹配：{path.name}")
        if not isinstance(candidate.get("candidate_id"), str) or not isinstance(candidate.get("projects"), list):
            raise ValueError(f"candidate 缺少 candidate_id/projects：{path.name}")
        _candidate_status(candidate)
        candidates[article_id] = candidate
        hashes[path.name] = sha256_file(path)
    if set(candidates) != expected_ids:
        raise ValueError(
            f"{role} candidate ID 集合不匹配：missing={sorted(expected_ids-set(candidates))}, "
            f"extra={sorted(set(candidates)-expected_ids)}"
        )
    return candidates, hashes


def _validate_parent_links(
    providers: dict[int, dict[str, Any]],
    reviewers: dict[int, dict[str, Any]],
) -> None:
    """Require every reviewer candidate to point to the corresponding frozen provider candidate."""
    for article_id, reviewer in reviewers.items():
        expected = providers[article_id]["candidate_id"]
        if reviewer.get("parent_candidate_id") != expected:
            raise ValueError(f"Reviewer parent_candidate_id 不匹配：article_id={article_id}")


def _gate_report(records: list[dict[str, Any]], candidates: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Compute exact four-class and binary valid/non-valid article-gate accuracy."""
    exact_correct = binary_correct = 0
    rows: list[dict[str, Any]] = []
    for record in records:
        article_id = int(record["article_id"])
        gold = record["article_status"]
        predicted = _candidate_status(candidates[article_id])
        exact = gold == predicted
        binary = (gold == "valid") == (predicted == "valid")
        exact_correct += int(exact)
        binary_correct += int(binary)
        rows.append({"article_id": article_id, "gold": gold, "predicted": predicted,
                     "exact_correct": exact, "binary_correct": binary})
    total = len(records)
    return {
        "four_class": {"correct": exact_correct, "total": total,
                       "accuracy": exact_correct / total if total else None},
        "binary_valid": {"correct": binary_correct, "total": total,
                         "accuracy": binary_correct / total if total else None},
        "articles": rows,
    }


def _role_report(
    role: str,
    records: list[dict[str, Any]],
    candidates: dict[int, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Evaluate one candidate role with the shared typed field and project-alignment semantics."""
    predictions = {
        article_id: [adapt_project(project) for project in candidate["projects"]]
        for article_id, candidate in candidates.items()
    }
    layer, errors, unscorable = _evaluate_layer(f"{role}_s01b", records, predictions, PRIMARY_FIELDS)
    return {
        "role": role,
        "model": EXPECTED_MODELS[role],
        "gate": _gate_report(records, candidates),
        "s01b": layer,
        "unscorable": unscorable,
        "excluded_from_primary": {
            "category": "candidate category is not a recorded deterministic taxonomy trace",
            "subcategory": "candidate subcategory_raw_fact is not final Gold subcategory",
        },
    }, errors


def _error_key(error: dict[str, Any]) -> tuple[Any, ...]:
    """Identify the same Gold-side mismatch across Provider and Reviewer reports."""
    return (error.get("article_id"), error.get("gold_project_id"), error.get("field"), error.get("kind"))


def _comparison(
    provider: dict[str, Any],
    reviewer: dict[str, Any],
    provider_errors: list[dict[str, Any]],
    reviewer_errors: list[dict[str, Any]],
) -> dict[str, Any]:
    """Calculate Reviewer-minus-Provider aggregate, per-field, and mismatch-transition deltas."""
    provider_fields = provider["s01b"]["fields"]["by_field"]
    reviewer_fields = reviewer["s01b"]["fields"]["by_field"]
    fields: dict[str, Any] = {}
    for name in PRIMARY_FIELDS:
        before = provider_fields[name]
        after = reviewer_fields[name]
        fields[name] = {
            "provider_accuracy": before["accuracy"],
            "reviewer_accuracy": after["accuracy"],
            "accuracy_delta": (
                after["accuracy"] - before["accuracy"]
                if before["accuracy"] is not None and after["accuracy"] is not None else None
            ),
            "correct_delta": after["correct"] - before["correct"],
            "incorrect_delta": after["incorrect"] - before["incorrect"],
            "unscorable_delta": after["unscorable"] - before["unscorable"],
        }
    provider_by_key = {_error_key(error): error for error in provider_errors}
    reviewer_by_key = {_error_key(error): error for error in reviewer_errors}
    resolved_keys = sorted(set(provider_by_key) - set(reviewer_by_key))
    introduced_keys = sorted(set(reviewer_by_key) - set(provider_by_key))
    return {
        "direction": "reviewer_minus_provider",
        "four_class_gate_accuracy_delta": (
            reviewer["gate"]["four_class"]["accuracy"] - provider["gate"]["four_class"]["accuracy"]
        ),
        "binary_gate_accuracy_delta": (
            reviewer["gate"]["binary_valid"]["accuracy"] - provider["gate"]["binary_valid"]["accuracy"]
        ),
        "project_f1_delta": reviewer["s01b"]["projects"]["f1"] - provider["s01b"]["projects"]["f1"],
        "field_micro_accuracy_delta": (
            reviewer["s01b"]["fields"]["micro_accuracy"] - provider["s01b"]["fields"]["micro_accuracy"]
        ),
        "fields": fields,
        "mismatch_transitions": {
            "resolved_count": len(resolved_keys),
            "introduced_count": len(introduced_keys),
            "shared_count": len(set(provider_by_key) & set(reviewer_by_key)),
            "resolved_by_reviewer": [provider_by_key[key] for key in resolved_keys],
            "introduced_by_reviewer": [reviewer_by_key[key] for key in introduced_keys],
        },
    }


def _prompt_provenance(article_ids: list[int], run_root: Path) -> dict[str, Any]:
    """Hash available rendered Prompt packs while labelling the older 9027 provenance gap."""
    output: dict[str, Any] = {}
    for article_id in article_ids:
        run_dir = run_root / str(article_id)
        files: dict[str, str] = {}
        if run_dir.is_dir():
            for path in sorted(run_dir.iterdir()):
                if path.is_file() and (path.name.endswith(".prompt.txt") or path.name.endswith("manifest.json")):
                    files[path.name] = sha256_file(path)
        output[str(article_id)] = {
            "grade": "rendered_prompt_pack" if files else "candidate_source_paths_only",
            "files": files,
        }
    return output


def _summary_markdown(provider: dict[str, Any], reviewer: dict[str, Any], comparison: dict[str, Any]) -> str:
    """Render a concise development-set experiment summary with leakage warnings."""
    improved = sorted(
        ((name, values["accuracy_delta"]) for name, values in comparison["fields"].items()
         if values["accuracy_delta"] is not None and values["accuracy_delta"] > 0),
        key=lambda item: (-item[1], item[0]),
    )
    regressed = sorted(
        ((name, values["accuracy_delta"]) for name, values in comparison["fields"].items()
         if values["accuracy_delta"] is not None and values["accuracy_delta"] < 0),
        key=lambda item: (item[1], item[0]),
    )
    lines = [
        "# Frozen Terra → Sol development-set experiment", "",
        "> 这 10 篇在人工复核时展示过模型候选；结果仅用于开发诊断，不是无泄漏泛化评估。", "",
        "## Primary results", "",
        f"- Provider four-class/binary gate accuracy: {provider['gate']['four_class']['accuracy']:.3f}/{provider['gate']['binary_valid']['accuracy']:.3f}",
        f"- Reviewer four-class/binary gate accuracy: {reviewer['gate']['four_class']['accuracy']:.3f}/{reviewer['gate']['binary_valid']['accuracy']:.3f}",
        f"- Provider project P/R/F1: {provider['s01b']['projects']['precision']:.3f}/{provider['s01b']['projects']['recall']:.3f}/{provider['s01b']['projects']['f1']:.3f}",
        f"- Reviewer project P/R/F1: {reviewer['s01b']['projects']['precision']:.3f}/{reviewer['s01b']['projects']['recall']:.3f}/{reviewer['s01b']['projects']['f1']:.3f}",
        f"- Provider typed field micro accuracy: {provider['s01b']['fields']['micro_accuracy']:.3f}",
        f"- Reviewer typed field micro accuracy: {reviewer['s01b']['fields']['micro_accuracy']:.3f}",
        f"- Reviewer field micro delta: {comparison['field_micro_accuracy_delta']:+.3f}", "",
        f"- Reviewer resolved/introduced/shared mismatches: {comparison['mismatch_transitions']['resolved_count']}/"
        f"{comparison['mismatch_transitions']['introduced_count']}/{comparison['mismatch_transitions']['shared_count']}", "",
        "## Reviewer field deltas", "",
    ]
    lines.extend(f"- Improved `{name}`: {delta:+.3f}" for name, delta in improved)
    lines.extend(f"- Regressed `{name}`: {delta:+.3f}" for name, delta in regressed)
    if not improved and not regressed:
        lines.append("- No scored field accuracy changed.")
    lines.extend([
        "", "## Attribution limits", "",
        "- A Reviewer-introduced mismatch means disagreement with the current Gold, not automatically a Reviewer reasoning error; inspect `comparison.json` before changing a Prompt.",
        "- `category` and final `subcategory` are excluded because the frozen candidates do not contain a complete deterministic taxonomy trace.",
        "- S02A duplicate merging, S02B normalization, and S03 admin1 are not part of this Provider/Reviewer experiment.",
        "- Article 9027 predates the rendered Prompt pack; its candidate JSON records source paths but not the complete rendered Prompt bytes.",
        "- No model was rerun and no Prompt, policy, Gold, candidate, or upstream database was modified.", "",
    ])
    return "\n".join(lines)


def run_experiment(
    release_version: str = "pilot-v0.1.0",
    experiment_id: str = "frozen-terra-sol-v1",
    release_root: Path = DEFAULT_RELEASE_ROOT,
    candidate_root: Path = DEFAULT_CANDIDATE_ROOT,
    run_root: Path = DEFAULT_RUN_ROOT,
    experiment_root: Path = DEFAULT_EXPERIMENT_ROOT,
) -> Path:
    """Atomically publish one immutable frozen-candidate comparison experiment."""
    if not RUN_ID_PATTERN.fullmatch(experiment_id):
        raise ValueError("experiment id 只能包含小写字母、数字、点、下划线和连字符")
    release_dir = release_root / release_version
    manifest, records = _load_release(release_dir)
    providers, provider_hashes = _load_candidates("provider", records, candidate_root)
    reviewers, reviewer_hashes = _load_candidates("reviewer", records, candidate_root)
    _validate_parent_links(providers, reviewers)
    provider_report, provider_errors = _role_report("provider", records, providers)
    reviewer_report, reviewer_errors = _role_report("reviewer", records, reviewers)
    comparison = _comparison(provider_report, reviewer_report, provider_errors, reviewer_errors)
    final_dir = experiment_root / release_version / experiment_id
    if final_dir.exists():
        raise FileExistsError(f"实验 run 已存在，禁止覆盖：{final_dir}")
    temporary = final_dir.parent / f".{experiment_id}.tmp-{uuid.uuid4().hex}"
    try:
        temporary.mkdir(parents=True)
        payloads = {
            "provider-report.json": _json_bytes(provider_report),
            "provider-errors.jsonl": b"".join(_json_bytes(item) for item in provider_errors),
            "reviewer-report.json": _json_bytes(reviewer_report),
            "reviewer-errors.jsonl": b"".join(_json_bytes(item) for item in reviewer_errors),
            "comparison.json": _json_bytes(comparison),
        }
        for name, content in payloads.items():
            (temporary / name).write_bytes(content)
        (temporary / "summary.md").write_text(
            _summary_markdown(provider_report, reviewer_report, comparison), encoding="utf-8", newline="\n"
        )
        artifacts = {path.name: sha256_file(path) for path in sorted(temporary.iterdir()) if path.is_file()}
        run_manifest = {
            "schema_version": EXPERIMENT_SCHEMA,
            "release_version": release_version,
            "experiment_id": experiment_id,
            "immutable": True,
            "development_set": True,
            "release": {"manifest_sha256": sha256_file(release_dir / "manifest.json"),
                        "rich_gold_sha256": manifest["artifacts"]["rich-gold.jsonl"]},
            "models": EXPECTED_MODELS,
            "model_execution": {
                "surface": "codex_subagents",
                "sampling_parameters": "not_exposed_by_subagent_runtime",
                "fresh_calls_in_this_experiment": 0,
            },
            "candidate_sha256": {"provider": provider_hashes, "reviewer": reviewer_hashes},
            "prompt_provenance": _prompt_provenance(sorted(providers), run_root),
            "experimenter_sha256": sha256_file(Path(__file__)),
            "layered_evaluator_sha256": sha256_file(Path(__file__).with_name("layered_evaluator.py")),
            "artifacts": artifacts,
        }
        (temporary / "run-manifest.json").write_bytes(_json_bytes(run_manifest))
        for name in ("provider-report.json", "reviewer-report.json", "comparison.json", "run-manifest.json"):
            json.loads((temporary / name).read_text(encoding="utf-8"))
        for name in ("provider-errors.jsonl", "reviewer-errors.jsonl"):
            for line in (temporary / name).read_text(encoding="utf-8").splitlines():
                json.loads(line)
        if final_dir.exists():
            raise FileExistsError(f"实验 run 已存在，禁止覆盖：{final_dir}")
        os.replace(temporary, final_dir)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return final_dir


def _main(argv: list[str] | None = None) -> int:
    """Run the immutable frozen-candidate experiment from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-version", default="pilot-v0.1.0")
    parser.add_argument("--experiment-id", default="frozen-terra-sol-v1")
    parser.add_argument("--release-root", type=Path, default=DEFAULT_RELEASE_ROOT)
    parser.add_argument("--candidate-root", type=Path, default=DEFAULT_CANDIDATE_ROOT)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--experiment-root", type=Path, default=DEFAULT_EXPERIMENT_ROOT)
    args = parser.parse_args(argv)
    try:
        path = run_experiment(
            args.release_version, args.experiment_id, args.release_root,
            args.candidate_root, args.run_root, args.experiment_root,
        )
        print(json.dumps({"ok": True, "path": str(path)}, ensure_ascii=False, indent=2))
        return 0
    except (FileNotFoundError, FileExistsError, ValueError, OSError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
