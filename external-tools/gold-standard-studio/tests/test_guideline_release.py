"""Verify the approved guideline audit, immutable v0.1.1 release, and frozen rerun attribution."""

from __future__ import annotations

import json
from pathlib import Path

from gold_standard_studio.pilot_release import sha256_file


def _jsonl_by_article(path: Path) -> dict[int, dict]:
    """Load one rich Gold JSONL artifact keyed by article ID."""
    return {int(row["article_id"]): row for row in (
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    )}


def test_guideline_audit_changes_only_the_nine_approved_fields() -> None:
    """Match every v0.1.1 rich-field change to the immutable guideline audit record."""
    tool_root = Path(__file__).resolve().parents[1]
    old_root = tool_root / "data" / "releases" / "pilot-v0.1.0"
    new_root = tool_root / "data" / "releases" / "pilot-v0.1.1"
    audit_root = tool_root / "data" / "audits" / "pilot-v0.1.1-guideline-audit"
    audit = json.loads((audit_root / "audit-report.json").read_text(encoding="utf-8"))
    assert len(audit["changes"]) == 9
    assert audit["kept_after_audit"] == [{
        "article_id": 1533,
        "evidence": "三一硅能参建达尔汗5MW+4MWh光储电站",
        "field": "project_stage",
        "gold_project_id": "P07",
        "project_index": 7,
        "rule": "participation_in_construction_supports_under_construction",
        "value": "在建",
    }]
    old, new = _jsonl_by_article(old_root / "rich-gold.jsonl"), _jsonl_by_article(new_root / "rich-gold.jsonl")
    expected = {(item["article_id"], item["project_index"], item["field"]) for item in audit["changes"]}
    actual: set[tuple[int, int, str]] = set()
    for article_id in old:
        assert len(old[article_id]["projects"]) == len(new[article_id]["projects"])
        for project_index, (before, after) in enumerate(zip(old[article_id]["projects"], new[article_id]["projects"]), 1):
            for field_name in before["fields"]:
                if before["fields"][field_name] != after["fields"][field_name]:
                    actual.add((article_id, project_index, field_name))
    assert actual == expected


def test_v011_release_and_frozen_experiment_are_hashed_and_attributable() -> None:
    """Verify release artifacts and prove that only Gold, not candidate bytes, changed between experiments."""
    tool_root = Path(__file__).resolve().parents[1]
    release = tool_root / "data" / "releases" / "pilot-v0.1.1"
    manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["release_version"] == "pilot-v0.1.1"
    assert manifest["article_count"] == 10
    assert manifest["project_count"] == 14
    for relative, expected in manifest["artifacts"].items():
        assert sha256_file(release / relative) == expected

    experiment_root = tool_root / "data" / "experiments"
    old = experiment_root / "pilot-v0.1.0" / "frozen-terra-sol-v1"
    new = experiment_root / "pilot-v0.1.1" / "frozen-terra-sol-v1"
    old_manifest = json.loads((old / "run-manifest.json").read_text(encoding="utf-8"))
    new_manifest = json.loads((new / "run-manifest.json").read_text(encoding="utf-8"))
    assert old_manifest["candidate_sha256"] == new_manifest["candidate_sha256"]
    assert old_manifest["prompt_provenance"] == new_manifest["prompt_provenance"]
    provider = json.loads((new / "provider-report.json").read_text(encoding="utf-8"))
    reviewer = json.loads((new / "reviewer-report.json").read_text(encoding="utf-8"))
    comparison = json.loads((new / "comparison.json").read_text(encoding="utf-8"))
    assert provider["s01b"]["fields"]["micro_accuracy"] < reviewer["s01b"]["fields"]["micro_accuracy"]
    assert comparison["mismatch_transitions"]["introduced_count"] == 1
    introduced = comparison["mismatch_transitions"]["introduced_by_reviewer"]
    assert [(item["article_id"], item["gold_project_id"], item["field"]) for item in introduced] == [
        (1533, "P07", "project_stage")
    ]
