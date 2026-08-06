"""Exercise safe local CLI orchestration without calling external models."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from typer.testing import CliRunner


def _create_sources(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create a quota-feasible three-database fixture that uses real SQLite source shapes."""
    raw_db, projects_db, main_db = (tmp_path / name for name in ("raw_news.db", "projects.db", "main.db"))
    rare_categories = ["储能"] * 3 + ["制造业"] * 3 + ["输变电建设"] * 3 + ["核电"] * 2 + ["火电"] * 2 + ["地热及余热"] * 2 + ["生物质"] * 2
    positive_categories = rare_categories + ["光伏"] * 52
    with sqlite3.connect(raw_db) as conn:
        conn.execute("CREATE TABLE raw_news (id INTEGER PRIMARY KEY, url TEXT UNIQUE, title TEXT, content TEXT, publish_date TEXT)")
        rows = [
            (index, f"https://example.test/{index}", f"Article {index}", f"China builds a {index + 10} MW project.", "2026-08-01")
            for index in range(1, 81)
        ]
        conn.executemany("INSERT INTO raw_news VALUES (?, ?, ?, ?, ?)", rows)
    with sqlite3.connect(projects_db) as conn:
        conn.execute("CREATE TABLE extracted_projects (id INTEGER PRIMARY KEY, source_link TEXT, imported_to_s02a INTEGER, category TEXT)")
        conn.executemany(
            "INSERT INTO extracted_projects VALUES (?, ?, 0, ?)",
            [(index, f"https://example.test/{index}", category) for index, category in enumerate(positive_categories, start=1)],
        )
    with sqlite3.connect(main_db) as conn:
        conn.execute("CREATE TABLE canonical_projects (id TEXT PRIMARY KEY, project_name TEXT, category TEXT)")
        conn.execute("CREATE TABLE project_events (id TEXT PRIMARY KEY, canonical_id TEXT, source_links TEXT)")
        conn.execute("CREATE TABLE chinese_entities (id INTEGER PRIMARY KEY, event_id TEXT)")
    return raw_db, projects_db, main_db


def _args(paths: tuple[Path, Path, Path], data_dir: Path) -> list[str]:
    """Return one explicit source configuration argument sequence for CLI tests."""
    raw_db, projects_db, main_db = paths
    return [
        "--raw-news-db", str(raw_db), "--projects-db", str(projects_db), "--main-db", str(main_db), "--data-dir", str(data_dir),
    ]


def test_sample_defaults_to_dry_run(tmp_path: Path) -> None:
    """Keep source inspection and quota selection dry until the operator explicitly requests apply."""
    from gold_standard_studio.cli import app

    data_dir = tmp_path / "studio"
    result = CliRunner().invoke(app, ["sample", *_args(_create_sources(tmp_path), data_dir)])

    assert result.exit_code == 0, result.stdout
    assert "dry-run" in result.stdout.lower()
    assert "selected=60" in result.stdout
    assert "statuses=" in result.stdout
    assert not (data_dir / "review.db").exists()


def test_sampling_coverage_explicitly_maps_current_new_energy_manufacturing_category() -> None:
    """Treat the production taxonomy's 新能源制造业 label as the immutable Pilot manufacturing stratum."""
    from gold_standard_studio.cli import _coverage_category_tags

    assert _coverage_category_tags(("新能源制造业", "光伏")) == ("光伏", "制造业", "新能源制造业")


def test_init_persists_explicit_paths_and_sample_apply_seeds_only_review_db(tmp_path: Path) -> None:
    """Persist only local Studio configuration and review state after an explicitly applied sample run."""
    from gold_standard_studio.cli import app

    sources = _create_sources(tmp_path)
    source_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    data_dir = tmp_path / "studio"
    runner = CliRunner()

    initialized = runner.invoke(app, ["init", *_args(sources, data_dir)])
    applied = runner.invoke(app, ["sample", "--apply", "--data-dir", str(data_dir)])

    assert initialized.exit_code == 0, initialized.stdout
    assert applied.exit_code == 0, applied.stdout
    assert (data_dir / "studio-config.json").is_file()
    assert (data_dir / "review.db").is_file()
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources} == source_hashes


def test_publish_requires_explicit_version(tmp_path: Path) -> None:
    """Prevent ambiguous or accidental formal publication without a semantic version request."""
    from gold_standard_studio.cli import app

    result = CliRunner().invoke(app, ["publish", "--data-dir", str(tmp_path / "studio")])

    assert result.exit_code != 0
    assert "--version" in result.output


def test_build_all_requires_confirmation_and_reports_exact_resume_count(tmp_path: Path) -> None:
    """Block cost-bearing all-sample candidate work until the operator confirms the exact scope."""
    from gold_standard_studio.cli import app

    sources = _create_sources(tmp_path)
    data_dir = tmp_path / "studio"
    runner = CliRunner()
    assert runner.invoke(app, ["init", *_args(sources, data_dir)]).exit_code == 0
    assert runner.invoke(app, ["sample", "--apply", "--data-dir", str(data_dir)]).exit_code == 0

    blocked = runner.invoke(app, ["build-candidates", "--all", "--data-dir", str(data_dir)], input="n\n")
    completed = runner.invoke(app, ["build-candidates", "--all", "--yes", "--data-dir", str(data_dir)])
    resumed = runner.invoke(app, ["build-candidates", "--all", "--resume", "--yes", "--data-dir", str(data_dir)])

    assert blocked.exit_code != 0
    assert "60" in blocked.stdout
    assert completed.exit_code == 0, completed.stdout
    assert "built=60" in completed.stdout
    assert resumed.exit_code == 0, resumed.stdout
    assert "remaining=0" in resumed.stdout


def test_report_writes_accessible_self_contained_html(tmp_path: Path) -> None:
    """Produce a local review summary with no network dependencies after the review database exists."""
    from gold_standard_studio.cli import app

    sources = _create_sources(tmp_path)
    data_dir = tmp_path / "studio"
    runner = CliRunner()
    assert runner.invoke(app, ["init", *_args(sources, data_dir)]).exit_code == 0
    assert runner.invoke(app, ["sample", "--apply", "--data-dir", str(data_dir)]).exit_code == 0
    output = tmp_path / "pilot.html"

    result = runner.invoke(app, ["report", "--data-dir", str(data_dir), "--out", str(output)])

    contents = output.read_text(encoding="utf-8")
    assert result.exit_code == 0, result.stdout
    assert "<main" in contents and "<table" in contents and "<caption" in contents
    assert "https://" not in contents
