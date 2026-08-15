"""Tests for logical database identity, verification, audit, and safe promotion."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts.database_worktree_guard import (
    audit_worktree_database,
    inspect_database,
    promote_database,
    state_from_report,
    verify_database,
)


def _create_database(path: Path, rows: list[tuple[str, str]]) -> None:
    """Create a minimal database satisfying the guard's core schema contract."""
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE canonical_projects (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                technology_tags_json TEXT NOT NULL DEFAULT '[]'
            );
            CREATE TABLE project_events (
                id TEXT PRIMARY KEY,
                canonical_id TEXT NOT NULL REFERENCES canonical_projects(id),
                generation_sources_json TEXT,
                storage_status TEXT,
                storage_power_mw REAL,
                storage_energy_mwh REAL
            );
            CREATE TABLE chinese_entities (id INTEGER PRIMARY KEY, canonical_id TEXT);
            CREATE TABLE project_classification_shadow (
                canonical_id TEXT PRIMARY KEY REFERENCES canonical_projects(id),
                technology_tags_json TEXT NOT NULL DEFAULT '[]',
                classification_version TEXT NOT NULL,
                needs_review INTEGER NOT NULL
            );
            """
        )
        for project_id, category in rows:
            connection.execute(
                "INSERT INTO canonical_projects(id,category) VALUES (?,?)", (project_id, category)
            )
            connection.execute(
                "INSERT INTO project_events(id,canonical_id,storage_status) VALUES (?,?,?)",
                (f"{project_id}-01", project_id, "not_mentioned"),
            )
            connection.execute(
                "INSERT INTO project_classification_shadow VALUES (?,?,?,?)",
                (project_id, "[]", "energy_taxonomy_v2.1", 0),
            )


def test_logical_fingerprint_ignores_insertion_order(tmp_path: Path) -> None:
    """Equivalent schemas and rows receive one logical identity despite different insertion order."""
    first = tmp_path / "first.db"
    second = tmp_path / "second.db"
    _create_database(first, [("B", "风电"), ("A", "光伏")])
    _create_database(second, [("A", "光伏"), ("B", "风电")])

    assert inspect_database(first)["logical_sha256"] == inspect_database(second)["logical_sha256"]


def test_verify_and_audit_detect_logical_drift(tmp_path: Path) -> None:
    """Tracked state verification and worktree audit both reject changed logical data."""
    stable = tmp_path / "stable.db"
    candidate = tmp_path / "candidate.db"
    manifest_path = tmp_path / "database_state.json"
    _create_database(stable, [("A", "光伏")])
    _create_database(candidate, [("A", "风电")])
    report = inspect_database(stable)
    manifest_path.write_text(
        json.dumps(state_from_report(report, "data/stable.db"), ensure_ascii=False), encoding="utf-8"
    )

    verify_database(stable, manifest_path)
    assert audit_worktree_database(candidate, stable)["same_logical_database"] is False
    with pytest.raises(RuntimeError, match="mismatches"):
        verify_database(candidate, manifest_path)


def test_promote_creates_backup_and_replaces_stable_database(tmp_path: Path) -> None:
    """Promotion preserves a verified backup and makes stable logically equal to candidate."""
    stable = tmp_path / "stable.db"
    candidate = tmp_path / "candidate.db"
    _create_database(stable, [("A", "光伏")])
    _create_database(candidate, [("A", "风电"), ("B", "光伏")])
    candidate_report = inspect_database(candidate)

    result = promote_database(candidate, stable, candidate_report["logical_sha256"])

    assert Path(result["backup"]).is_file()
    assert Path(result["receipt"]).is_file()
    assert inspect_database(stable)["logical_sha256"] == candidate_report["logical_sha256"]
    assert inspect_database(Path(result["backup"]))["counts"]["canonical_projects"] == 1


def test_promote_rejects_wrong_confirmation_without_writing(tmp_path: Path) -> None:
    """An incorrect confirmation fails before a backup or stable write occurs."""
    stable = tmp_path / "stable.db"
    candidate = tmp_path / "candidate.db"
    _create_database(stable, [("A", "光伏")])
    _create_database(candidate, [("A", "风电")])
    before = inspect_database(stable)["logical_sha256"]

    with pytest.raises(ValueError, match="confirmation"):
        promote_database(candidate, stable, "0" * 64)

    assert inspect_database(stable)["logical_sha256"] == before
    assert not list(tmp_path.glob("*.pre-promotion.*"))


def test_promote_rejects_invalid_technology_tag_assignment(tmp_path: Path) -> None:
    """Promotion rejects technology tags outside the controlled parent-category contract."""
    stable = tmp_path / "stable.db"
    candidate = tmp_path / "candidate.db"
    _create_database(stable, [("A", "光伏")])
    _create_database(candidate, [("A", "光伏")])
    with sqlite3.connect(candidate) as connection:
        connection.execute(
            "UPDATE canonical_projects SET technology_tags_json=? WHERE id=?",
            ('["风光"]', "A"),
        )
    candidate_report = inspect_database(candidate)

    with pytest.raises(RuntimeError, match="invalid_tag_rows"):
        promote_database(candidate, stable, candidate_report["logical_sha256"])

    assert inspect_database(stable)["taxonomy"]["tagged_projects"] == 0
