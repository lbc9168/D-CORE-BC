"""Regression tests for fail-closed province preservation during offline export regeneration."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "regenerate_exports_offline.py"
SPEC = importlib.util.spec_from_file_location("regenerate_exports_offline", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write_export(path: Path, rows: list[tuple[str, str | None]]) -> None:
    """Create the minimal projects_flat table needed by the province-map loader."""
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE projects_flat (project_id TEXT, province TEXT)")
        connection.executemany("INSERT INTO projects_flat VALUES (?, ?)", rows)


def test_load_province_map_rejects_all_empty_values(tmp_path: Path) -> None:
    """An all-empty prior export must fail instead of silently erasing province coverage."""
    export_db = tmp_path / "empty.db"
    _write_export(export_db, [("A", None), ("B", "null"), ("C", "N/A")])

    with pytest.raises(ValueError, match="省份映射全部为空"):
        MODULE._load_province_map(export_db)


def test_resolve_province_map_fills_from_seed_without_overwriting_current(tmp_path: Path) -> None:
    """A seed fills missing provinces while non-empty current reviewed values remain authoritative."""
    current_db = tmp_path / "current.db"
    seed_db = tmp_path / "seed.db"
    _write_export(current_db, [("A", None), ("B", "Current State")])
    _write_export(seed_db, [("A", "Seed Province"), ("B", "Seed State"), ("C", "Seed Region")])

    assert MODULE._resolve_province_map(current_db, seed_db) == {
        "A": "Seed Province",
        "B": "Current State",
        "C": "Seed Region",
    }


def test_regenerate_checks_tracked_database_state_before_export(monkeypatch, tmp_path: Path) -> None:
    """Offline regeneration verifies the tracked database state before touching export artifacts."""
    main_db = tmp_path / "main.db"
    state = tmp_path / "state.json"
    calls: list[tuple[Path, Path]] = []

    def _record_verify(database: Path, manifest: Path) -> None:
        """Record the fail-closed verification call made before export work begins."""
        calls.append((database, manifest))

    def _stop_after_guard(*_args: object, **_kwargs: object) -> dict[str, str | None]:
        """Stop the test immediately after the state guard to avoid building fixture exports."""
        raise RuntimeError("after guard")

    monkeypatch.setattr(MODULE, "verify_database", _record_verify)
    monkeypatch.setattr(MODULE, "_resolve_province_map", _stop_after_guard)

    with pytest.raises(RuntimeError, match="after guard"):
        MODULE.regenerate(main_db, tmp_path / "output", tmp_path / "public", state_manifest=state)

    assert calls == [(main_db, state)]
