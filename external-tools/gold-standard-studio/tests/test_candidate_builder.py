"""Test independent candidate construction, cache identity, and failure isolation."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

import pytest

from gold_standard_studio.candidate_builder import (
    CandidateBuilder,
    CandidateRunRequest,
    CandidateRunnerError,
    DeterministicCandidateRunner,
    ProductionCandidateRunner,
    ReviewCandidateRunner,
)
from gold_standard_studio.candidate_cache import CandidateCache, cache_key
from gold_standard_studio.review_prompt import build_review_prompt, review_prompt_hash
from gold_standard_studio.schemas import CandidateValue
from gold_standard_studio.sources import ArticleSnapshot, CurrentProject


@dataclass
class RecordingRunner:
    """Record requests and return a stable source-specific candidate value."""

    source_name: str
    version: str = "test-v1"
    prompt_hash: str = ""
    parser_version: str = ""
    calls: list[CandidateRunRequest] = field(default_factory=list)

    def run(self, request: CandidateRunRequest) -> CandidateValue:
        """Record one candidate request without consulting another candidate source."""
        self.calls.append(request)
        return CandidateValue(source_name=self.source_name, source_version=self.version)


@dataclass
class FailingRunner:
    """Raise a structured candidate error for one isolated source."""

    source_name: str
    code: str
    version: str = "test-v1"
    prompt_hash: str = ""
    parser_version: str = ""

    def run(self, request: CandidateRunRequest) -> CandidateValue:
        """Expose one provider-like failure without manufacturing a candidate."""
        raise CandidateRunnerError(self.code, f"{self.source_name} failed")


@dataclass
class FlakyRunner:
    """Fail once with a provider timeout before producing a source candidate."""

    source_name: str
    version: str = "test-v1"
    prompt_hash: str = ""
    parser_version: str = ""
    calls: int = 0

    def run(self, request: CandidateRunRequest) -> CandidateValue:
        """Simulate one transient provider failure followed by a valid retry."""
        self.calls += 1
        if self.calls == 1:
            raise CandidateRunnerError("timeout", "temporary provider timeout")
        return CandidateValue(source_name=self.source_name, source_version=self.version)


@pytest.fixture
def article() -> ArticleSnapshot:
    """Return one immutable article snapshot with an exact SHA-256 identifier."""
    return ArticleSnapshot(
        source_link="https://example.test/article",
        title="Kenya solar project",
        content="China Power is building a 100 MW PV solar project with 50 MWh energy storage.",
        publish_date="2026-08-01",
        article_sha256="a" * 64,
        raw_values={},
        display_values={},
    )


@pytest.fixture
def current_project(article: ArticleSnapshot) -> CurrentProject:
    """Return one representative current label without requiring a source database."""
    return CurrentProject(
        source_link=article.source_link,
        canonical_id="KEN-SOL-0001",
        event_id="KEN-SOL-0001-01",
        category="solar",
        raw_values={"canonical_project_name": "Kenya solar", "capacity_mw": 100},
        display_values={"canonical_project_name": "Kenya solar", "capacity_mw": 100},
        entities=(),
    )


def make_builder(
    tmp_path: Path,
    current_project: CurrentProject,
    *,
    production: RecordingRunner | None = None,
    review: RecordingRunner | FailingRunner | None = None,
    deterministic: RecordingRunner | None = None,
) -> CandidateBuilder:
    """Build a real orchestrator around deterministic recording runners."""
    return CandidateBuilder(
        current_labels=lambda _article: [current_project],
        production_runner=production or RecordingRunner("production"),
        review_runner=review or RecordingRunner("review", prompt_hash="review-prompt-v1"),
        deterministic_runner=deterministic or RecordingRunner("deterministic", parser_version="rules-v1"),
        cache=CandidateCache(tmp_path / "data" / "model_cache"),
    )


def test_review_runner_receives_article_but_not_database_or_production_candidate(
    tmp_path: Path, article: ArticleSnapshot, current_project: CurrentProject
) -> None:
    """Prevent the independent reviewer from being anchored by existing answers."""
    reviewer = RecordingRunner("review", prompt_hash="review-prompt-v1")

    make_builder(tmp_path, current_project, review=reviewer).build(article)

    request = reviewer.calls[0]
    assert request.article is article
    assert request.database_candidate is None
    assert request.production_candidate is None


def test_partial_runner_failure_keeps_other_candidates(
    tmp_path: Path, article: ArticleSnapshot, current_project: CurrentProject
) -> None:
    """Return useful source results when a separate provider times out."""
    bundle = make_builder(
        tmp_path, current_project, review=FailingRunner("review", "timeout")
    ).build(article)

    assert bundle.sources["database"].status == "ok"
    assert bundle.sources["production"].status == "ok"
    assert bundle.sources["review"].status == "error"
    assert bundle.sources["review"].error_code == "timeout"
    assert bundle.sources["deterministic"].status == "ok"


def test_cache_key_changes_for_every_behavior_changing_component(article: ArticleSnapshot) -> None:
    """Invalidate cached output whenever a model, prompt, parser, or source changes."""
    baseline = cache_key(
        article, source_name="review", model="model-a", prompt_hash="prompt-a", parser_version="rules-a"
    )

    assert baseline != cache_key(article, source_name="review", model="model-b", prompt_hash="prompt-a", parser_version="rules-a")
    assert baseline != cache_key(article, source_name="review", model="model-a", prompt_hash="prompt-b", parser_version="rules-a")
    assert baseline != cache_key(article, source_name="review", model="model-a", prompt_hash="prompt-a", parser_version="rules-b")
    assert baseline != cache_key(article, source_name="production", model="model-a", prompt_hash="prompt-a", parser_version="rules-a")


def test_builder_reuses_a_content_addressed_cache_record(
    tmp_path: Path, article: ArticleSnapshot, current_project: CurrentProject
) -> None:
    """Avoid rerunning a source whose complete behavior key has already been recorded."""
    production = RecordingRunner("production", version="extractor-v1", prompt_hash="prod-v1")
    builder = make_builder(tmp_path, current_project, production=production)

    builder.build(article)
    builder.build(article)

    assert len(production.calls) == 1


@pytest.mark.parametrize("corrupt_record", [[], None, "scalar", 3.14])
def test_cache_load_quarantines_non_mapping_json_as_a_cache_miss(
    tmp_path: Path, article: ArticleSnapshot, corrupt_record: object
) -> None:
    """Treat every valid non-object cache JSON shape as corruption instead of calling mapping methods on it."""
    cache = CandidateCache(tmp_path / "data" / "model_cache")
    key = cache_key(article, source_name="review", model="model", prompt_hash="prompt", parser_version="parser")
    cache._directory.mkdir(parents=True)
    cache._path_for(key).write_text(json.dumps(corrupt_record), encoding="utf-8")

    assert cache.load(key) is None
    assert not cache._path_for(key).exists()
    assert list(cache._directory.glob("*.corrupt"))


def test_cache_load_quarantines_schema_invalid_record_as_a_cache_miss(
    tmp_path: Path, article: ArticleSnapshot
) -> None:
    """Reject cache records whose candidate payload no longer satisfies the Pydantic contract."""
    cache = CandidateCache(tmp_path / "data" / "model_cache")
    key = cache_key(article, source_name="review", model="model", prompt_hash="prompt", parser_version="parser")
    cache._directory.mkdir(parents=True)
    cache._path_for(key).write_text(
        json.dumps({"key": key.__dict__, "candidate": {"source_name": "review", "status": "invalid"}}),
        encoding="utf-8",
    )

    assert cache.load(key) is None
    assert not cache._path_for(key).exists()


def test_production_runner_normalizes_staged_extractor_output(article: ArticleSnapshot) -> None:
    """Convert the existing staged extractor shape through the established flat prediction contract."""
    runner = ProductionCandidateRunner(
        extractor=lambda _title, _content, _date: {
            "status": "valid_project",
            "projects": [{"common_info": {"Project Name": "Solar", "Capacity (MW)": "100 MW"}}],
        },
        version="extractor-v1",
    )

    candidate = runner.run(CandidateRunRequest(article=article))

    assert candidate.status == "ok"
    assert candidate.projects[0].fields["project_name"].value == "Solar"
    assert candidate.projects[0].fields["capacity_mw"].value == "100 MW"


def test_review_runner_records_invalid_provider_json_as_a_structured_error(article: ArticleSnapshot) -> None:
    """Reject malformed provider payloads rather than silently converting them to an empty answer."""
    runner = ReviewCandidateRunner(
        reviewer=lambda _prompt: "not valid json",
        model="review-model",
        prompt_hash="review-prompt-v1",
    )

    candidate = runner.run(CandidateRunRequest(article=article))

    assert candidate.status == "error"
    assert candidate.error_code == "invalid_json"
    assert candidate.projects == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"projects": [], "unexpected": True},
        {"projects": {}},
        {"projects": [{"gold_project_id": "review-1", "fields": []}]},
    ],
)
def test_review_runner_rejects_incomplete_or_malformed_provider_contract(
    article: ArticleSnapshot, payload: object
) -> None:
    """Require exactly the review top-level contract and valid nested GoldProject values."""
    runner = ReviewCandidateRunner(
        reviewer=lambda _prompt: payload,
        model="review-model",
        prompt_hash="review-prompt-v1",
    )

    candidate = runner.run(CandidateRunRequest(article=article))

    assert candidate.status == "error"
    assert candidate.error_code == "invalid_json"
    assert candidate.projects == []


def test_review_prompt_contains_only_article_material(article: ArticleSnapshot) -> None:
    """Keep review instructions and source text free of database or production answers."""
    prompt = build_review_prompt(article)

    assert article.title in prompt
    assert article.content in prompt
    assert "database candidate" not in prompt.lower()
    assert "production candidate" not in prompt.lower()


def test_review_prompt_hash_changes_with_static_instruction_material() -> None:
    """Invalidate review cache entries when any reusable instruction wording changes."""
    assert review_prompt_hash("Instruction: return exact evidence.") != review_prompt_hash(
        "Instruction: return precise evidence."
    )


def test_builder_rejects_unknown_or_duplicate_source_names_before_execution(
    tmp_path: Path, current_project: CurrentProject
) -> None:
    """Require one runner for each expected source so collisions cannot overwrite bundle entries."""
    with pytest.raises(ValueError, match="unknown=.*unknown.*missing=.*production"):
        make_builder(
            tmp_path,
            current_project,
            production=RecordingRunner("unknown"),
            deterministic=RecordingRunner("review"),
        )


def test_builder_retries_transient_runner_errors_instead_of_caching_them(
    tmp_path: Path, article: ArticleSnapshot, current_project: CurrentProject
) -> None:
    """Allow a later build to retry a timed-out source while retaining other cached candidates."""
    review = FlakyRunner("review", prompt_hash="review-prompt-v1")
    builder = make_builder(tmp_path, current_project, review=review)

    first = builder.build(article)
    second = builder.build(article)

    assert first.sources["review"].error_code == "timeout"
    assert second.sources["review"].status == "ok"
    assert review.calls == 2


def test_deterministic_runner_reuses_capacity_and_taxonomy_policy(article: ArticleSnapshot) -> None:
    """Derive numeric and energy candidates through the existing versioned parser policy."""
    candidate = DeterministicCandidateRunner().run(CandidateRunRequest(article=article))

    fields = candidate.projects[0].fields
    assert fields["capacity_mw"].value == 100.0
    assert fields["storage_energy_mwh"].value == 50.0
    assert fields["proposed_category"].value == "光伏"
