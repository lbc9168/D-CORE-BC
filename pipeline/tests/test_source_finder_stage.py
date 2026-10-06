"""Incremental reference discovery runs after normalization and before export."""
import sqlite3
import sys
from pathlib import Path
from types import ModuleType

import pytest

from bri_pipeline import flow


def flow_case(tmp_path, monkeypatch, source_result):
    raw, extracted, main = (tmp_path / name for name in ("raw.db", "extracted.db", "main.db"))
    with sqlite3.connect(raw) as conn:
        conn.execute("CREATE TABLE raw_news(id INTEGER)")
        conn.execute("INSERT INTO raw_news VALUES (1)")
    with sqlite3.connect(extracted) as conn:
        conn.execute("CREATE TABLE extracted_projects(id INTEGER)")
    with sqlite3.connect(main) as conn:
        for table in ("canonical_projects", "project_events", "chinese_entities"):
            conn.execute(f"CREATE TABLE {table}(id TEXT)")
    config = flow.PipelineConfig(
        skip_s01a=True, raw_news_db_path=str(raw), projects_db_path=str(extracted),
        review_db_path=str(tmp_path / "review.db"), db_path=str(main),
        run_entity_intake=False, run_location_normalize=False,
        run_developer_normalize=False, run_en_backfill=False,
        run_txmf_backfill=False, run_residual_translate=False,
        run_storage_subcat_backfill=False, run_energy_taxonomy_shadow=False,
        run_export=True, export_dir=str(tmp_path / "export"), export_web_dir=str(tmp_path / "web"),
    )
    calls = []
    monkeypatch.setattr(flow, "_run_s01b", lambda **kwargs: {
        "status": "completed", "projects_db_path": str(extracted), "articles": [],
    })

    class Merger:
        def _run(self, **kwargs):
            calls.append(("merge", kwargs))
            # Already-imported rows are exactly the recovery case: S02E still runs.
            self.last_result = {"status": "completed", "input_rows": 0}
            return "completed"

    monkeypatch.setattr(flow, "DuplicateMergerTool", Merger)
    monkeypatch.setattr(flow, "run_energy_taxonomy_stages", lambda *args, **kwargs: calls.append(("normalize", kwargs)))
    recurring = ModuleType("s02e_source_finder.recurring")
    recurring.run_incremental = lambda **kwargs: calls.append(("s02e", kwargs)) or source_result
    monkeypatch.setitem(sys.modules, "s02e_source_finder.recurring", recurring)
    from s03_data_exporter import main as exporter
    monkeypatch.setattr(exporter, "run", lambda **kwargs: calls.append(("export", kwargs)) or {"status": "completed"})
    return config, calls


@pytest.mark.parametrize("stage_status,overall", [
    ("completed", "completed"), ("partial", "partial"), ("awaiting_budget", "partial"),
])
def test_incremental_stage_recovers_after_zero_merge_and_exports(tmp_path, monkeypatch, stage_status, overall):
    snapshot = str(tmp_path / "references.json")
    source_result = {"status": stage_status, "reference_snapshot": snapshot}
    config, calls = flow_case(tmp_path, monkeypatch, source_result)
    result = flow.run_pipeline(config)
    assert [stage for stage, _ in calls] == ["merge", "normalize", "s02e", "export"]
    assert calls[2][1] == {"main_db": Path(config.db_path), "raw_db": Path(config.raw_news_db_path), "config_path": None}
    assert calls[3][1]["external_references"] == Path(snapshot)
    assert calls[3][1]["reference_config"] is None
    assert result["stage_reports"]["s02e"] == source_result
    assert result["status"] == overall


@pytest.mark.parametrize("explicit_field", ["export_external_references", "export_reference_config"])
def test_explicit_export_selection_precedes_incremental_snapshot(tmp_path, monkeypatch, explicit_field):
    config, calls = flow_case(tmp_path, monkeypatch, {"status": "completed", "reference_snapshot": "generated.json"})
    explicit = tmp_path / "chosen.json"
    setattr(config, explicit_field, str(explicit))
    config.source_finder_config = str(tmp_path / "source-finder.json")
    flow.run_pipeline(config)
    assert calls[2][1]["config_path"] == Path(config.source_finder_config)
    export = calls[3][1]
    assert export["external_references"] == (explicit if explicit_field == "export_external_references" else None)
    assert export["reference_config"] == (explicit if explicit_field == "export_reference_config" else None)


def test_disabled_source_finder_preserves_export_default(tmp_path, monkeypatch):
    config, calls = flow_case(tmp_path, monkeypatch, {"status": "completed"})
    config.run_source_finder = False
    result = flow.run_pipeline(config)
    assert [stage for stage, _ in calls] == ["merge", "normalize", "export"]
    assert "s02e" not in result["stage_reports"]
    assert calls[-1][1]["external_references"] is None
    assert calls[-1][1]["reference_config"] is None


def test_missing_incremental_snapshot_preserves_export_default(tmp_path, monkeypatch):
    config, calls = flow_case(tmp_path, monkeypatch, {"status": "awaiting_budget", "reference_snapshot": None})
    result = flow.run_pipeline(config)
    assert result["status"] == "partial"
    assert calls[-1][1]["external_references"] is None


def test_invalid_stage_contract_is_partial_and_export_continues(tmp_path, monkeypatch):
    config, calls = flow_case(tmp_path, monkeypatch, {"status": "running"})
    result = flow.run_pipeline(config)
    assert result["status"] == "partial"
    assert result["stage_reports"]["s02e"]["reason"] == "source_finder_failed"
    assert calls[-1][0] == "export"


@pytest.mark.parametrize("error", [FileNotFoundError("config missing"), RuntimeError("service unavailable"), sqlite3.OperationalError("database locked")])
def test_source_finder_failure_keeps_export_and_reports_partial(tmp_path, monkeypatch, error):
    config, calls = flow_case(tmp_path, monkeypatch, None)
    def fail(**kwargs):
        raise error
    monkeypatch.setattr(sys.modules["s02e_source_finder.recurring"], "run_incremental", fail)
    result = flow.run_pipeline(config)
    assert result["status"] == "partial"
    assert result["stage_reports"]["s02e"]["error_type"] == type(error).__name__
    assert calls[-1][0] == "export"
    assert calls[-1][1]["external_references"] is None
