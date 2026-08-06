"""Provide a conservative local command line for Golden Standard Studio operations."""

from __future__ import annotations

from collections import Counter
import html
import json
from pathlib import Path
import sqlite3
from typing import Annotated, Any, Iterable

import typer

from gold_standard_studio.candidate_builder import CandidateBuilder, CandidateRunRequest, CandidateRunnerError, DeterministicCandidateRunner
from gold_standard_studio.candidate_cache import CandidateCache
from gold_standard_studio.config import StudioConfig
from gold_standard_studio.difficulty import score_difficulty
from gold_standard_studio.exporter import publish_gold
from gold_standard_studio.review_store import ReviewStore
from gold_standard_studio.sampling import SampleCandidate, SamplingConfig, SamplingInfeasibleError, SelectedSample, build_manifest, select_sample
from gold_standard_studio.schemas import CandidateValue, DifficultyScore, RichGoldRecord
from gold_standard_studio.sources import ArticleSnapshot, SourceContractError, SourceRepository
from gold_standard_studio.web.app import create_app


app = typer.Typer(add_completion=False, help="Local, read-only Golden Standard Studio operations.")
_CONFIG_NAME = "studio-config.json"
_MANIFEST_NAME = "sample-manifest.json"
_SAMPLING_CATEGORY_ALIASES = {"新能源制造业": "制造业"}


class _DisabledExternalRunner:
    """Represent a cost-bearing candidate provider that the safe CLI deliberately leaves disabled."""

    def __init__(self, source_name: str) -> None:
        """Store the expected candidate source identity without holding any model credentials."""
        self.source_name = source_name
        self.version = "disabled"
        self.prompt_hash = ""
        self.parser_version = ""

    def run(self, _request: CandidateRunRequest) -> CandidateValue:
        """Return a structured disabled-provider result without sending article content anywhere."""
        raise CandidateRunnerError("model_calls_disabled", "external model calls are disabled by the CLI")


def _coverage_category_tags(categories: tuple[str, ...]) -> tuple[str, ...]:
    """Add documented Pilot coverage aliases while retaining every exact production category label for audit."""
    aliases = {_SAMPLING_CATEGORY_ALIASES[category] for category in categories if category in _SAMPLING_CATEGORY_ALIASES}
    return tuple(sorted(set(categories).union(aliases)))


def _config_from_args(
    raw_news_db: Path | None, projects_db: Path | None, main_db: Path | None, data_dir: Path
) -> StudioConfig:
    """Resolve either a complete explicit source triple or a previously saved local Studio configuration."""
    supplied = (raw_news_db, projects_db, main_db)
    if any(path is not None for path in supplied):
        if not all(path is not None for path in supplied):
            raise typer.BadParameter("--raw-news-db, --projects-db, and --main-db must be supplied together")
        return StudioConfig.from_paths(
            raw_news_db=raw_news_db, projects_db=projects_db, main_db=main_db, data_dir=data_dir
        )
    config_path = data_dir / _CONFIG_NAME
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise typer.BadParameter("source paths are required until gold-studio init has been run") from error
    except json.JSONDecodeError as error:
        raise typer.BadParameter(f"invalid Studio configuration: {config_path}") from error
    if not isinstance(payload, dict):
        raise typer.BadParameter(f"invalid Studio configuration: {config_path}")
    return StudioConfig.from_paths(
        raw_news_db=payload.get("raw_news_db"), projects_db=payload.get("projects_db"),
        main_db=payload.get("main_db"), data_dir=data_dir,
    )


def _write_config(config: StudioConfig) -> Path:
    """Persist only explicit source paths in the Studio-owned data directory for later local commands."""
    config.data_dir.mkdir(parents=True, exist_ok=True)
    path = config.data_dir / _CONFIG_NAME
    payload = {
        "raw_news_db": str(config.raw_news_db.resolve()),
        "projects_db": str(config.projects_db.resolve()),
        "main_db": str(config.main_db.resolve()),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def _sample_evidence(selected: list[SelectedSample], before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Assemble machine-readable selection quotas and before/after source-integrity evidence for a dry-run record."""
    categories = Counter(category for row in selected for category in row.category_tags)
    risks = Counter(risk for row in selected for risk in row.risk_tags)
    statuses = Counter("unlabeled_negative_proxy" if row.is_negative else "labeled_current_source" for row in selected)
    floors = {
        category: {"required": required, "selected": categories[category], "satisfied": categories[category] >= required}
        for category, required in SamplingConfig.pilot().category_floors.items()
    }
    unchanged = {
        "logical_hashes": before["logical_hashes"] == after["logical_hashes"],
        "physical_hashes": before["physical_hashes"] == after["physical_hashes"],
        "raw_news": before["raw_news"] == after["raw_news"],
        "canonical_projects": before["canonical_projects"] == after["canonical_projects"],
        "project_events": before["project_events"] == after["project_events"],
    }
    return {
        "mode": "dry-run",
        "selection": {
            "selected_count": len(selected), "negative_count": sum(row.is_negative for row in selected),
            "statuses": dict(sorted(statuses.items())), "categories": dict(sorted(categories.items())),
            "risks": dict(sorted(risks.items())), "category_floors": floors,
        },
        "source_before": before,
        "source_after": after,
        "unchanged": unchanged,
    }


def _sampling_candidates(repository: SourceRepository) -> tuple[list[SampleCandidate], dict[str, ArticleSnapshot]]:
    """Translate immutable raw articles and current labels into conservative deterministic sampling strata."""
    by_sample: dict[str, ArticleSnapshot] = {}
    candidates: list[SampleCandidate] = []
    for index, row in enumerate(repository.list_sampling_rows(), start=1):
        article = row.article
        raw_id = article.raw_values.get("id")
        sample_id = f"raw-{raw_id}" if raw_id is not None else f"article-{index:06d}"
        categories = _coverage_category_tags(row.category_tags)
        is_negative = row.label_count == 0
        risks: list[str] = []
        if is_negative:
            risks.append("unlabeled_source")
        if row.label_count > 1:
            risks.append("multiple_projects")
        if any(category in SamplingConfig.pilot().category_floors for category in categories):
            risks.append("rare_category")
        candidates.append(
            SampleCandidate(
                sample_id=sample_id, source_link=article.source_link, category_tags=categories,
                is_negative=is_negative, risk_tags=tuple(risks), risk_weight=1.0 + len(risks),
            )
        )
        by_sample[sample_id] = article
    return candidates, by_sample


def _sample_records(selected: Iterable[SelectedSample], articles: dict[str, ArticleSnapshot]) -> list[RichGoldRecord]:
    """Create unreviewed structural shells without treating database labels as human-confirmed gold facts."""
    records: list[RichGoldRecord] = []
    for row in selected:
        article = articles[row.sample_id]
        records.append(
            RichGoldRecord(
                sample_id=row.sample_id, article_sha256=article.article_sha256, source_link=article.source_link,
                risk_tags=list(row.risk_tags), dataset_split=row.dataset_split,
                difficulty=DifficultyScore(score=0, reason_codes=[], risk_tags=list(row.risk_tags)),
                audit_metadata={
                    "sampling": {
                        "seed": row.sampling_seed, "inclusion_reasons": list(row.inclusion_reasons),
                        "category_tags": list(row.category_tags), "negative_proxy": row.is_negative,
                    }
                },
            )
        )
    return records


def _write_sample_manifest(data_dir: Path, selected: list[SelectedSample], source_hashes: dict[str, str]) -> Path:
    """Write a local audit manifest that binds selected IDs to the immutable source logical hashes."""
    manifest = build_manifest(selected, source_hashes).model_dump(mode="json")
    manifest["selected"] = [
        {
            "sample_id": row.sample_id, "source_link": row.source_link, "category_tags": list(row.category_tags),
            "risk_tags": list(row.risk_tags), "negative_proxy": row.is_negative,
            "inclusion_reasons": list(row.inclusion_reasons), "dataset_split": row.dataset_split,
        }
        for row in selected
    ]
    path = data_dir / _MANIFEST_NAME
    path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def _build_candidate_builder(repository: SourceRepository, data_dir: Path) -> CandidateBuilder:
    """Build a no-network candidate orchestrator whose disabled providers remain explicit in review state."""
    return CandidateBuilder(
        current_labels=repository.load_current_labels,
        production_runner=_DisabledExternalRunner("production"), review_runner=_DisabledExternalRunner("review"),
        deterministic_runner=DeterministicCandidateRunner(), cache=CandidateCache(data_dir / "model_cache"),
    )


def _load_store(data_dir: Path, *, require_existing: bool = True) -> ReviewStore:
    """Open the Studio-owned review store while refusing commands that would invent review history unexpectedly."""
    store = ReviewStore(data_dir / "review.db")
    if require_existing and not store.path.is_file():
        raise typer.BadParameter(f"review database does not exist: {store.path}; run init then sample --apply")
    store.initialize()
    return store


@app.command()
def init(
    raw_news_db: Annotated[Path, typer.Option("--raw-news-db")],
    projects_db: Annotated[Path, typer.Option("--projects-db")],
    main_db: Annotated[Path, typer.Option("--main-db")],
    data_dir: Annotated[Path, typer.Option("--data-dir")] = Path("data"),
) -> None:
    """Initialize isolated local review storage after validating explicit read-only source paths."""
    config = StudioConfig.from_paths(raw_news_db=raw_news_db, projects_db=projects_db, main_db=main_db, data_dir=data_dir)
    try:
        with SourceRepository(config) as repository:
            repository.validate_contract()
    except (SourceContractError, sqlite3.DatabaseError) as error:
        raise typer.BadParameter(str(error)) from error
    _write_config(config)
    _load_store(data_dir, require_existing=False)
    typer.echo(f"initialized data_dir={data_dir} (source databases remain read-only)")


@app.command()
def sample(
    raw_news_db: Annotated[Path | None, typer.Option("--raw-news-db")] = None,
    projects_db: Annotated[Path | None, typer.Option("--projects-db")] = None,
    main_db: Annotated[Path | None, typer.Option("--main-db")] = None,
    data_dir: Annotated[Path, typer.Option("--data-dir")] = Path("data"),
    pilot_size: Annotated[int, typer.Option("--pilot-size")] = 60,
    apply: Annotated[bool, typer.Option("--apply")] = False,
    format: Annotated[str, typer.Option("--format")] = "text",
) -> None:
    """Propose or explicitly seed an exact quota-validated Pilot sample without writing source databases."""
    config = _config_from_args(raw_news_db, projects_db, main_db, data_dir)
    sampling_config = SamplingConfig.pilot() if pilot_size == 60 else SamplingConfig(
        seed=config.seed, size=pilot_size, negative_fraction=config.negative_fraction,
    )
    if format not in {"text", "json"}:
        raise typer.BadParameter("--format must be text or json")
    with SourceRepository(config) as repository:
        repository.validate_contract()
        candidates, articles = _sampling_candidates(repository)
        source_before = repository.source_integrity()
        try:
            selected = select_sample(candidates, sampling_config)
        except SamplingInfeasibleError as error:
            raise typer.BadParameter(f"Pilot quotas are infeasible: {error}") from error
        source_after = repository.source_integrity()
    evidence = _sample_evidence(selected, source_before, source_after)
    if format == "json":
        typer.echo(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    else:
        selection = evidence["selection"]
        typer.echo(
            f"selected={selection['selected_count']} negatives={selection['negative_count']} statuses={selection['statuses']} "
            f"categories={selection['categories']} risks={selection['risks']} "
            f"source_hashes_unchanged={evidence['unchanged']['logical_hashes']}"
        )
    if not apply:
        if format != "json":
            typer.echo("dry-run: no review database, model cache, or source database was written")
        return
    store = _load_store(data_dir, require_existing=False)
    for record in _sample_records(selected, articles):
        store.seed_article(record)
    manifest_path = _write_sample_manifest(data_dir, selected, source_before["logical_hashes"])
    typer.echo(f"applied selected={len(selected)} review_db={store.path} manifest={manifest_path}")


@app.command("build-candidates")
def build_candidates(
    sample_id: Annotated[str | None, typer.Option("--sample-id")] = None,
    all_samples: Annotated[bool, typer.Option("--all")] = False,
    resume: Annotated[bool, typer.Option("--resume")] = False,
    yes: Annotated[bool, typer.Option("--yes")] = False,
    data_dir: Annotated[Path, typer.Option("--data-dir")] = Path("data"),
) -> None:
    """Build candidate bundles only after scoped confirmation, recording disabled model sources instead of calling them."""
    if all_samples == (sample_id is not None):
        raise typer.BadParameter("provide exactly one of --sample-id or --all")
    config = _config_from_args(None, None, None, data_dir)
    store = _load_store(data_dir)
    records = [store.get_article(sample_id)] if sample_id else [store.get_article(item.sample_id) for item in store.list_all_samples()]
    if resume:
        records = [record for record in records if not store.has_candidate_bundle(record.sample_id)]
    typer.echo(f"candidate scope count={len(records)} remaining={len(records)}; external model calls are disabled by this CLI")
    if not records:
        return
    if not yes and not typer.confirm(f"Build candidates for exactly {len(records)} samples?"):
        raise typer.Abort()
    with SourceRepository(config) as repository:
        builder = _build_candidate_builder(repository, data_dir)
        for record in records:
            article = repository.load_article(record.source_link or "")
            bundle = builder.build(article, sample_id=record.sample_id)
            store.save_candidate_bundle(bundle)
            store.update_sample_scoring(record.sample_id, score_difficulty(bundle))
    typer.echo(f"built={len(records)} model_calls=0")


@app.command()
def serve(
    data_dir: Annotated[Path, typer.Option("--data-dir")] = Path("data"),
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8765,
    allow_network: Annotated[bool, typer.Option("--allow-network")] = False,
) -> None:
    """Run the review interface on loopback by default and require an explicit network exposure opt-in."""
    if host not in {"127.0.0.1", "::1", "localhost"} and not allow_network:
        raise typer.BadParameter("non-loopback hosts require --allow-network")
    config = _config_from_args(None, None, None, data_dir)
    store = _load_store(data_dir)
    import uvicorn

    with SourceRepository(config) as repository:
        typer.echo(f"serving http://{host}:{port}/queue")
        uvicorn.run(create_app(store, source_repository=repository), host=host, port=port)


@app.command()
def publish(
    version: Annotated[str | None, typer.Option("--version")] = None,
    output: Annotated[Path, typer.Option("--output")] = Path("eval/gold_sets/database_core"),
    data_dir: Annotated[Path, typer.Option("--data-dir")] = Path("data"),
) -> None:
    """Publish only a fully human-confirmed immutable release to a previously unused semantic version directory."""
    if not version:
        raise typer.BadParameter("--version is required")
    store = _load_store(data_dir)
    paths = publish_gold(store, output, version)
    typer.echo(f"published version={version} rich_gold={paths.rich_gold} eval_projection={paths.eval_projection}")


def _report_counts(store: ReviewStore) -> dict[str, Counter[str]]:
    """Aggregate review queue and draft metadata into compact report distributions for fast operator inspection."""
    statuses: Counter[str] = Counter()
    splits: Counter[str] = Counter()
    risks: Counter[str] = Counter()
    for item in store.list_all_samples():
        statuses[item.review_status] += 1
        if item.dataset_split:
            splits[item.dataset_split] += 1
        risks.update(item.risk_tags)
    return {"status": statuses, "split": splits, "risk": risks}


def _render_report(title: str, counts: dict[str, Counter[str]], row_counts: dict[str, int]) -> str:
    """Render an accessible, entirely inline HTML report without scripts, remote assets, or unsafe interpolation."""
    sections = "".join(
        "<section><h2>{}</h2><table><caption>{}</caption><thead><tr><th scope=\"col\">Label</th><th scope=\"col\">Count</th></tr></thead><tbody>{}</tbody></table></section>".format(
            html.escape(name.title()), html.escape(f"{name.title()} distribution"),
            "".join(f"<tr><th scope=\"row\">{html.escape(key)}</th><td>{value}</td></tr>" for key, value in sorted(values.items())) or "<tr><td colspan=\"2\">No records</td></tr>",
        )
        for name, values in counts.items()
    )
    rows = "".join(f"<tr><th scope=\"row\">{html.escape(key)}</th><td>{value}</td></tr>" for key, value in sorted(row_counts.items()))
    return f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{html.escape(title)}</title><style>body{{font:16px system-ui,sans-serif;line-height:1.5;margin:auto;max-width:70rem;padding:1rem}}table{{border-collapse:collapse;margin-bottom:1.5rem;min-width:20rem}}th,td{{border:1px solid #555;padding:.45rem;text-align:left}}th{{background:#f2f2f2}}caption{{text-align:left;font-weight:700;padding:.25rem 0}}</style></head><body><main><h1>{html.escape(title)}</h1><p>Local Studio report; no remote assets or scripts are loaded.</p>{sections}<section><h2>Review database rows</h2><table><caption>SQLite review-state rows</caption><thead><tr><th scope=\"col\">Table</th><th scope=\"col\">Count</th></tr></thead><tbody>{rows}</tbody></table></section></main></body></html>"


@app.command()
def report(
    data_dir: Annotated[Path, typer.Option("--data-dir")] = Path("data"),
    out: Annotated[Path | None, typer.Option("--out")] = None,
    format: Annotated[str, typer.Option("--format")] = "html",
) -> None:
    """Write one self-contained accessible HTML report from local review state without reading or writing sources."""
    if format != "html":
        raise typer.BadParameter("only --format html is supported")
    store = _load_store(data_dir)
    output = out or data_dir / "reports" / "pilot.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_render_report("Golden Standard Studio Pilot Report", _report_counts(store), store.table_row_counts()), encoding="utf-8")
    typer.echo(f"report={output}")


if __name__ == "__main__":
    app()
