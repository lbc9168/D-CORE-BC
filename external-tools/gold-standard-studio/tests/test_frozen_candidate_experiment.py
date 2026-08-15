"""Verify immutable, fail-closed Terra Provider and Sol Reviewer development-set experiments."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from gold_standard_studio.frozen_candidate_experiment import (
    _candidate_status,
    adapt_project,
    run_experiment,
)


def test_candidate_adapter_unwraps_entities_and_status_aliases() -> None:
    """Map nested model envelopes and the production no-Chinese label without taxonomy invention."""
    project = {
        "project_name": {"value_state": "explicit", "value": "甲项目"},
        "chinese_entities": [{
            "entity": {"value_state": "explicit", "value": "中国公司"},
            "role": {"value_state": "explicit", "value": "EPC总承包"},
        }],
        "category": {"value_state": "derived_from_explicit", "value": "光伏"},
        "subcategory_raw_fact": {"value_state": "explicit", "value": "光储项目"},
    }
    adapted = adapt_project(project)
    assert adapted["project_name"] == "甲项目"
    assert adapted["entities"] == [{"name": "中国公司", "role": "EPC总承包"}]
    assert adapted["candidate_category"] == "光伏"
    assert adapted["subcategory_raw_fact"] == "光储项目"
    assert _candidate_status({"article_status": {"value": "no_chinese_participation"}}) == "no_chinese"


def test_real_frozen_experiment_is_reproducible_and_immutable(tmp_path: Path) -> None:
    """Run both real frozen candidate sets twice and refuse reuse of an experiment id."""
    tool_root = Path(__file__).resolve().parents[1]
    arguments = (
        "pilot-v0.1.0", "frozen-terra-sol-v1", tool_root / "data" / "releases",
        tool_root / "data" / "candidates", tool_root / "data" / "runs",
    )
    first = run_experiment(*arguments, tmp_path / "experiments-a")
    second = run_experiment(*arguments, tmp_path / "experiments-b")
    assert (first / "provider-report.json").read_bytes() == (second / "provider-report.json").read_bytes()
    assert (first / "reviewer-report.json").read_bytes() == (second / "reviewer-report.json").read_bytes()
    provider = json.loads((first / "provider-report.json").read_text(encoding="utf-8"))
    comparison = json.loads((first / "comparison.json").read_text(encoding="utf-8"))
    manifest = json.loads((first / "run-manifest.json").read_text(encoding="utf-8"))
    assert provider["gate"]["four_class"]["accuracy"] == 1.0
    assert provider["s01b"]["projects"]["f1"] == 1.0
    assert comparison["direction"] == "reviewer_minus_provider"
    assert manifest["development_set"] is True
    assert manifest["model_execution"]["surface"] == "codex_subagents"
    assert manifest["model_execution"]["fresh_calls_in_this_experiment"] == 0
    assert manifest["prompt_provenance"]["9027"]["grade"] == "candidate_source_paths_only"
    with pytest.raises(FileExistsError, match="禁止覆盖"):
        run_experiment(*arguments, tmp_path / "experiments-a")


def test_frozen_experiment_rejects_wrong_model(tmp_path: Path) -> None:
    """Fail closed when a frozen candidate no longer has the contracted model identity."""
    tool_root = Path(__file__).resolve().parents[1]
    candidates = tmp_path / "candidates"
    shutil.copytree(tool_root / "data" / "candidates", candidates)
    path = candidates / "terra-provider-1586.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["model"] = "unexpected-model"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="model 不匹配"):
        run_experiment(
            "pilot-v0.1.0", "bad-model", tool_root / "data" / "releases",
            candidates, tool_root / "data" / "runs", tmp_path / "experiments",
        )
