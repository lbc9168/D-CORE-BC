# Final Hardening A — Fix Round 1 Report

Date: 2026-08-06

Worktree: `.claude/worktrees/database-refactor-expansion-aa6198`

Branch: `claude/database-refactor-expansion-aa6198`

## Outcome

All ten final-core review findings are closed. Legacy migrations no longer confer review authority, snapshot recovery is explicit and source-backed, review/artifact paths are race-checked, formal publication contracts are strict and provenance-complete, and the browser exposes the same adjudication/evidence/entity state machine enforced by the public API and CLI publisher.

## Review finding closure

1. **Migration recovery:** v1/v2 reviewed/status values become non-authoritative `legacy_migration_hint`; payload/index workflow and gold status reset to `unreviewed`, with `migration_rereview_required` audit rows. `rehydrate-snapshots` restores only from configured read-only `SourceRepository`; data-bearing positive and negative v1 rows must be freshly reviewed before publish. Missing, corrupt, old, and future schema metadata fail closed on ordinary opens.
2. **Race-safe isolation:** every review SQLite connection claims a held no-follow handle, then compares claim, current path, SQLite's opened path, and forbidden source identities before any transaction. Artifact writes use sibling exclusive temporary files, flush/fsync, parent/destination/temp identity rechecks, and atomic replace. Deterministic path-swap tests preserve source bytes.
3. **Publish provenance:** Studio confirmation/publication requires `sampling_provenance`; rich gold and eval projection preserve it exactly. The canonical eval loader remains optional for legacy gold only.
4. **Complete UI/API:** the browser uses the dedicated adjudication endpoint and supports repeatable field evidence, repeatable entity evidence, entity add/edit/delete, and registry-provided controlled roles. A public API→CLI E2E saves all 19 fields, adjudicates, saves an entity, confirms, and publishes.
5. **Atomic negatives:** one-click negative confirmation records an explicit empty structure revision, resolves candidate structure conflicts, records status, and confirms in one SQLite transaction.
6. **Valid/entity semantics:** `valid` requires at least one named Chinese entity, a controlled non-`Unknown` role, and exact evidence. `chinese_unnamed` is documented as a zero-project outcome for indicated but unnamed Chinese participation, not a valid-record placeholder.
7. **Strict boundaries:** persisted/formal/API models use `strict=True` and `extra="forbid"`, rejecting coerced offsets, revisions, seeds, strings, and booleans. Candidate envelopes remain observational and permissive.
8. **Complete projection:** every project emits every registry field key. `not_mentioned` is explicit JSON null with eligibility true.
9. **Registry evidence policy:** `evidence_required` is derived from and checked against `FIELD_REGISTRY` during structure replacement, field save, adjudication, confirmation, and publication; mismatches are rejected through the API. Multiple exact spans are validated independently.
10. **Warning/docs/report:** pytest filters only the exact upstream Starlette `httpx`/`httpx2` deprecation. Status, changelog, runbook, implementation plan, and this report record the final behavior and evidence.

## TDD evidence

- Initial fix-round regression baseline: 11 focused failures covering migration authority/recovery, path races, strict contracts, provenance/entity gates, projection nulls, and atomic negative confirmation.
- CLI recovery began with `No such command 'rehydrate-snapshots'` and passed after the explicit recovery implementation.
- Browser regression initially failed because field edits used the generic save route, evidence editors supported one span, and entity roles were free text; the real Chromium flow now passes through dedicated adjudication and repeatable controlled editors.
- Strict-contract activation exposed 49 legacy fixture failures; fixtures were upgraded with real provenance, registry evidence flags, named controlled entities, and exact evidence without weakening production guards.
- Additional hardening covers corrupt/future ordinary opens and a non-source destination identity swap during atomic artifact commit.

## Final verification

- Full Studio suite including real Chromium: `191 passed in 25.65s`.
- Eval loader and legacy CSV compatibility: `14 passed in 0.76s`.
- Focused fix-round regressions: `16 passed in 1.15s`.
- JavaScript syntax: `node --check external-tools/gold-standard-studio/src/gold_standard_studio/web/static/review.js` passed.
- Python syntax: `python -m compileall -q external-tools/gold-standard-studio/src external-tools/gold-standard-studio/tests` passed.
- Diff hygiene: `git diff --check` passed.
- The Studio run emitted no pytest warnings; only the exact known Starlette compatibility warning is filtered.

## Preserved unrelated worktree state

Pre-existing edits/untracked files outside this fix round were not modified or staged, including `docs/status/overview.md`, `docs/changes/2026-W30c.md`, `docs/changes/2026-W31.md`, `docs/superpowers/plans/2026-08-04-worktree-migration.md`, and `supporting_docs/storage_audit_2026-W30/`.
