"""Build independent database, production, review, and deterministic candidate bundles."""

from __future__ import annotations

import importlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from gold_standard_studio.candidate_cache import CandidateCache, cache_key
from gold_standard_studio.field_registry import FIELD_REGISTRY
from gold_standard_studio.review_prompt import build_review_prompt, review_prompt_hash
from gold_standard_studio.schemas import CandidateBundle, CandidateValue, GoldEntity, GoldField, GoldProject
from gold_standard_studio.sources import ArticleSnapshot, CurrentProject


@dataclass(frozen=True)
class CandidateRunRequest:
    """Carry the immutable article for a runner while keeping other candidate answers optional."""

    article: ArticleSnapshot
    database_candidate: CandidateValue | None = None
    production_candidate: CandidateValue | None = None


class CandidateRunner(Protocol):
    """Define the isolated runner interface used by the candidate orchestrator."""

    source_name: str
    version: str
    prompt_hash: str
    parser_version: str

    def run(self, request: CandidateRunRequest) -> CandidateValue:
        """Produce one source-local candidate from the supplied isolated request."""


class CandidateRunnerError(RuntimeError):
    """Represent an expected candidate-provider failure with a stable machine-readable code."""

    def __init__(self, code: str, detail: str) -> None:
        """Create one recoverable source error that the bundle can retain."""
        super().__init__(detail)
        self.code = code


class DatabaseCandidateRunner:
    """Map immutable current project labels into the Studio's candidate value contract."""

    source_name = "database"
    version = "current-labels-v1"
    prompt_hash = ""
    parser_version = ""

    def __init__(self, current_labels: Callable[[ArticleSnapshot], Sequence[CurrentProject]]) -> None:
        """Store the read-only current-label loader supplied by the Studio source boundary."""
        self._current_labels = current_labels

    def run(self, request: CandidateRunRequest) -> CandidateValue:
        """Translate all current labels for the request article without mutating the source repository."""
        projects = [
            _current_project_to_gold(project, index)
            for index, project in enumerate(self._current_labels(request.article), start=1)
        ]
        return CandidateValue(source_name=self.source_name, source_version=self.version, projects=projects)


class ProductionCandidateRunner:
    """Run the existing staged extractor and normalize it through eval's flat prediction contract."""

    source_name = "production"
    prompt_hash = ""
    parser_version = ""

    def __init__(self, extractor: Callable[..., Mapping[str, Any]], *, version: str) -> None:
        """Store a staged extractor callable and its behavior version without invoking it yet."""
        self._extractor = extractor
        self.version = version

    def run(self, request: CandidateRunRequest) -> CandidateValue:
        """Extract one article and convert its established flattened result into Studio projects."""
        article = request.article
        raw_result = self._extractor(article.title, article.content, article.publish_date)
        flattened = _flat_prediction(dict(raw_result))
        projects = [_flat_project_to_gold(project, index) for index, project in enumerate(flattened["projects"], 1)]
        return CandidateValue(
            source_name=self.source_name,
            source_version=self.version,
            projects=projects,
            metadata={"status": flattened["status"]},
        )


class ReviewCandidateRunner:
    """Call a dedicated article-only reviewer and preserve invalid payloads as explicit failures."""

    source_name = "review"
    parser_version = "review-json-v1"

    def __init__(self, reviewer: Callable[[str], str | Mapping[str, Any]], *, model: str, prompt_hash: str | None = None) -> None:
        """Store a review provider and all model/prompt versions that alter its output behavior."""
        self._reviewer = reviewer
        self.version = model
        self.prompt_hash = prompt_hash or review_prompt_hash()

    def run(self, request: CandidateRunRequest) -> CandidateValue:
        """Review only article material and return structured JSON failures without coercion."""
        if request.database_candidate is not None or request.production_candidate is not None:
            raise CandidateRunnerError("independence_violation", "review runner received another candidate")
        try:
            payload = self._reviewer(build_review_prompt(request.article))
            decoded = json.loads(payload) if isinstance(payload, str) else payload
        except json.JSONDecodeError as error:
            return _error_candidate(self.source_name, self.version, "invalid_json", str(error))
        if not isinstance(decoded, Mapping):
            return _error_candidate(self.source_name, self.version, "invalid_json", "review response is not an object")
        if set(decoded) != {"projects"}:
            return _error_candidate(
                self.source_name,
                self.version,
                "invalid_json",
                "review response must contain only the required projects key",
            )
        if not isinstance(decoded["projects"], list):
            return _error_candidate(
                self.source_name,
                self.version,
                "invalid_json",
                "review response projects must be a list",
            )
        try:
            projects = [GoldProject.model_validate(project) for project in decoded["projects"]]
        except (TypeError, ValueError) as error:
            return _error_candidate(self.source_name, self.version, "invalid_json", str(error))
        return CandidateValue(source_name=self.source_name, source_version=self.version, projects=projects)


class DeterministicCandidateRunner:
    """Derive parser and taxonomy signals from immutable article text without a model call."""

    source_name = "deterministic"
    version = "deterministic-parsers-v1"
    prompt_hash = ""

    def __init__(self) -> None:
        """Read the existing taxonomy policy version for complete deterministic cache identity."""
        POLICY_VERSION = _pipeline_module("s01b_project_extractor.energy_taxonomy").POLICY_VERSION

        self.parser_version = POLICY_VERSION

    def run(self, request: CandidateRunRequest) -> CandidateValue:
        """Reuse existing capacity, storage, and energy taxonomy parsers on article content."""
        capacity_parser = _pipeline_module("s01b_project_extractor.capacity_parser")
        energy_taxonomy = _pipeline_module("s01b_project_extractor.energy_taxonomy")

        text = request.article.content
        energy = energy_taxonomy.extract_energy_facts(text)
        capacity, _details = capacity_parser.parse_capacity_mw(_capacity_phrase(text), category=None)
        classification = energy_taxonomy.classify_energy(energy.generation_sources, energy.storage_status)
        fields = _present_fields(
            {
                "capacity_mw": capacity,
                "generation_sources": list(energy.generation_sources),
                "storage_status": energy.storage_status,
                "storage_power_mw": energy.storage_power_mw,
                "storage_energy_mwh": energy.storage_energy_mwh,
                "category": classification.proposed_category,
            }
        )
        return CandidateValue(
            source_name=self.source_name,
            source_version=self.version,
            projects=[GoldProject(gold_project_id="deterministic-1", fields=fields)],
            metadata={"taxonomy_policy_version": classification.policy_version, "needs_review": classification.needs_review},
        )


class CandidateBuilder:
    """Orchestrate all four candidate sources with cache isolation and per-source failure retention."""

    def __init__(
        self,
        *,
        current_labels: Callable[[ArticleSnapshot], Sequence[CurrentProject]],
        production_runner: CandidateRunner,
        review_runner: CandidateRunner,
        deterministic_runner: CandidateRunner,
        cache: CandidateCache,
    ) -> None:
        """Compose source-local runners around a private Studio candidate cache."""
        self._runners: tuple[CandidateRunner, ...] = (
            DatabaseCandidateRunner(current_labels),
            production_runner,
            review_runner,
            deterministic_runner,
        )
        _validate_runner_sources(self._runners)
        self._cache = cache

    def build(self, article: ArticleSnapshot, *, sample_id: str | None = None) -> CandidateBundle:
        """Build a complete bundle while preventing candidate answers from crossing runner boundaries."""
        sources: dict[str, CandidateValue] = {}
        for runner in self._runners:
            key = cache_key(
                article,
                source_name=runner.source_name,
                model=runner.version,
                prompt_hash=runner.prompt_hash,
                parser_version=runner.parser_version,
            )
            candidate = self._cache.load(key)
            if candidate is None:
                candidate = self._run_isolated(runner, article)
                if candidate.status == "ok":
                    self._cache.store(key, candidate)
            sources[runner.source_name] = candidate
        return CandidateBundle(sample_id=sample_id or article.article_sha256, article_sha256=article.article_sha256, sources=sources)

    def _run_isolated(self, runner: CandidateRunner, article: ArticleSnapshot) -> CandidateValue:
        """Run one source with an article-only request and convert exceptions to source-local errors."""
        try:
            candidate = runner.run(CandidateRunRequest(article=article))
            if candidate.source_name != runner.source_name:
                raise CandidateRunnerError("source_mismatch", "runner returned a candidate for another source")
            return candidate
        except CandidateRunnerError as error:
            return _error_candidate(runner.source_name, runner.version, error.code, str(error))
        except Exception as error:
            return _error_candidate(runner.source_name, runner.version, "runner_error", str(error))


def _current_project_to_gold(project: CurrentProject, index: int) -> GoldProject:
    """Map one current database label's heterogeneous column names into reviewed Studio fields."""
    values = project.display_values
    mapped = {
        "project_name": _first_value(values, "canonical_project_name", "project_name", "Project Name"),
        "category": project.category,
        "capacity_mw": _first_value(values, "capacity_mw", "Capacity (MW)", "capacity_mw_raw"),
        "project_stage": _first_value(values, "project_stage", "Project Stage"),
        "location": _first_value(values, "location", "Location(State)"),
        "total_investment_usd": _first_value(values, "total_investment_usd", "Total Project Investment (Million USD)"),
        "financing_model": _first_value(values, "financing_model", "Financing Model"),
    }
    entities = [
        GoldEntity(
            role=str(_first_value(entity, "involvement_type", "Type of involvement of Chinese Entities") or "Unknown"),
            name=str(_first_value(entity, "implementing_entity", "Implementing Chinese Entity", "name") or "Unknown"),
        )
        for entity in project.entities
    ]
    return GoldProject(
        gold_project_id=project.canonical_id or project.event_id or f"database-{index}",
        fields=_present_fields(mapped),
        entities=entities,
    )


def _flat_project_to_gold(project: Mapping[str, Any], index: int) -> GoldProject:
    """Convert one eval flat prediction project into the Studio field and entity contracts."""
    mapped = {name: project.get(name) for name in FIELD_REGISTRY if name in project}
    mapped["project_name"] = project.get("project_name")
    mapped["category"] = project.get("proposed_category", project.get("category"))
    entities = [
        GoldEntity(role=str(entity.get("involvement_type") or "Unknown"), name=str(entity.get("name") or "Unknown"))
        for entity in project.get("entities", [])
        if entity.get("name")
    ]
    return GoldProject(gold_project_id=f"production-{index}", fields=_present_fields(mapped), entities=entities)


def _present_fields(values: Mapping[str, Any]) -> dict[str, GoldField]:
    """Represent nonblank source values as present fields and omit unavailable values from a candidate."""
    return {
        name: GoldField(state="present", value=value)
        for name, value in values.items()
        if value is not None and value != "" and value != []
    }


def _first_value(values: Mapping[str, Any], *keys: str) -> Any:
    """Return the first nonblank value among alternate production column names."""
    for key in keys:
        value = values.get(key)
        if value is not None and value != "":
            return value
    return None


def _capacity_phrase(text: str) -> str | None:
    """Select one candidate installed-capacity phrase before passing it to the established strict parser."""
    match = re.search(
        r"\d+(?:\.\d+)?\s*(?:万千瓦(?!时)|(?:MW|兆瓦|MWp)(?!h|/年|时)|(?:GW|吉瓦)(?!/年|时)|(?:kW|千瓦)(?!时|h))",
        text,
        re.IGNORECASE,
    )
    return match.group(0) if match else None


def _error_candidate(source_name: str, source_version: str, code: str, detail: str) -> CandidateValue:
    """Construct a structured candidate error without inventing empty project content."""
    return CandidateValue(source_name=source_name, source_version=source_version, status="error", error_code=code, error_detail=detail)


def _validate_runner_sources(runners: Sequence[CandidateRunner]) -> None:
    """Reject missing, duplicate, or unknown runner names before any candidate execution can begin."""
    expected = {"database", "production", "review", "deterministic"}
    names = [runner.source_name for runner in runners]
    actual = set(names)
    unknown = sorted(actual.difference(expected))
    duplicates = sorted(name for name in actual if names.count(name) != 1)
    missing = sorted(expected.difference(actual))
    if unknown or duplicates or missing:
        raise ValueError(f"invalid candidate runners: unknown={unknown}; duplicates={duplicates}; missing={missing}")


def _flat_prediction(result: dict[str, Any]) -> dict[str, Any]:
    """Import and invoke the established evaluation flattening contract from the repository root."""
    return _pipeline_module("eval.predict").flat_prediction(result)


def _pipeline_module(name: str) -> Any:
    """Import a reusable repository module after exposing its source roots to the isolated Studio runtime."""
    repository_root = Path(__file__).resolve().parents[4]
    extractor_source = repository_root / "s01b_project_extractor" / "src"
    for root in (repository_root, extractor_source):
        text = str(root)
        if text not in sys.path:
            sys.path.insert(0, text)
    return importlib.import_module(name)
