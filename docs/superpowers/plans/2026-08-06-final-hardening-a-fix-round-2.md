# Golden Standard Studio Final Hardening A Fix Round 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining backup, staged-publication, provenance-verification, local-request security, and real-browser publication findings without weakening source-database immutability or legacy eval loading.

**Status:** Completed and verified on 2026-08-06; see the round-2 final-hardening report for exact command evidence.

**Architecture:** Extend the existing held-handle/identity-checked filesystem primitives to every SQLite backup destination and every staged release artifact. Represent migrated provenance as explicit but unverified data that can be repaired only by an audited operator decision, then protect every HTTP mutation with strict Host, same-origin-if-present, custom-header, and actor gates. Exercise the whole formal workflow through a real Chromium page and the public publish CLI.

**Tech Stack:** Python 3.11+, SQLite, Pydantic v2, FastAPI/Starlette, Typer, vanilla JavaScript, pytest, Playwright Chromium.

## Global Constraints

- Every production change follows RED → observed expected failure → minimal GREEN → regression run.
- Source SQLite bytes must remain unchanged across backup and publish race failures.
- Formal Studio confirmation and publication require operator-verifiable sampling provenance; legacy eval JSON remains loadable.
- HTTP remains loopback-only and has no CORS support.
- Every function and major module keeps a one-sentence purpose comment/docstring.
- Update `docs/status/external-tools.md`, `docs/changes/2026-W32.md`, README, and the final-hardening report.
- Preserve and never stage unrelated dirty worktree files.

---

### Task 1: Secured SQLite backup destination

**Files:**
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/review_store.py`
- Test: `external-tools/gold-standard-studio/tests/test_final_hardening_fix_round_2.py`

**Interfaces:**
- Consumes: `_secured_sqlite_connect(path, source_paths, allow_create)`.
- Produces: backup destination connection acquired and identity-verified before `sqlite3.Connection.backup()` writes any page.

- [ ] Add a deterministic test whose `_before_sqlite_open` hook replaces the selected backup temporary path with a hardlink to a configured source DB; assert `backup()` raises, source SHA-256 is unchanged, target is absent, and no backup temp remains.
- [ ] Run the single test and observe it fail because `backup()` calls raw `sqlite3.connect(temporary)`.
- [ ] Replace raw destination connect with `_secured_sqlite_connect`, treating configured sources and the live review DB as forbidden identities, and close both connection and claim on every path.
- [ ] Run the backup race plus existing backup tests to green.

### Task 2: Secured atomic release staging

**Files:**
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/exporter.py`
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/config.py`
- Test: `external-tools/gold-standard-studio/tests/test_final_hardening_fix_round_2.py`

**Interfaces:**
- Consumes: `atomic_write_text(path, content, source_paths=...)`.
- Produces: every rich/projection/schema/distribution/changelog/manifest staging write uses exclusive no-follow temporary creation and pre-replace identity checks; output/staging paths are rechecked against source identities before release commit.

- [ ] Add parameterized source-hardlink and source-symlink artifact-race tests using `_before_atomic_replace`; assert publish fails, source hash is unchanged, final version is absent, and staging is cleaned.
- [ ] Run the tests and observe raw `_write_text(... path.open("w"))` either truncates/follows the swapped artifact or fails without the required secure contract.
- [ ] Thread `store.source_paths` through every exporter writer, replace `_write_text` with `atomic_write_text`, and check root/staging/artifact/final paths before writes and before no-replace directory commit.
- [ ] Run focused exporter race and immutable-release tests to green.

### Task 3: Explicit unverified legacy provenance and audited repair

**Files:**
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/schemas.py`
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/review_store.py`
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/web/app.py`
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/cli.py`
- Modify: `eval/gold_loader.py`
- Test: `external-tools/gold-standard-studio/tests/test_final_hardening_fix_round_2.py`
- Test: `external-tools/gold-standard-studio/tests/test_cli.py`
- Test: `tests/test_gold_loader.py`

**Interfaces:**
- Produces: `SamplingProvenance.verified: bool` and `verification_source: Literal["sample_selection", "operator_review", "legacy_unknown"]`.
- Produces: `ReviewStore.replace_sampling_provenance(sample_id, provenance, actor=...)`, `PUT /api/articles/{sample_id}/sampling-provenance`, and `gold-studio verify-provenance`.

- [ ] Add data-bearing v1 tests for missing and malformed legacy sampling; after migration each row must load with `verified=false`, `verification_source="legacy_unknown"`, and a repair-required audit.
- [ ] Add confirmation/publish rejection tests requiring an actionable provenance-verification message.
- [ ] Add API/CLI repair tests that submit literal operator-reviewed provenance, assert the audited replacement, then re-review and publish successfully.
- [ ] Run the provenance tests and observe migration validation/invented seed and missing operation failures.
- [ ] Implement strict verification-state invariants, sanitizing migration to loadable unverified placeholders without claiming truth; add audited store/API/CLI replacement and formal confirmation/publication gates.
- [ ] Keep eval-loader provenance fields optional for old gold while round-tripping the new fields exactly, then run Studio and root loader tests to green.

### Task 4: Trusted Host, same-origin, custom header, and mandatory actor

**Files:**
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/web/app.py`
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/web/templates/review.html`
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/web/static/review.js`
- Test: `external-tools/gold-standard-studio/tests/test_web_api.py`
- Test: `external-tools/gold-standard-studio/tests/test_browser_smoke.py`

**Interfaces:**
- Consumes: fixed bootstrap value `requestHeader="1"`.
- Produces: `TrustedHostMiddleware` allowlist for `localhost`, `127.0.0.1`, `[::1]`, and `testserver`; mutations require `X-Gold-Studio-Request: 1`, bounded nonblank `X-Review-Actor`, and a same-origin `Origin` when supplied.

- [ ] Add table-driven mutation tests for start, structure, field, adjudication, status, confirmation, and provenance routes with missing custom header, missing actor, cross-origin Origin, and rebinding Host; assert rejection before any audit/state change.
- [ ] Run them and observe current routes accept missing headers/default actors and arbitrary Host/Origin.
- [ ] Add TrustedHost plus local-mutation middleware and require explicit actor values; emit the request token in bootstrap and send it with every browser mutation.
- [ ] Update TestClient fixtures to supply the real local headers, run API tests to green, and verify browser writes still succeed.

### Task 5: Real Chromium positive review → public CLI publish

**Files:**
- Modify: `external-tools/gold-standard-studio/tests/test_browser_smoke.py`
- Modify: `external-tools/gold-standard-studio/src/gold_standard_studio/web/static/review.js` only if the real flow exposes a UI defect.
- Modify: required docs/report files.

**Interfaces:**
- Consumes: the public review page/API and `gold-studio publish` CLI.
- Produces: one genuine browser E2E with no direct ReviewStore review-decision calls.

- [ ] Add a Chromium test that seeds only the immutable article shell, creates a project in the UI, resolves all 19 registry fields through page actions, uses dedicated adjudication with two evidence spans, creates a controlled-role entity with two spans, saves valid status, confirms, and invokes public CLI publish.
- [ ] Run it and observe the first missing selector/security/workflow failure.
- [ ] Make only the minimal UI change needed by the failing step, rerunning the same test after each RED→GREEN cycle.
- [ ] Validate the released rich record and projection contain verified provenance, all registry keys, exact entity/role, explicit nulls, and confirmation state.
- [ ] Run focused tests, full Studio including Chromium, root eval compatibility, `node --check`, Python `compileall`, and `git diff --check`; update status/change/runbook/report, stage only round-2 files, inspect cached diff, and commit.
