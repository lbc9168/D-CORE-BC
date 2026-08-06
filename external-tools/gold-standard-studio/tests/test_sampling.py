"""Test deterministic, quota-aware sampling for Golden Standard Studio."""

from collections import Counter

import pytest

from gold_standard_studio.sampling import (
    SampleCandidate,
    SamplingConfig,
    SamplingInfeasibleError,
    build_manifest,
    select_sample,
)


def _candidate(
    sample_id: str,
    *,
    category_tags: tuple[str, ...] = ("光伏",),
    is_negative: bool = False,
    risk_tags: tuple[str, ...] = (),
    risk_weight: float = 1.0,
    source_link: str | None = None,
) -> SampleCandidate:
    """Create a compact candidate with a unique deterministic source link by default."""
    return SampleCandidate(
        sample_id=sample_id,
        source_link=source_link or f"https://example.test/{sample_id}",
        category_tags=category_tags,
        is_negative=is_negative,
        risk_tags=risk_tags,
        risk_weight=risk_weight,
    )


@pytest.fixture
def candidates() -> list[SampleCandidate]:
    """Provide enough diverse unique-source candidates for the 60-article pilot."""
    rows = [
        _candidate(f"P{index:03d}", risk_tags=("multi_entity",) if index % 7 == 0 else ())
        for index in range(80)
    ]
    rows.extend(
        _candidate(
            f"N{index:03d}",
            category_tags=("irrelevant",),
            is_negative=True,
            risk_tags=("negative_boundary",),
            risk_weight=2.0,
        )
        for index in range(20)
    )
    for category in SamplingConfig.pilot().category_floors:
        rows.extend(
            _candidate(
                f"{category}-{index}",
                category_tags=(category,),
                risk_tags=("rare_category",),
                risk_weight=3.0,
            )
            for index in range(3)
        )
    return rows


def test_pilot_is_deterministic_and_has_negative_quota(candidates):
    """Produce the same 60 unique links in a fixed 18-percent-negative pilot regardless of input order."""
    config = SamplingConfig(seed=20260806, size=60, negative_fraction=0.18)

    first = select_sample(candidates, config)
    second = select_sample(list(reversed(candidates)), config)

    assert [row.sample_id for row in first] == [row.sample_id for row in second]
    assert sum(row.is_negative for row in first) == 11
    assert len({row.source_link for row in first}) == 60


def test_rare_categories_receive_configured_floor(candidates):
    """Reserve every configured rare-category floor before the weighted remainder is filled."""
    chosen = select_sample(candidates, SamplingConfig.pilot())
    counts = Counter(tag for row in chosen for tag in row.category_tags)

    for category, floor in SamplingConfig.pilot().category_floors.items():
        assert counts[category] >= floor


def test_selection_records_reasons_risk_tags_and_pilot_split_targets(candidates):
    """Keep inclusion audit data and assign the exact 36/17/7 Pilot split targets."""
    selected = select_sample(candidates, SamplingConfig.pilot())
    split_counts = Counter(row.dataset_split for row in selected)

    assert split_counts == {"development": 36, "regression": 17, "challenge": 7}
    assert all(row.inclusion_reasons for row in selected)
    assert all(isinstance(row.risk_tags, tuple) for row in selected)
    assert any("category_floor:储能" in row.inclusion_reasons for row in selected)


def test_final_split_targets_and_manifest_are_sorted(candidates):
    """Use the exact final split targets and a stable source-hash manifest at size 250."""
    expanded = candidates + [_candidate(f"X{index:03d}") for index in range(140)]
    selected = select_sample(expanded, SamplingConfig(seed=3, size=250, negative_fraction=0.08))
    manifest = build_manifest(selected, {"z.db": "z", "a.db": "a"})

    assert Counter(row.dataset_split for row in selected) == {
        "development": 150,
        "regression": 70,
        "challenge": 30,
    }
    assert manifest.seed == 3
    assert manifest.sample_ids == sorted(row.sample_id for row in selected)
    assert list(manifest.source_hashes) == ["a.db", "z.db"]


def test_source_link_deduplication_keeps_the_stable_lowest_sample_id():
    """Collapse equivalent source links before selection using the lexicographically first sample ID."""
    selected = select_sample(
        [
            _candidate("C"),
            _candidate("B"),
            _candidate("A", source_link="https://example.test/shared"),
            _candidate("Z", source_link="https://example.test/shared"),
        ],
        SamplingConfig(seed=1, size=3, negative_fraction=0, category_floors={}),
    )

    assert [row.sample_id for row in selected] == ["A", "B", "C"]


def test_zero_negative_quota_excludes_high_risk_negative_candidates():
    """Exclude all negative candidates from weighted fill when the requested negative quota is zero."""
    candidates = [_candidate(f"P{index}") for index in range(8)] + [
        _candidate(f"N{index}", is_negative=True, risk_weight=1_000_000.0) for index in range(4)
    ]

    selected = select_sample(
        candidates,
        SamplingConfig(seed=20260806, size=8, negative_fraction=0, category_floors={}),
    )

    assert sum(row.is_negative for row in selected) == 0


def test_weighted_fill_selects_the_extreme_risk_candidate_deterministically():
    """Select an extreme-risk remaining candidate reproducibly through the deterministic weighted fill."""
    candidates = [_candidate("low-a"), _candidate("low-b"), _candidate("high", risk_weight=1_000_000.0)]
    config = SamplingConfig(seed=17, size=1, negative_fraction=0, category_floors={})

    first = select_sample(candidates, config)
    second = select_sample(list(reversed(candidates)), config)

    assert [row.sample_id for row in first] == ["high"]
    assert [row.sample_id for row in first] == [row.sample_id for row in second]


def test_infeasible_negative_or_category_quotas_raise_stable_errors():
    """Reject unavailable negative and category quotas rather than silently weakening the sample design."""
    no_negative = [_candidate(f"P{index}") for index in range(10)]

    with pytest.raises(SamplingInfeasibleError, match="negative quota requires 2 candidates but only 0"):
        select_sample(
            no_negative,
            SamplingConfig(seed=1, size=10, negative_fraction=0.2, category_floors={}),
        )
    with pytest.raises(SamplingInfeasibleError, match="category floor '储能' requires 1 candidates but only 0"):
        select_sample(
            no_negative + [_candidate("N", is_negative=True)],
            SamplingConfig(seed=1, size=10, negative_fraction=0, category_floors={"储能": 1}),
        )
    with pytest.raises(SamplingInfeasibleError, match="category floors require more negative candidates"):
        select_sample(
            [_candidate("P"), _candidate("N", category_tags=("储能",), is_negative=True)],
            SamplingConfig(seed=1, size=2, negative_fraction=0, category_floors={"储能": 1}),
        )
