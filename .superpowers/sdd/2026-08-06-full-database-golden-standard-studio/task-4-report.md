# Task 4 — Deterministic Stratified Pilot Sampling Report

## Scope

Implemented `gold_standard_studio.sampling` for deterministic source-link-deduplicated selection of Studio review articles. The module exposes immutable `SampleCandidate`, `SamplingConfig`, `SelectedSample`, `SamplingInfeasibleError`, `select_sample`, and `build_manifest` contracts.

## RED

Created `external-tools/gold-standard-studio/tests/test_sampling.py` before production code. The first focused invocation could not start the sandboxed `uv` environment because its executable was denied; rerunning the same command with the local environment permitted reached the intended RED state:

```text
ModuleNotFoundError: No module named 'gold_standard_studio.sampling'
```

The failing tests specified deterministic ordering independent of input order, unique source links, an exact 11-of-60 negative quota, rare-category floors, 36/17/7 Pilot splits, 150/70/30 final splits, stable manifests, source-link deduplication, and explicit infeasibility errors.

## GREEN

Added `sampling.py` with these deterministic stages:

1. Normalize immutable candidate tags and deduplicate by trimmed source link, retaining the lexicographically lowest sample ID.
2. Validate that the requested size, negative quota, and every category floor are available; invalid and mutually incompatible quotas raise `SamplingInfeasibleError` rather than degrading the sample.
3. Satisfy category floors while preferring positive candidates to preserve the exact negative quota, then fill that quota.
4. Fill remaining capacity with SHA-256-seeded Efraimidis-style risk-weighted reservoir keys.
5. Put the highest-risk selected articles in Challenge, allocate the remaining regression/development articles deterministically, and attach every selected sample's inclusion reasons, risk tags, split, and seed.
6. Build a sorted `DatasetManifest` from a single-seed sample and sorted source hashes.

## Test evidence

Focused command (after the RED run):

```powershell
$env:UV_CACHE_DIR = (Resolve-Path '.uv-cache')
uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_sampling.py -q
```

Result: `6 passed in 0.10s`.

## Self-review and concerns

- The current public `DatasetManifest` schema records sample IDs, source hashes, and the common seed. Per-sample selection reasons and splits deliberately remain on `SelectedSample`, ready for the later review-store/exporter tasks to persist with each record.
- Category floors are treated as exact hard constraints, including their interaction with the exact negative quota. A pool that can only satisfy a zero-negative configuration by selecting negative category-floor rows is explicitly rejected.
- Challenge ranking concentrates high `risk_weight` and rich risk tags, while hard category floors currently apply to the complete selected sample rather than each split; the design specifies overall floor coverage, not split-level floor quotas.

## Commit

Commit message: `feat(gold-studio): add deterministic pilot sampling`.
