"""Prove the complete local Pilot workflow preserves every source database exactly."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from test_cli import _args, _create_sources


@dataclass
class _FakeRunner:
    """Return an in-memory successful candidate without a provider, credential, or network call."""

    source_name: str
    version: str = "fake-v1"
    prompt_hash: str = ""
    parser_version: str = ""

    def run(self, _request):
        """Return an empty but valid candidate response from the requested independent source."""
        from gold_standard_studio.schemas import CandidateValue

        return CandidateValue(source_name=self.source_name, source_version=self.version)


def _source_shape(paths: tuple[Path, Path, Path]) -> dict[str, object]:
    """Capture physical hashes plus all table row counts and primary ID sets for mutation detection."""
    shape: dict[str, object] = {}
    for path in paths:
        with sqlite3.connect(path) as conn:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            shape[path.name] = {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "rows": {table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in tables},
                "ids": {
                    table: [row[0] for row in conn.execute(f'SELECT id FROM "{table}" ORDER BY id')]
                    for table in tables if any(column[1] == "id" for column in conn.execute(f'PRAGMA table_info("{table}")'))
                },
            }
    return shape


def test_pilot_flow_with_fake_runners_never_mutates_sources(tmp_path: Path, monkeypatch) -> None:
    """Seed, build, confirm, publish, and report a Pilot while every external candidate runner is fake."""
    from gold_standard_studio import cli
    from gold_standard_studio.candidate_builder import CandidateBuilder
    from gold_standard_studio.candidate_cache import CandidateCache
    from gold_standard_studio.review_store import ReviewStore

    sources = _create_sources(tmp_path)
    before = _source_shape(sources)
    data_dir = tmp_path / "studio"

    def fake_builder(repository, configured_data_dir):
        """Build the real orchestration layer around deterministic source-local fake runners."""
        return CandidateBuilder(
            current_labels=repository.load_current_labels,
            production_runner=_FakeRunner("production"), review_runner=_FakeRunner("review"),
            deterministic_runner=_FakeRunner("deterministic"), cache=CandidateCache(configured_data_dir / "model_cache"),
        )

    monkeypatch.setattr(cli, "_build_candidate_builder", fake_builder)
    runner = CliRunner()
    assert runner.invoke(cli.app, ["init", *_args(sources, data_dir)]).exit_code == 0
    seeded = runner.invoke(cli.app, ["sample", "--apply", "--data-dir", str(data_dir)])
    built = runner.invoke(cli.app, ["build-candidates", "--all", "--yes", "--data-dir", str(data_dir)])
    assert seeded.exit_code == 0, seeded.output
    assert built.exit_code == 0, built.output

    store = ReviewStore(data_dir / "review.db")
    for item in store.list_queue():
        store.confirm_article(item.sample_id, actor="fixture-reviewer")
    published = runner.invoke(cli.app, ["publish", "--version", "v0.1.0", "--output", str(tmp_path / "gold_sets"), "--data-dir", str(data_dir)])
    reported = runner.invoke(cli.app, ["report", "--data-dir", str(data_dir), "--out", str(tmp_path / "pilot.html")])

    assert published.exit_code == 0, published.output
    assert reported.exit_code == 0, reported.output
    assert (tmp_path / "gold_sets" / "v0.1.0" / "rich_gold.jsonl").is_file()
    assert (tmp_path / "gold_sets" / "v0.1.0" / "eval_projection.jsonl").is_file()
    assert (tmp_path / "pilot.html").is_file()
    assert _source_shape(sources) == before
