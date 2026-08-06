# Final Hardening A — Fix Round 2 Report

Date: 2026-08-06

Worktree: `.claude/worktrees/database-refactor-expansion-aa6198`

Branch: `claude/database-refactor-expansion-aa6198`

## Outcome

The second final-hardening review is closed. Backup and release destinations now reuse held-handle or identity-checked write primitives, legacy sampling data remains explicit but unverified until an audited operator repair, every HTTP mutation is protected by local Host/intent/actor/origin checks, and one real Chromium workflow produces a complete positive record that the public CLI publishes successfully.

## Review finding closure

1. **Backup destination identity:** `ReviewStore.backup()` acquires its temporary destination with `_secured_sqlite_connect`, holding the no-follow claim while SQLite opens the same inode and rejecting configured source or live review DB identities before `Connection.backup()` writes pages. The race regression swaps the selected temp to a source hardlink and verifies failure, unchanged source SHA-256, no target, and no remaining selected temp.
2. **Six secure release artifacts:** rich JSONL, eval projection, rich schema, distribution, changelog, and manifest all use `atomic_write_text` with the configured source DBs and review DB as forbidden identities. Root, final, staging, and artifact paths are checked before writes and again before the no-replace commit. Twelve hardlink/symlink race cases prove no source change, visible release, or staging residue.
3. **No invented provenance:** ReviewStore schema v4 migrates every pre-verification provenance payload to `verified=false` and `verification_source=legacy_unknown`, keeps the original JSON only as an audit hint, revokes review status, and appends repair/re-review audit events. Missing and malformed legacy values remain loadable without claiming factual seed, strata, risk, or negative status.
4. **Audited provenance repair:** strict schema invariants distinguish `sample_selection`, `operator_review`, and `legacy_unknown`. Store, `PUT /api/articles/{sample_id}/sampling-provenance`, and `gold-studio verify-provenance` accept a complete operator-reviewed replacement and append `sampling_provenance_decision`; positive/negative confirmation and publication reject absent or unverified provenance with an actionable command message. The canonical eval loader retains backward compatibility by making the two verification fields optional for old gold.
5. **Local mutation boundary:** TrustedHost accepts only `localhost`, `127.0.0.1`, `[::1]`, and `testserver`; a narrow subclass corrects Starlette's colon split for bracketed IPv6 while retaining its exact allowlist semantics. Every mutation requires `X-Gold-Studio-Request: 1`, a nonblank single-line actor of at most 80 characters, and an exactly matching scheme/host/effective port when `Origin` is present. Headerless form, missing/oversized actor, cross-origin, and rebinding requests are rejected before any draft/audit mutation.
6. **Real public UI publication:** the Chromium E2E seeds only an immutable empty article shell. Page actions create P01, resolve all 19 registry fields, exercise dedicated adjudication with two exact category spans, add a named Chinese entity with controlled role and two evidence spans, save `valid`, and confirm. The public `publish` CLI then emits a rich record and eval projection whose full registry, terminal states, evidence, entity/role, verified provenance, and review status are asserted.

## TDD evidence

- Backup RED: the source-hardlink destination swap reached raw `sqlite3.connect` and did not raise; the secured connection made the test green.
- Exporter RED: no artifact commit seam was reached because release files used raw path writes; all six artifact names now fail closed under both hardlink and symlink swaps.
- Provenance RED: missing migration sampling loaded as `None`, malformed sampling failed Pydantic loading, verified/source fields were absent, and `verify-provenance` did not exist. Schema v4 plus audited repair made the migration, Store, CLI, confirmation, and publication tests green.
- HTTP RED: provenance returned 404 and existing mutations accepted requests without the intended security contract. The request middleware, TrustedHost policy, API route, bootstrap value, and JS headers made state-preservation tests and browser writes green.
- Chromium E2E passed through the public page and CLI without direct ReviewStore review-decision calls.

## Final verification

- Full Studio suite including real Chromium: `212 passed in 29.42s`.
- Eval loader and legacy CSV compatibility: `15 passed in 0.70s`; the only warning is the pre-existing Windows pytest cache ACL warning.
- JavaScript syntax: `node --check external-tools/gold-standard-studio/src/gold_standard_studio/web/static/review.js` passed.
- Python syntax: `python -m compileall -q external-tools/gold-standard-studio/src external-tools/gold-standard-studio/tests eval/gold_loader.py` passed in the Studio environment.
- Diff hygiene: `git diff --check` passed.
- The full Studio run emitted no warnings.

## Preserved unrelated worktree state

Pre-existing edits/untracked files outside this fix round were not modified or staged, including `docs/status/overview.md`, `docs/changes/2026-W30c.md`, `docs/changes/2026-W31.md`, `docs/superpowers/plans/2026-08-04-worktree-migration.md`, and `supporting_docs/storage_audit_2026-W30/`.
