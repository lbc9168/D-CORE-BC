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
