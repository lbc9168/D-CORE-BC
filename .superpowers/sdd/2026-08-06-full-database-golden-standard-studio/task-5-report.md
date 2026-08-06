# Task 5 — Review Store, Audit Trail, and Backup Report

## Scope

Implemented the isolated SQLite review store in `external-tools/gold-standard-studio` and updated the Studio status/change records.

## RED / GREEN evidence

- RED 1: `uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_review_store.py -q` failed during collection with `ModuleNotFoundError: gold_standard_studio.review_store` before production code existed.
- GREEN 1: after the initial store implementation, the focused test file passed `8 passed in 0.30s`.
- RED 2: the added deleted-project-ID regression failed with `['P01', 'P02'] != ['P01', 'P03']` before fresh-ID allocation was added.
- GREEN 2: the same focused command passed `9 passed in 0.30s` after the allocation fix.
- RED 3: after review-driven connection cleanup, the initialization regression failed because `studio_meta.schema_version` was absent after reopen.
- GREEN 3: restoring success-path commit and exception rollback made the focused suite pass `10 passed in 0.34s`.
- RED 4: audit-fidelity regressions failed because `AuditEntry` exposed no complete old/new payload and candidate-bundle updates wrote `old_json = NULL`.
- GREEN 4: canonical prior-bundle capture plus full decoded `old_payload/new_payload` made the focused suite pass `12 passed in 0.95s`.

## Delivered files

- `external-tools/gold-standard-studio/src/gold_standard_studio/review_store.py`
- `external-tools/gold-standard-studio/tests/test_review_store.py`
- `docs/status/external-tools.md`
- `docs/changes/2026-W32.md`

## Verification

- Focused review-store tests cover atomic field/audit writes, failed-write rollback, unresolved-conflict confirmation guard, canonical candidate JSON, structure revisions and stale revisions, deleted-ID non-reuse, session resume, deterministic queueing, and SQLite backup integrity/tail preservation.
- Full Studio suite: `uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests -q` passed `43 passed in 1.68s` after the audit-fidelity fix.
- `git diff --check` and `git diff --cached --check` completed with no whitespace errors; the staged path list contains only the five Task 5 deliverables.

## Self-review

- Each production module, class, method, and helper has a one-sentence docstring.
- SQL values use bound parameters; nested field paths are validated before selecting a project and field.
- Mutations run under `BEGIN IMMEDIATE` and the transaction manager rolls back exceptions.
- The backup API compares `PRAGMA integrity_check`, all store-table row counts, and the final audit ID.
- Review follow-up fixed backup validation to collect metadata and copy under one source read snapshot, then retain the temporary SQLite file only after all checks succeed.
- The connection helper now commits successful implicit transactions and rolls back exceptions before closing, preserving idempotent initialization metadata as well as write atomicity.
- Audit entries now preserve complete decoded GoldField and CandidateBundle payloads while retaining compact `old_value/new_value` display access; generic structure and confirmation payloads remain faithful decoded JSON.

## Concerns

- Task 8's HTTP layer is not part of Task 5; it will need to translate store `ValueError` cases to API status responses.
- Backup retention and scheduled backup cadence are intentionally deferred to the later CLI/operator workflow.
