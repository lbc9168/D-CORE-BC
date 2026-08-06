"""Prove the complete local Pilot workflow preserves every source database exactly."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
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
    from gold_standard_studio.field_registry import FIELD_REGISTRY
    from gold_standard_studio.review_store import ReviewStore
    from gold_standard_studio.schemas import EvidenceSpan, GoldEntity, GoldField, GoldProject

    sources = _create_sources(tmp_path)
    with sqlite3.connect(sources[0]) as conn:
        conn.execute(
            "UPDATE raw_news SET title = ?, content = ?, publish_date = ?",
            (
                "肯尼亚光储项目",
                "中国电建作为EPC总承包商在肯尼亚内罗毕开发光伏项目，装机100 MW并配套confirmed储能50 MW/200 MWh；项目已签约，计划2026-08-01开工、2027-12-31完工，总投资100000000美元、合同80000000美元、信贷融资60000000美元。",
                "2026-08-01",
            ),
        )
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
    samples = store.list_queue()
    valid_id = samples[0].sample_id
    snapshot = store.get_article_snapshot(valid_id)
    evidence = [EvidenceSpan(
        quote=snapshot.content,
        start=0,
        end=len(snapshot.content),
        article_sha256=snapshot.article_sha256,
    )]
    values = {
        "project_name": "肯尼亚光储项目", "country": "肯尼亚", "location": "内罗毕",
        "category": "光伏", "generation_sources": ["光伏"], "storage_status": "confirmed",
        "capacity_mw": 100, "storage_power_mw": 50, "storage_energy_mwh": 200,
        "project_stage": "签约", "project_developer": "中国电建",
        "planned_start_date": "2026-08-01", "planned_complete_date": "2027-12-31",
        "investment_date": "2026-08-01", "total_investment_usd": 100_000_000,
        "contract_amount_usd": 80_000_000, "financing_amount_usd": 60_000_000,
        "financing_model": "信贷融资", "project_summary": "中国电建在肯尼亚开发光储项目。",
    }
    fields = {
        name: GoldField(
            state="present",
            value=values[name],
            evidence=evidence if specification.evidence_required else [],
            evidence_required=specification.evidence_required,
        )
        for name, specification in FIELD_REGISTRY.items()
    }
    store.save_structure(
        valid_id,
        [GoldProject(
            gold_project_id="P01",
            fields=fields,
            entities=[GoldEntity(name="中国电建", role="EPC总承包", evidence=evidence)],
        )],
        base_revision=store.current_structure_revision(valid_id),
        actor="fixture-reviewer",
    )
    store.save_article_status(valid_id, "valid", actor="fixture-reviewer")
    store.confirm_article(valid_id, actor="fixture-reviewer")
    negative_statuses = ["no_chinese", "chinese_unnamed"] + ["irrelevant"] * (len(samples) - 3)
    for item, status in zip(samples[1:], negative_statuses, strict=True):
        store.confirm_negative(item.sample_id, status, actor="fixture-reviewer")
    published = runner.invoke(cli.app, ["publish", "--version", "v0.1.0", "--output", str(tmp_path / "gold_sets"), "--data-dir", str(data_dir)])
    reported = runner.invoke(cli.app, ["report", "--data-dir", str(data_dir), "--out", str(tmp_path / "pilot.html")])

    assert published.exit_code == 0, published.output
    assert reported.exit_code == 0, reported.output
    assert (tmp_path / "gold_sets" / "v0.1.0" / "rich_gold.jsonl").is_file()
    assert (tmp_path / "gold_sets" / "v0.1.0" / "eval_projection.jsonl").is_file()
    assert (tmp_path / "pilot.html").is_file()
    records = [
        json.loads(line)
        for line in (tmp_path / "gold_sets" / "v0.1.0" / "rich_gold.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert {record["article_status"] for record in records} == {
        "valid", "no_chinese", "irrelevant", "chinese_unnamed"
    }
    valid_record = next(record for record in records if record["article_status"] == "valid")
    assert set(valid_record["projects"][0]["fields"]) == set(FIELD_REGISTRY)
    assert valid_record["projects"][0]["entities"][0]["role"] == "EPC总承包"
    assert _source_shape(sources) == before
