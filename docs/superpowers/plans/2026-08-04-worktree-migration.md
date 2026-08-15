# Current Development Branch Worktree Migration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `claude/database-refactor-expansion-aa6198` into an isolated linked worktree while leaving the repository root checked out on `main` and preserving all current code, documentation, audit material, and database state.

**Architecture:** The repository root becomes the stable `main` checkout. The existing ignored `.claude/worktrees/` directory hosts a new linked worktree for the current development branch. Git-managed dirty files move through a named stash; ignored runtime data, including the SQLite database and audit CSV files, are copied explicitly and verified by hashes.

**Tech Stack:** Git worktrees, PowerShell, SQLite database files, Python project test suites.

## Global Constraints

- Do not modify `main` history or commit development changes onto `main`.
- Preserve all existing user changes, including untracked and ignored audit files.
- Keep the root database in place and copy it to the new worktree; future development database changes occur only in the new worktree copy.
- Database schema and data transformations must later be represented by committed, idempotent migration/backfill code before applying them to the root database.
- After project code changes, update `docs/status/` and `docs/changes/` as required by `AGENTS.md`.

---

### Task 1: Capture migration baseline

**Files:**
- Inspect: repository metadata and `s02a_duplicate_merger/data/db/bri_re_database.db`

- [x] Record current branch, HEAD, dirty paths, linked worktrees, database size, timestamp, and SHA-256.
- [x] Record ignored files under `supporting_docs/storage_audit_2026-W30/` that need explicit copying.

### Task 2: Preserve the dirty working tree

**Files:**
- Preserve: all tracked and untracked paths reported by `git status --untracked-files=all`

- [x] Create a named stash with `--include-untracked` after this plan is present.
- [x] Verify the root checkout is clean and the stash contains every dirty Git-managed path.

### Task 3: Reassign branches to worktrees

**Files:**
- Root checkout: `E:/Projects/BRI_RE_Database_v2`
- Create worktree: `E:/Projects/BRI_RE_Database_v2/.claude/worktrees/database-refactor-expansion-aa6198`

- [x] Switch the clean root checkout to `main` without changing `main` history.
- [x] Add a linked worktree for the existing branch `claude/database-refactor-expansion-aa6198`.
- [x] Verify root=`main` and new worktree=`claude/database-refactor-expansion-aa6198`.

### Task 4: Restore code and copy runtime data

**Files:**
- Restore in new worktree: stashed tracked/untracked paths.
- Copy explicitly: `s02a_duplicate_merger/data/db/bri_re_database.db` and all files in `supporting_docs/storage_audit_2026-W30/`.

- [x] Apply the named stash in the new worktree and verify the original dirty paths reappear there only.
- [x] Create required ignored destination directories in the new worktree.
- [x] Copy the database and ignored audit CSV files without deleting root copies.
- [x] Verify source/destination size and SHA-256 for the database and copied audit files.

### Task 5: Verify isolation and baseline

**Files:**
- Inspect both worktrees and copied database files.

- [x] Confirm the root tracked working tree is clean on `main`.
- [x] Confirm the development worktree contains the intended dirty files on the development branch.
- [x] Confirm the two database paths are distinct files with identical starting hashes.
- [x] Run focused s01b tests covering the currently modified extraction area without changing source files.
- [x] Report any pre-existing test failure rather than modifying implementation during migration.

### Task 6: Document the workspace layout

**Files:**
- Modify: `docs/status/overview.md`
- Modify: `docs/changes/2026-W31.md`

- [x] Record the stable-root/development-worktree layout and the rule that SQLite files do not merge through Git.
- [x] Record the migration outcome and verification evidence in the change log.
- [x] Re-run Git status checks in both worktrees.
