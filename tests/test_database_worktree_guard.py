"""Tests for read-only database identity, including allocation sequences and schema drift."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts.database_worktree_guard import (
    audit_worktree_database,
    inspect_database,
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



def test_sequence_drift_is_detected_without_touching_database(tmp_path: Path) -> None:
    database = tmp_path / "source.db"
    _create_database(database, [("A", "光伏")])
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE allocation_test(id INTEGER PRIMARY KEY AUTOINCREMENT)")
        connection.execute("INSERT INTO allocation_test DEFAULT VALUES")
        connection.execute("DELETE FROM allocation_test")
    before_bytes = database.read_bytes()
    first = inspect_database(database)
    assert database.read_bytes() == before_bytes
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE sqlite_sequence SET seq=20 WHERE name='allocation_test'")
    second_bytes = database.read_bytes()
    second = inspect_database(database)
    assert database.read_bytes() == second_bytes
    assert first["counts"] == second["counts"]
    assert first["logical_sha256"] != second["logical_sha256"]


def test_schema_drift_and_missing_database_fail_closed(tmp_path: Path) -> None:
    database = tmp_path / "source.db"
    _create_database(database, [("A", "光伏")])
    before = inspect_database(database)["logical_sha256"]
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE chinese_entities ADD COLUMN publication_status TEXT")
    assert inspect_database(database)["logical_sha256"] != before
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        inspect_database(missing)
    assert not missing.exists()


def test_inspection_connection_is_read_only_and_holds_one_snapshot(tmp_path: Path) -> None:
    from scripts.database_worktree_guard import _open_readonly

    database = tmp_path / "source.db"
    _create_database(database, [("A", "光伏")])
    with sqlite3.connect(database) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        reader = _open_readonly(database)
        try:
            assert reader.in_transaction
            assert reader.execute("SELECT category FROM canonical_projects").fetchone()[0] == "光伏"
            writer.execute("UPDATE canonical_projects SET category='风电'")
            writer.commit()
            assert reader.execute("SELECT category FROM canonical_projects").fetchone()[0] == "光伏"
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                reader.execute("DELETE FROM canonical_projects")
        finally:
            reader.close()
