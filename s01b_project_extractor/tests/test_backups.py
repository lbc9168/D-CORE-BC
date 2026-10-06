"""Recovery, retention and deletion boundaries for grouped S01B backups."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime

import pytest

from s01b_project_extractor import backups


def seed(path, value=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY, value INTEGER)")
        conn.execute("INSERT OR REPLACE INTO facts VALUES(1, ?)", (value,))
        conn.commit()


def setup_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(backups, "MODULE_ROOT", tmp_path)
    paths = [tmp_path / "data/projects.db", tmp_path / "data/review.db"]
    for path in paths:
        seed(path)
    return paths


def test_wal_pair_recovery_and_source_unchanged(tmp_path, monkeypatch):
    paths = setup_sources(tmp_path, monkeypatch)
    writer = sqlite3.connect(paths[0])
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE facts SET value=42")
        writer.commit()
        # SHM lock bookkeeping can change during readers; database and committed WAL cannot.
        before = {p: backups._sha256(p) for p in tmp_path.joinpath("data").glob("*.db*")
                  if not p.name.endswith("-shm")}
        folder = backups.backup_databases(paths, now=datetime(2026, 10, 5))
        for path, expected in zip(paths, (42, 1)):
            with closing(sqlite3.connect(folder / path.name)) as conn:
                assert conn.execute("SELECT value FROM facts").fetchone()[0] == expected
        assert all(backups._sha256(path) == digest for path, digest in before.items())
        assert len(list((tmp_path / "backup/monthly/2026-10").iterdir())) == 1
    finally:
        writer.close()


def test_five_runs_six_calendar_months_across_year_boundary(tmp_path, monkeypatch):
    paths = setup_sources(tmp_path, monkeypatch)
    # Monthly history starts outside the final window: Aug-Jan should remain.
    for year, month in [(2025, 6), (2025, 7), (2025, 8), (2025, 9),
                        (2025, 10), (2025, 11), (2025, 12)]:
        seed(paths[0], month)
        backups.backup_databases(paths, now=datetime(year, month, 2))
    for day in range(1, 8):
        seed(paths[0], 100 + day)
        backups.backup_databases(paths, now=datetime(2026, 1, day))
    root = tmp_path / "backup"
    recent = backups._groups(root / "recent")
    assert [g["created"].day for g in recent] == [3, 4, 5, 6, 7]
    assert sorted(p.name for p in (root / "monthly").iterdir()) == [
        "2025-08", "2025-09", "2025-10", "2025-11", "2025-12", "2026-01"]
    january = backups._groups(root / "monthly/2026-01")
    assert len(january) == 1 and january[0]["created"].day == 7
    with closing(sqlite3.connect(january[0]["path"] / "projects.db")) as conn:
        assert conn.execute("SELECT value FROM facts").fetchone() == (107,)


def test_failed_second_database_never_prunes_and_can_retry(tmp_path, monkeypatch):
    paths = setup_sources(tmp_path, monkeypatch)
    for day in range(1, 6):
        backups.backup_databases(paths, now=datetime(2026, 10, day))
    root = tmp_path / "backup"
    protected = {p: backups._sha256(p) for p in root.rglob("*.db")}
    paths[1].write_bytes(b"broken sqlite")
    with pytest.raises(sqlite3.DatabaseError):
        backups.backup_databases(paths, now=datetime(2026, 10, 6))
    assert len(backups._groups(root / "recent")) == 5
    assert all(backups._sha256(path) == digest for path, digest in protected.items())
    paths[1].unlink()
    seed(paths[1])
    backups.backup_databases(paths, now=datetime(2026, 10, 6))
    assert len(backups._groups(root / "recent")) == 5


def test_monthly_copy_failure_keeps_existing_recovery_points(tmp_path, monkeypatch):
    paths = setup_sources(tmp_path, monkeypatch)
    for day in range(1, 6):
        backups.backup_databases(paths, now=datetime(2026, 10, day))
    root = tmp_path / "backup"
    protected = {p: backups._sha256(p) for p in root.rglob("*.db")}
    copier = backups.shutil.copyfileobj
    monkeypatch.setattr(backups.shutil, "copyfileobj", lambda *args: (_ for _ in ()).throw(OSError("full disk")))
    with pytest.raises(OSError, match="full disk"):
        backups.backup_databases(paths, now=datetime(2026, 10, 6))
    assert len(backups._groups(root / "recent")) == 6
    assert all(backups._sha256(path) == digest for path, digest in protected.items())
    monkeypatch.setattr(backups.shutil, "copyfileobj", copier)
    backups.retain_backups(root, now=datetime(2026, 10, 6))
    assert len(backups._groups(root / "recent")) == 5


@pytest.mark.parametrize("damage", ["unexpected", "traversal", "hash"])
def test_invalid_owned_group_stops_cleanup(tmp_path, monkeypatch, damage):
    paths = setup_sources(tmp_path, monkeypatch)
    folder = backups.backup_databases(paths, now=datetime(2026, 10, 1))
    outside = tmp_path / "protected.db"
    seed(outside, 99)
    fingerprint = backups._sha256(outside)
    if damage == "unexpected":
        (folder / "user-note.txt").write_text("keep me", encoding="utf-8")
    elif damage == "traversal":
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        manifest["files"][0]["file"] = "../../../../protected.db"
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    else:
        seed(folder / "projects.db", 999)
    with pytest.raises(ValueError):
        backups.retain_backups(tmp_path / "backup", now=datetime(2026, 10, 2))
    assert (folder / "projects.db").exists()
    assert backups._sha256(outside) == fingerprint


def test_legacy_pair_monthly_gaps_preserved_baselines_and_idempotency(tmp_path):
    data = tmp_path / "data"
    seed(data / "projects.db", 123)
    live_hash = backups._sha256(data / "projects.db")
    for month in [4, 7, 8, 9, 10]:
        for name, micros in [("projects", "123456"), ("review", "234567")]:
            seed(data / f"{name}.db.bak.2026{month:02}01_120000_{micros}", month)
    for day in range(2, 6):
        seed(data / f"projects.db.bak.202610{day:02}_120000", day)
    special = data / "projects.db.pre_july_update_20260816.bak"
    seed(special, 88)
    special_hash = backups._sha256(special)
    (data / "All_AI_Results_20261001.json").write_text("{}")
    result = backups.organize_legacy(tmp_path, now=datetime(2026, 10, 6))
    assert result == {"organized_files": 15, "recent_runs": 5,
                      "months": ["2026-07", "2026-08", "2026-09", "2026-10"]}
    root = tmp_path / "backup"
    assert backups._sha256(root / "preserved" / special.name) == special_hash
    assert not list(data.glob("*.bak*"))
    assert backups._sha256(data / "projects.db") == live_hash
    assert (data / "All_AI_Results_20261001.json").exists()
    assert len(backups._groups(root / "monthly/2026-07")[0]["files"]) == 2
    again = backups.organize_legacy(tmp_path, now=datetime(2026, 10, 6))
    assert again["organized_files"] == 0
    assert again["months"] == result["months"]


def test_custom_sources_are_isolated_and_collision_is_refused(tmp_path, monkeypatch):
    production = tmp_path / "production"
    monkeypatch.setattr(backups, "MODULE_ROOT", production)
    isolated = tmp_path / "trial"
    paths = [isolated / "p.db", isolated / "r.db"]
    for path in paths:
        seed(path)
    folder = backups.backup_databases(paths, now=datetime(2026, 10, 5))
    assert isolated / "backup" in folder.parents
    assert not (production / "backup").exists()
    original = backups._sha256(folder / "p.db")
    with pytest.raises(FileExistsError):
        backups.backup_databases(paths, now=datetime(2026, 10, 5))
    assert backups._sha256(folder / "p.db") == original
