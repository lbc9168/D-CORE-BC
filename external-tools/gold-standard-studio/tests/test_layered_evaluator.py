"""Verify typed, reproducible, capability-honest Pilot evaluation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gold_standard_studio.layered_evaluator import align_projects, compare_field, run_evaluation


def _envelope(value, state: str = "present") -> dict:
    """Build the minimal Gold envelope accepted by one field comparator."""
    return {"value": value, "value_state": state}


def test_typed_comparators_distinguish_correct_wrong_and_unscorable() -> None:
    """Compare numbers, absence, sets, entities, investments, and non-normalized Gold values by meaning."""
    assert compare_field("storage_energy_mwh", _envelope("300"), 300.0)["correct"] is True
    assert compare_field("storage_energy_mwh", _envelope("300"), 305.0)["correct"] is False
    assert compare_field("financing_mode", _envelope(None, "not_mentioned"), "Unknown")["correct"] is True
    assert compare_field("generation_sources", _envelope(["光伏", "风电"]), ["风电", "光伏"])["correct"] is True
    assert compare_field(
        "entities", _envelope([{"name": "中企甲", "role": "EPC"}]),
        [{"role": "EPC", "name": "中企甲"}],
    )["correct"] is True
    assert compare_field("planned_start_date", _envelope(None), None) == {
        "scorable": False, "correct": None, "reason": "gold_present_without_normalized_value",
    }
    investment = compare_field(
        "investment", _envelope({"contract_amount_million_usd": 42.94}),
        {"contract_amount_million_usd": 42.94},
    )
    assert investment["correct"] is True


def test_project_alignment_is_one_to_one_and_reports_extras() -> None:
    """Align each Gold project at most once and leave an extra stored prediction unmatched."""
    gold = [
        {"fields": {"project_name": _envelope("甲国100MW光伏项目"), "country": _envelope("甲国"),
                     "location": _envelope("甲省")}},
        {"fields": {"project_name": _envelope("甲国200MW风电项目"), "country": _envelope("甲国"),
                     "location": _envelope("乙省")}},
    ]
    predicted = [
        {"project_name": "甲国100MW光伏项目", "country": "甲国", "location": "甲省"},
        {"project_name": "甲国200MW风电项目", "country": "甲国", "location": "乙省"},
        {"project_name": "完全多余项目", "country": "甲国", "location": "丙省"},
    ]
    result = align_projects(gold, predicted)
    assert len(result["matches"]) == 2
    assert result["unmatched_gold"] == []
    assert result["unmatched_predicted"] == [2]


def test_real_release_evaluation_is_reproducible_and_immutable(tmp_path: Path) -> None:
    """Evaluate the real frozen Pilot twice into separate roots and refuse run-id reuse."""
    tool_root = Path(__file__).resolve().parents[1]
    release_root = tool_root / "data" / "releases"
    first_root = tmp_path / "eval-a"
    second_root = tmp_path / "eval-b"
    first = run_evaluation("pilot-v0.1.0", "stored-snapshot-v2", release_root, first_root)
    second = run_evaluation("pilot-v0.1.0", "stored-snapshot-v2", release_root, second_root)
    assert (first / "report.json").read_bytes() == (second / "report.json").read_bytes()
    report = json.loads((first / "report.json").read_text(encoding="utf-8"))
    assert report["capabilities"]["s02a_cross_article_duplicate_merger"].startswith("unsupported")
    assert report["capabilities"]["s03_admin1"].startswith("unsupported")
    assert report["s01b"]["fields"]["unscorable"] > 0
    with pytest.raises(FileExistsError, match="禁止覆盖"):
        run_evaluation("pilot-v0.1.0", "stored-snapshot-v2", release_root, first_root)
