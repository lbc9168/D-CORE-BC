# Task 11 report

## Delivered

- Preserved the committed strict gold-loader and energy-taxonomy behavior.
- Added eligibility-aware scalar and set scoring for versioned Studio projections while retaining old-gold defaults.
- Flattened all reviewed extractor fields and added deterministic split/category/risk slices plus separate regression and challenge core metrics.
- Kept `weighted_total` as the legacy headline and exposed its label explicitly.
- Fix round: made eligible rich nulls explicit empty values across every metric family, added location scoring, predeclared empty slices, and made the Studio exporter import the shared loader without repository-path test masking.
- Fix round 2: added registry-primary metric dispatch so source-set F1 drives core aggregation, while matching Studio's Unicode/punctuation text normalization for location.

## Verification status

- `uv run pytest tests/test_gold_loader.py tests/test_eval_metrics.py tests/test_predict.py tests/test_run_eval_e2e.py -q`: 45 passed.
- `uv run pytest tests -q`: 304 passed, 1 skipped.
- `uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_exporter.py -q`: 21 passed.
- `uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests -q`: 146 passed.
- `uv run --project . python -c "import gold_standard_studio.exporter"` from the Studio directory: passed.
- `git diff --check`: no whitespace errors.

The direct sandboxed Python launchers return Windows `Access is denied`; the required suites were run through the approved uv execution path instead. Pytest could not update the pre-existing worktree `.pytest_cache`, producing non-failing cache warnings in the root runs.

## Scope

Only Task 11 eval modules, their focused tests, this status/changelog update, and this report are intended for staging. Existing unrelated worktree changes remain untouched.
