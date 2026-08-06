# Final Core Hardening Report (A1–A6)

Date: 2026-08-06

Worktree: `.claude/worktrees/database-refactor-expansion-aa6198`

Branch: `claude/database-refactor-expansion-aa6198`

## Outcome

The Golden Standard Studio final core hardening is implemented. Publication now depends on an explicit audited article gold decision, a complete state machine, exact registry/type/evidence/entity gates, immutable canonical article snapshots, source/mutable identity isolation, and ordered transactional ReviewStore migrations.

## Acceptance mapping

### A1 — Authoritative article gold status + explicit sampling provenance

- Added `article_status` independently of workflow `article_review_status`.
- Added audited article status decisions and publication proof checks.
- Removed project-count/audit-metadata status inference from projection.
- Added explicit `sampling_provenance` to rich gold and eval projection.
- Enabled `extra="forbid"` across persisted/public Pydantic contracts.

### A2 — Complete valid-project state machine + genuine valid-project E2E

- `valid` requires one or more projects; all negative statuses require none.
- Every valid project must contain the exact 19-field registry and no unresolved state.
- Added atomic negative confirmation for all three negative statuses.
- Reworked the Pilot E2E fixture to publish one evidence-backed full-field valid project with a Chinese entity/role, plus all three negative statuses.

### A3 — Strict exact registry/types/evidence/entity-role confirmation gates

- Added strict field types and controlled values from canonical enums.
- Added controlled entity-role validation.
- Revalidated evidence hash, bounds, and exact quote at confirmation and publication.
- Added Web/API editing for article status and project entities/roles/evidence.

### A4 — Source/mutable path identity isolation including links/hardlinks

- Added reusable resolved-path, `samefile`, and device/inode checks.
- Applied checks to Studio config, review DB, manifest/cache/report locations, and publication output.
- Added direct, symlink, and hardlink alias regression coverage.

### A5 — Canonical ArticleSnapshot evidence relocation identity

- Evidence location/relocation now takes the complete `ArticleSnapshot`.
- Canonical identity hashes title, content, and publish date.
- ReviewStore persists immutable snapshots; confirm, UI, and publish read the stored snapshot.

### A6 — Ordered transactional ReviewStore schema migrations

- Advanced the schema to v3 through ordered v1→v2→v3 migrations in one transaction.
- Added immutable snapshot storage, indexed article status, and sampling-provenance migration.
- Missing, invalid, and future schema metadata fail closed.
- Backup row-count verification includes the new snapshot table.

## TDD and verification evidence

- RED: the new hardening suite initially failed during collection because `SamplingProvenance` did not exist.
- GREEN: `external-tools/gold-standard-studio/tests/test_final_hardening.py` passed all 12 focused cases after implementation.
- Full Studio suite: `173 passed, 1 warning` in 19.05s. The warning is Starlette's upstream `httpx` deprecation notice.
- Eval loader compatibility: `14 passed, 1 warning` in 0.72s. The warning is an existing pytest cache ACL warning in this Windows worktree.
- JavaScript syntax: `node --check external-tools/gold-standard-studio/src/gold_standard_studio/web/static/review.js` passed.
- Python syntax: `python -m compileall -q external-tools/gold-standard-studio/src eval/gold_loader.py` passed in the Studio environment.
- Diff hygiene: `git diff --check` passed.

The repository root is a multi-project tree without one environment that installs every subproject. A diagnostic root `uv run pytest -q` therefore failed during collection on missing independent project packages/dependencies (including the Studio package itself); no tests executed. Verification was consequently run in the Studio project environment plus the affected root eval-loader tests in the root environment.

## Documentation

- Updated `docs/status/external-tools.md` with the final hardened state.
- Updated `docs/changes/2026-W32.md` with the A1–A6 contract and verification record.
