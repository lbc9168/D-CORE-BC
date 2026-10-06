"""Regression tests for fail-closed province preservation during offline export regeneration."""

from __future__ import annotations

import importlib.util
import hashlib
import json
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


def test_reference_preflight_preserves_existing_offline_export(tmp_path, monkeypatch):
    output = tmp_path / "export"
    output.mkdir()
    exported = output / "bri_re_export.db"
    exported.write_bytes(b"prior reviewed export")
    monkeypatch.setattr(MODULE, "_resolve_province_map", lambda *a: pytest.fail("rebuild started"))
    with pytest.raises(ValueError, match="reference_snapshot_unavailable"):
        MODULE.regenerate(tmp_path / "source.db", output, tmp_path / "public",
                          external_references=tmp_path / "missing.json")
    assert exported.read_bytes() == b"prior reviewed export"


def test_offline_regeneration_retains_configured_reference_and_source_bytes(tmp_path):
    from datetime import datetime, timedelta, timezone
    from s02a_duplicate_merger.tools.duplicate_merger_tool import _init_db
    source = tmp_path / "main.db"
    connection = _init_db(str(source))
    with connection:
        connection.execute("INSERT INTO canonical_projects(id,project_name,country,country_iso,category) VALUES ('P','风电项目','阿根廷','ARG','风电')")
        connection.execute("INSERT INTO project_events(id,canonical_id,project_stage,capacity_mw,publish_date,source_links) VALUES ('P-01','P','投入运营',9,'2026-09-01','[]')")
    connection.close()
    output, public = tmp_path / "output", tmp_path / "public"
    output.mkdir()
    _write_export(output / "bri_re_export.db", [("P", "Reviewed province")])
    now = datetime.now(timezone.utc)
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"version": "external-references-v1", "candidates": [{
        "case_id": "reviewed", "project_id": "P", "event_id": "P-01",
        "target": {"project_name": "风电项目", "country_iso": "ARG"}, "decision": "project_reference", "evidence_language": "zh",
        "reference": {"reference_id": "reviewed", "url": "https://www.spic.com.cn/project", "title": "项目新闻", "publisher": "国家电投",
                      "source_type": "official", "scope": "project", "event_id": None, "stage": None, "stage_en": None,
                      "event_date": None, "checked_at": (now-timedelta(days=1)).isoformat(), "valid_until": (now+timedelta(days=20)).isoformat(),
                      "is_reprint": False, "original_publisher": None}}]}), encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"version": "external-reference-config-v1", "source_db": source.name, "snapshot": snapshot.name}), encoding="utf-8")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    for _ in range(2):
        report = MODULE.regenerate(source, output, public, reference_config=config)
        assert report["reference_summary"]["success"] == 1
        assert len(json.loads((public / "projects_flat.json").read_text(encoding="utf-8"))[0]["external_references"]) == 1
        assert (output / "projects_flat.json").read_bytes() == (public / "projects_flat.json").read_bytes()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
