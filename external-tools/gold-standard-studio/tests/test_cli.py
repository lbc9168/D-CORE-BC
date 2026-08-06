"""Exercise safe local CLI orchestration without calling external models."""

from __future__ import annotations

import hashlib
import json
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


def test_init_rejects_an_unusable_source_contract_without_creating_studio_state(tmp_path: Path) -> None:
    """Validate actual SQLite tables and columns before init can create local configuration or review state."""
    from gold_standard_studio.cli import app

    sources = _create_sources(tmp_path)
    with sqlite3.connect(sources[1]) as conn:
        conn.execute("DROP TABLE extracted_projects")
        conn.execute("CREATE TABLE extracted_projects (id INTEGER PRIMARY KEY, source_link TEXT)")
    data_dir = tmp_path / "studio"

    result = CliRunner().invoke(app, ["init", *_args(sources, data_dir)])

    assert result.exit_code != 0
    assert "contract" in result.output.lower()
    assert not data_dir.exists()


def test_init_rejects_corrupt_sqlite_without_creating_studio_state(tmp_path: Path) -> None:
    """Fail closed when a configured source is not queryable SQLite rather than leaving partial Studio state."""
    from gold_standard_studio.cli import app

    sources = _create_sources(tmp_path)
    sources[0].write_bytes(b"not a sqlite database")
    data_dir = tmp_path / "studio"

    result = CliRunner().invoke(app, ["init", *_args(sources, data_dir)])

    assert result.exit_code != 0
    assert "sqlite" in result.output.lower() or "contract" in result.output.lower()
    assert not data_dir.exists()


def test_sample_json_dry_run_emits_source_integrity_and_selection_evidence(tmp_path: Path) -> None:
    """Make source hash, row, ID, quota, and unchanged evidence machine-readable without Studio writes."""
    from gold_standard_studio.cli import app

    data_dir = tmp_path / "studio"
    result = CliRunner().invoke(app, ["sample", *_args(_create_sources(tmp_path), data_dir), "--format", "json"])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert payload["selection"]["selected_count"] == 60
    assert payload["applied"] is False
    assert payload["selection"]["negative_count"] == 11
    assert payload["selection"]["category_floors"]["储能"]["satisfied"] is True
    assert payload["source_before"]["logical_hashes"] == payload["source_after"]["logical_hashes"]
    assert payload["unchanged"]["logical_hashes"] is True
    assert payload["source_after"]["raw_news"]["row_count"] == 80
    assert not data_dir.exists()


def test_sample_json_apply_emits_one_document_with_seeded_local_state(tmp_path: Path) -> None:
    """Keep apply-mode stdout machine-parseable while reporting exactly what local review state was seeded."""
    from gold_standard_studio.cli import app
    from gold_standard_studio.review_store import ReviewStore

    data_dir = tmp_path / "studio"
    result = CliRunner().invoke(app, ["sample", *_args(_create_sources(tmp_path), data_dir), "--format", "json", "--apply"])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert payload["applied"] is True
    assert payload["seeded_count"] == 60
    assert payload["review_db"] == "review.db"
    assert len(result.stdout.splitlines()) == 1
    assert len(ReviewStore(data_dir / "review.db").list_all_samples()) == 60


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


def test_build_all_and_resume_include_reviewed_samples_using_bundle_presence_only(tmp_path: Path) -> None:
    """Build every seeded record and skip only stored bundles under resume, even after all articles are reviewed."""
    from gold_standard_studio.cli import app
    from gold_standard_studio.review_store import ReviewStore
    from gold_standard_studio.schemas import CandidateBundle, CandidateValue

    sources = _create_sources(tmp_path)
    data_dir = tmp_path / "studio"
    runner = CliRunner()
    assert runner.invoke(app, ["init", *_args(sources, data_dir)]).exit_code == 0
    assert runner.invoke(app, ["sample", "--apply", "--data-dir", str(data_dir)]).exit_code == 0
    store = ReviewStore(data_dir / "review.db")
    for item in store.list_all_samples():
        store.confirm_negative(item.sample_id, "irrelevant", actor="fixture")
    first = store.list_all_samples()[0]
    store.save_candidate_bundle(CandidateBundle(sample_id=first.sample_id, article_sha256=store.get_article(first.sample_id).article_sha256, sources={"database": CandidateValue(source_name="database")}))

    resume_result = runner.invoke(app, ["build-candidates", "--all", "--resume", "--yes", "--data-dir", str(data_dir)])
    all_result = runner.invoke(app, ["build-candidates", "--all", "--yes", "--data-dir", str(data_dir)])

    assert resume_result.exit_code == 0, resume_result.output
    assert "candidate scope count=59" in resume_result.stdout
    assert all_result.exit_code == 0, all_result.output
    assert "candidate scope count=60" in all_result.stdout


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


def test_report_retains_status_split_and_risk_totals_after_every_sample_is_reviewed(tmp_path: Path) -> None:
    """Report the completed Pilot from all samples rather than accidentally hiding reviewed records."""
    from gold_standard_studio.cli import app
    from gold_standard_studio.review_store import ReviewStore
    from gold_standard_studio.schemas import DifficultyScore

    sources = _create_sources(tmp_path)
    data_dir = tmp_path / "studio"
    runner = CliRunner()
    assert runner.invoke(app, ["init", *_args(sources, data_dir)]).exit_code == 0
    assert runner.invoke(app, ["sample", "--apply", "--data-dir", str(data_dir)]).exit_code == 0
    store = ReviewStore(data_dir / "review.db")
    for item in store.list_all_samples():
        store.confirm_negative(item.sample_id, "irrelevant", actor="fixture")
    store.update_sample_scoring("raw-1", DifficultyScore(score=10, risk_tags=["storage_context"]))
    output = tmp_path / "completed.html"

    result = runner.invoke(app, ["report", "--data-dir", str(data_dir), "--out", str(output)])

    contents = output.read_text(encoding="utf-8")
    assert result.exit_code == 0, result.output
    assert "reviewed" in contents and ">60<" in contents
    assert "development" in contents and "storage_context" in contents
