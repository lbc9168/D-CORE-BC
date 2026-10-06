"""Create a metadata-only s03 handoff from explicitly reviewed, already paid results."""

import argparse
import json
from pathlib import Path

from .contracts import EventContext
from .pilot import assert_sources, private_path
from .sample import file_hash
from .source_loop import write_frozen


def export_references(inputs_path: Path, reviewed_path: Path, output: Path) -> dict:
    """Bind reviewed candidates to frozen project identities without fetching or extracting."""
    inputs_path = private_path(inputs_path, existing=True)
    reviewed_path = private_path(reviewed_path, existing=True)
    output = private_path(output)
    inputs = json.loads(inputs_path.read_text(encoding="utf-8"))
    reviewed = json.loads(reviewed_path.read_text(encoding="utf-8"))
    contexts = {key: EventContext.model_validate(value) for key, value in inputs["contexts"].items()}
    assert_sources(inputs["source_hashes"])
    rows = reviewed.get("rows")
    if not isinstance(rows, list):
        raise ValueError("reviewed_candidates_missing")
    candidates = []
    for index, row in enumerate(rows):
        # A malformed review row is a local failure, not a reason to discard other reviews.
        if not isinstance(row, dict) or not isinstance(row.get("event_id"), str) or row["event_id"] not in contexts:
            candidates.append({"case_id": str(index), "decision": "pending", "reason": "review_target_unknown"})
            continue
        context = contexts[row["event_id"]]
        decision = row.get("decision")
        candidates.append({
            "case_id": row.get("case_id", str(index)),
            "project_id": context.project_id,
            "event_id": context.event_id,
            "target": {"project_name": context.project.get("project_name"), "country_iso": context.project.get("country_iso")},
            "decision": decision,
            "evidence_language": row.get("evidence_language", row.get("extraction", {}).get("language", "zh")),
            "reason": row.get("reason", ""),
            "reference": row.get("external_reference") if decision in ("event_reference", "project_reference") else None,
        })
    result = {
        "version": "external-references-v1",
        "provenance": {"inputs_sha256": file_hash(inputs_path), "review_sha256": file_hash(reviewed_path), "source_hashes": inputs["source_hashes"]},
        "candidates": candidates,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    write_frozen(output, result)
    return {"output": str(output), "candidates": len(candidates), "external_calls": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--reviewed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export_references(args.inputs, args.reviewed, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
