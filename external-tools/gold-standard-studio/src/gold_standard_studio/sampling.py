"""Select reproducible, unique-source Studio samples with explicit coverage audit metadata."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping, Sequence

from gold_standard_studio.schemas import DatasetManifest


_DEFAULT_CATEGORY_FLOORS = MappingProxyType(
    {
        "储能": 3,
        "制造业": 3,
        "输变电建设": 3,
        "核电": 2,
        "火电": 2,
        "地热及余热": 2,
        "生物质": 2,
    }
)
_FINAL_SPLIT_TARGETS = {"development": 150, "regression": 70, "challenge": 30}


class SamplingInfeasibleError(ValueError):
    """Signal that the supplied candidate pool cannot satisfy the requested immutable quotas."""


@dataclass(frozen=True)
class SampleCandidate:
    """Describe one pre-review article candidate and its deterministic sampling strata."""

    sample_id: str
    source_link: str
    category_tags: tuple[str, ...] = ()
    is_negative: bool = False
    risk_tags: tuple[str, ...] = ()
    risk_weight: float = 1.0

    def __post_init__(self) -> None:
        """Normalize immutable candidate labels and reject incomplete stable identifiers."""
        sample_id = self.sample_id.strip()
        source_link = self.source_link.strip()
        if not sample_id:
            raise ValueError("sample_id must not be empty")
        if not source_link:
            raise ValueError("source_link must not be empty")
        if not math.isfinite(self.risk_weight) or self.risk_weight <= 0:
            raise ValueError("risk_weight must be a finite positive number")
        object.__setattr__(self, "sample_id", sample_id)
        object.__setattr__(self, "source_link", source_link)
        object.__setattr__(self, "category_tags", _normalize_tags(self.category_tags))
        object.__setattr__(self, "risk_tags", _normalize_tags(self.risk_tags))


@dataclass(frozen=True)
class SamplingConfig:
    """Store the fixed seed, quotas, and minimum category coverage for one sample run."""

    seed: int
    size: int
    negative_fraction: float = 0.18
    category_floors: Mapping[str, int] = field(default_factory=lambda: dict(_DEFAULT_CATEGORY_FLOORS))

    def __post_init__(self) -> None:
        """Validate quota inputs and freeze category floors in deterministic key order."""
        if self.size <= 0:
            raise ValueError("size must be positive")
        if not math.isfinite(self.negative_fraction) or not 0 <= self.negative_fraction <= 1:
            raise ValueError("negative_fraction must be between 0 and 1")
        normalized_floors: dict[str, int] = {}
        for category, floor in self.category_floors.items():
            normalized_category = category.strip()
            if not normalized_category:
                raise ValueError("category floor name must not be empty")
            if not isinstance(floor, int) or floor < 0:
                raise ValueError("category floors must be non-negative integers")
            if floor:
                normalized_floors[normalized_category] = floor
        object.__setattr__(self, "category_floors", MappingProxyType(dict(sorted(normalized_floors.items()))))

    @classmethod
    def pilot(cls) -> SamplingConfig:
        """Return the fixed 60-article Pilot configuration from the Studio sample design."""
        return cls(seed=20260806, size=60, negative_fraction=0.18)


@dataclass(frozen=True)
class SelectedSample:
    """Record a selected candidate together with its audit reasons, risk tags, and intended split."""

    sample_id: str
    source_link: str
    category_tags: tuple[str, ...]
    is_negative: bool
    risk_tags: tuple[str, ...]
    inclusion_reasons: tuple[str, ...]
    dataset_split: str
    sampling_seed: int


def _normalize_tags(tags: Sequence[str]) -> tuple[str, ...]:
    """Return sorted, non-empty unique labels so input ordering cannot affect sampling."""
    return tuple(sorted({tag.strip() for tag in tags if tag.strip()}))


def _stable_random(seed: int, sample_id: str) -> float:
    """Map a seed and stable sample ID to a platform-independent pseudo-random fraction."""
    digest = hashlib.sha256(f"{seed}:{sample_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def _weighted_key(candidate: SampleCandidate, seed: int) -> float:
    """Produce an Efraimidis-style deterministic reservoir key weighted by candidate risk."""
    random_fraction = max(_stable_random(seed, candidate.sample_id), 1 / 2**64)
    return -math.log(random_fraction) / candidate.risk_weight


def _normalize_candidates(candidates: Sequence[SampleCandidate]) -> list[SampleCandidate]:
    """Deduplicate candidates by normalized source link and retain the lowest stable sample ID."""
    by_source: dict[str, SampleCandidate] = {}
    for candidate in sorted(candidates, key=lambda row: (row.source_link, row.sample_id)):
        by_source.setdefault(candidate.source_link, candidate)
    normalized = sorted(by_source.values(), key=lambda row: row.sample_id)
    sample_ids = [row.sample_id for row in normalized]
    if len(sample_ids) != len(set(sample_ids)):
        raise SamplingInfeasibleError("sample IDs must be unique after source-link deduplication")
    return normalized


def _negative_quota(config: SamplingConfig) -> int:
    """Round the requested negative share upward at one half to a reproducible integer quota."""
    return math.floor(config.size * config.negative_fraction + 0.5)


def _assert_feasible_pool(candidates: Sequence[SampleCandidate], config: SamplingConfig) -> None:
    """Reject candidate pools that cannot meet basic size, negative, or category availability quotas."""
    if len(candidates) < config.size:
        raise SamplingInfeasibleError(
            f"sample size requires {config.size} unique source links but only {len(candidates)} are available"
        )
    negative_count = sum(row.is_negative for row in candidates)
    required_negative = _negative_quota(config)
    if negative_count < required_negative:
        raise SamplingInfeasibleError(
            f"negative quota requires {required_negative} candidates but only {negative_count} are available"
        )
    for category, floor in config.category_floors.items():
        available = sum(category in row.category_tags for row in candidates)
        if available < floor:
            raise SamplingInfeasibleError(
                f"category floor '{category}' requires {floor} candidates but only {available} are available"
            )


def _rank_weighted(candidates: Sequence[SampleCandidate], seed: int) -> list[SampleCandidate]:
    """Order candidates by deterministic risk-weighted reservoir keys with stable ID tie breaks."""
    return sorted(candidates, key=lambda row: (_weighted_key(row, seed), row.sample_id))


def _satisfy_category_floors(
    candidates: Sequence[SampleCandidate], config: SamplingConfig
) -> tuple[list[SampleCandidate], dict[str, list[str]]]:
    """Choose deterministic floor candidates while preferring positives to preserve the negative quota."""
    selected: list[SampleCandidate] = []
    reasons: dict[str, list[str]] = {}
    counts = {category: 0 for category in config.category_floors}
    remaining = list(candidates)

    while any(counts[category] < floor for category, floor in config.category_floors.items()):
        missing = {
            category for category, floor in config.category_floors.items() if counts[category] < floor
        }
        eligible = [row for row in remaining if missing.intersection(row.category_tags)]
        if not eligible:
            raise SamplingInfeasibleError("category floors cannot be satisfied with unique source links")
        ranked = sorted(
            eligible,
            key=lambda row: (
                row.is_negative,
                -len(missing.intersection(row.category_tags)),
                _weighted_key(row, config.seed),
                row.sample_id,
            ),
        )
        chosen = ranked[0]
        covered = sorted(missing.intersection(chosen.category_tags))
        selected.append(chosen)
        reasons[chosen.sample_id] = [f"category_floor:{category}" for category in covered]
        for category in chosen.category_tags:
            if category in counts:
                counts[category] += 1
        remaining.remove(chosen)
        if len(selected) > config.size:
            raise SamplingInfeasibleError("category floors require more candidates than the sample size")
    return selected, reasons


def _split_targets(size: int) -> dict[str, int]:
    """Allocate exact final targets at 250 and proportional largest-remainder targets at other sizes."""
    if size == sum(_FINAL_SPLIT_TARGETS.values()):
        return dict(_FINAL_SPLIT_TARGETS)
    raw_targets = {
        name: size * target / sum(_FINAL_SPLIT_TARGETS.values())
        for name, target in _FINAL_SPLIT_TARGETS.items()
    }
    targets = {name: math.floor(value) for name, value in raw_targets.items()}
    remainder = size - sum(targets.values())
    for name in sorted(raw_targets, key=lambda key: (-(raw_targets[key] - targets[key]), key))[:remainder]:
        targets[name] += 1
    return targets


def _assign_splits(selected: Sequence[SampleCandidate], config: SamplingConfig) -> dict[str, str]:
    """Put the highest-risk selected articles in Challenge and deterministically divide the remainder."""
    targets = _split_targets(config.size)
    challenge_ranked = sorted(
        selected,
        key=lambda row: (
            -row.risk_weight,
            -len(row.risk_tags),
            _weighted_key(row, config.seed),
            row.sample_id,
        ),
    )
    challenge = challenge_ranked[: targets["challenge"]]
    challenge_ids = {row.sample_id for row in challenge}
    remaining = [row for row in selected if row.sample_id not in challenge_ids]
    regression = _rank_weighted(remaining, config.seed)[: targets["regression"]]
    regression_ids = {row.sample_id for row in regression}
    return {
        row.sample_id: (
            "challenge"
            if row.sample_id in challenge_ids
            else "regression"
            if row.sample_id in regression_ids
            else "development"
        )
        for row in selected
    }


def select_sample(candidates: Sequence[SampleCandidate], config: SamplingConfig) -> list[SelectedSample]:
    """Select a deterministic, deduplicated, floor-covered sample and attach complete selection metadata."""
    normalized = _normalize_candidates(candidates)
    _assert_feasible_pool(normalized, config)
    selected, reasons = _satisfy_category_floors(normalized, config)
    selected_ids = {row.sample_id for row in selected}
    negative_quota = _negative_quota(config)
    selected_negative = sum(row.is_negative for row in selected)
    if selected_negative > negative_quota:
        raise SamplingInfeasibleError(
            "category floors require more negative candidates than the configured negative quota"
        )

    needed_negative = negative_quota - selected_negative
    negative_pool = [row for row in normalized if row.is_negative and row.sample_id not in selected_ids]
    for candidate in _rank_weighted(negative_pool, config.seed)[:needed_negative]:
        selected.append(candidate)
        selected_ids.add(candidate.sample_id)
        reasons[candidate.sample_id] = ["negative_quota"]
    if sum(row.is_negative for row in selected) != negative_quota:
        raise SamplingInfeasibleError("negative quota cannot be satisfied after category-floor selection")
    if len(selected) > config.size:
        raise SamplingInfeasibleError("combined category and negative quotas exceed the sample size")

    fill_pool = [
        row
        for row in normalized
        if row.sample_id not in selected_ids and (not row.is_negative or negative_quota == 0)
    ]
    required_fill = config.size - len(selected)
    weighted_fill = _rank_weighted(fill_pool, config.seed)[:required_fill]
    if len(weighted_fill) != required_fill:
        raise SamplingInfeasibleError("not enough candidates remain after satisfying immutable quotas")
    for candidate in weighted_fill:
        selected.append(candidate)
        selected_ids.add(candidate.sample_id)
        reasons[candidate.sample_id] = ["risk_weighted_fill"]

    splits = _assign_splits(selected, config)
    return [
        SelectedSample(
            sample_id=candidate.sample_id,
            source_link=candidate.source_link,
            category_tags=candidate.category_tags,
            is_negative=candidate.is_negative,
            risk_tags=candidate.risk_tags,
            inclusion_reasons=tuple(reasons[candidate.sample_id]),
            dataset_split=splits[candidate.sample_id],
            sampling_seed=config.seed,
        )
        for candidate in sorted(selected, key=lambda row: row.sample_id)
    ]


def build_manifest(
    selected: Sequence[SelectedSample], source_hashes: Mapping[str, str]
) -> DatasetManifest:
    """Build a stably ordered manifest that binds one deterministic sample to immutable source hashes."""
    sample_ids = [row.sample_id for row in selected]
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("selected samples must have unique sample IDs")
    seeds = {row.sampling_seed for row in selected}
    if len(seeds) > 1:
        raise ValueError("selected samples must use one sampling seed")
    return DatasetManifest(
        seed=next(iter(seeds), None),
        sample_ids=sorted(sample_ids),
        source_hashes=dict(sorted(source_hashes.items())),
    )
