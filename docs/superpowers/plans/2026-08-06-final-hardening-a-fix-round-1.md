# Golden Standard Studio Final Hardening A Fix Round 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close all final-core review findings without weakening legacy eval ingestion or the Studio's source-data immutability boundary.

**Architecture:** Keep candidate ingestion permissive, but make formal ReviewStore/API/publication state strict and snapshot-backed. Recovery is explicit: migrations revoke legacy review authority, a configured read-only `SourceRepository` rehydrates the immutable snapshot, and a human must create new audited decisions before publication. All mutable filesystem boundaries claim and revalidate file identity before writes, while browser actions call the same transactional public API used by CLI publication.

**Tech Stack:** Python 3.11+, Pydantic v2, SQLite, FastAPI, Typer, vanilla JavaScript, pytest, Playwright.

## Global Constraints

- Use strict TDD: every production behavior begins with a focused failing regression test.
- Never infer authoritative article gold status from legacy projects, workflow status, or metadata.
- Source SQLite databases remain byte-for-byte immutable.
- Formal/Persisted/API contracts use `strict=True` and `extra="forbid"`; candidate contracts remain permissive.
- Update `docs/status`, `docs/changes/2026-W32.md`, and the final-core report.
- Preserve unrelated dirty files and stage only this fix round.

---

### Task 1: Migration recovery and strict formal contracts

**Files:**
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/review_store.py`
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/schemas.py`
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/cli.py`
- Test: `external-tools/gold-standard-studio/tests/test_final_hardening.py`

**Interfaces:**
- Produces: `ReviewStore.attach_article_snapshot(sample_id, snapshot, actor=...)` and CLI recovery support.
- Produces: migration audit action `migration_rereview_required`; migrated rows use `article_status=unreviewed` and `article_review_status=unreviewed`.

- [ ] Write data-bearing v1 migration tests for legacy positive/negative reviewed rows, missing snapshots, fail-closed opens, rehydration, audited re-review, and publish.
- [ ] Run focused tests and verify failures identify inferred status/snapshot recovery gaps.
- [ ] Implement ordered migration reset/audit and explicit snapshot attach using canonical `ArticleSnapshot` identity.
- [ ] Implement a configured CLI rehydration command/path through `SourceRepository`.
- [ ] Run focused migration/recovery tests to green.

### Task 2: Strict provenance, registry, evidence, role, and projection contracts

**Files:**
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/schemas.py`
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/field_registry.py`
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/review_store.py`
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/exporter.py`
- Modify: `eval/gold_loader.py`
- Test: `external-tools/gold-standard-studio/tests/test_final_hardening.py`
- Test: `external-tools/gold-standard-studio/tests/test_exporter.py`

**Interfaces:**
- Produces: strict formal models and mandatory Studio `SamplingProvenance` at publication.
- Produces: projection projects containing all registry field keys, with eligible `not_mentioned` values as JSON null.

- [ ] Write failing tests for strict ints/bools/offsets/revisions/seeds, missing provenance, caller `evidence_required` mismatch, Unknown role, missing named entity, and raw projection keys.
- [ ] Verify every new test fails for its intended boundary.
- [ ] Split permissive candidate models from strict formal models and derive evidence requirements from `FIELD_REGISTRY`.
- [ ] Require a named evidenced Chinese entity with a controlled non-Unknown role for valid records; retain `chinese_unnamed` as a no-project article outcome.
- [ ] Emit every projection field key with explicit null semantics and preserve provenance through the legacy-optional eval loader.
- [ ] Run focused schema/store/exporter tests to green.

### Task 3: Race-safe filesystem write boundaries

**Files:**
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/config.py`
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/review_store.py`
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/candidate_cache.py`
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/cli.py`
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/exporter.py`
- Test: `external-tools/gold-standard-studio/tests/test_final_hardening.py`

**Interfaces:**
- Produces: secured review SQLite connection acquisition that compares claimed handle, opened DB, current path, and forbidden source identities before any transaction.
- Produces: sibling-temp, fsync, recheck, atomic replacement helpers for config, manifest, cache, and report artifacts.

- [ ] Write a deterministic monkeypatch race that swaps review DB to a source hardlink between precheck and SQLite open and asserts source bytes remain unchanged.
- [ ] Verify the race test fails against the current precheck-only connection.
- [ ] Implement secured no-follow/exclusive claim/open and post-open identity verification with fail-closed cleanup.
- [ ] Implement atomic artifact writes and recheck targets immediately before commit.
- [ ] Run focused identity/race tests to green on Windows and keep portable guarded fallbacks.

### Task 4: Complete public API and browser review state machine

**Files:**
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/web/app.py`
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/web/static/review.js`
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/web/templates/review.html`
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/review_store.py`
- Test: `external-tools/gold-standard-studio/tests/test_web_api.py`
- Test: `external-tools/gold-standard-studio/tests/test_browser_smoke.py`
- Test: `external-tools/gold-standard-studio/tests/test_integration.py`

**Interfaces:**
- Consumes: strict API models and ReviewStore transaction gates from Tasks 1–3.
- Produces: dedicated adjudication calls, multi-span field/entity evidence editors, entity delete, controlled-role select, and atomic negative structure decision.

- [ ] Write failing API/browser tests for real adjudication routing, multiple evidence spans, entity add/edit/delete, controlled roles, and false-positive structural conflict negative confirmation.
- [ ] Verify failures are caused by current single-span/generic-save/non-atomic UI behavior.
- [ ] Implement dedicated adjudication state/action, repeatable evidence span editors, and entity removal/controlled-role UI.
- [ ] Make `confirm_negative` append an explicit empty `structure_decision` and confirmation in one transaction, resolving prior structure conflicts.
- [ ] Add a browser/API E2E that creates a project, resolves all registry fields, adjudicates one, edits entity evidence, confirms, and publishes through CLI/public boundaries.
- [ ] Run API/browser/integration tests to green.

### Task 5: Verification, warning hygiene, documentation, and commit

**Files:**
- Modify: `external-tools/gold-standard-studio/pyproject.toml`
- Modify: `docs/status/external-tools.md`
- Modify: `docs/changes/2026-W32.md`
- Modify: `.superpowers/sdd/2026-08-06-full-database-golden-standard-studio/final-core-report.md`

**Interfaces:**
- Produces: exact Starlette `TestClient` deprecation filter only and final audit evidence.

- [ ] Add the exact warning filter and prove unrelated warnings are not hidden.
- [ ] Run focused tests, full Studio tests including Chromium, eval loader tests, `node --check`, Python `compileall`, and `git diff --check`.
- [ ] Update status/change/report documents with commands, counts, migration/recovery semantics, and remaining upstream limitation if any.
- [ ] Stage only fix-round files, inspect the cached diff, commit, and leave the worktree/branch intact.
