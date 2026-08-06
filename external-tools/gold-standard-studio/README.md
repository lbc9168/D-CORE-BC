# Golden Standard Studio

Golden Standard Studio is a local, single-user review workspace for building an evidence-backed gold set. It opens `raw_news.db`, `projects.db`, and `bri_re_database.db` only through SQLite read-only connections. Its mutable files live only under the explicit `--data-dir`.

## Setup and inputs

Run commands from the repository root with Python 3.11+ and `uv`. The normal inputs are `s01a_news_crawler/data/raw_news.db`, `s01b_project_extractor/data/projects.db`, and `s02a_duplicate_merger/data/db/bri_re_database.db`.

```powershell
uv run --project external-tools/gold-standard-studio gold-studio init `
  --raw-news-db s01a_news_crawler/data/raw_news.db `
  --projects-db s01b_project_extractor/data/projects.db `
  --main-db s02a_duplicate_merger/data/db/bri_re_database.db `
  --data-dir data/gold-studio
```

`init` saves only explicit source paths and a Studio-owned review DB. It never changes raw news, `imported_to_s02a`, canonical/event IDs, or production fields.

## Pilot runbook

First run the default, no-write proposal. It reports selection count, negative proxy count, category/risk distribution, and source logical hashes before/after.

```powershell
uv run --project external-tools/gold-standard-studio gold-studio sample --data-dir data/gold-studio
```

For a mechanically reproducible record suitable for handoff, use `--format json`. It includes before/after logical hashes, optional main-file hashes, raw/canonical/event row counts and ID-set digests, exact selection/status/category/risk counts, every floor, and unchanged booleans. Logical hashes are authoritative because SQLite WAL content can be committed without changing the main database file; physical hashes are diagnostics only.

JSON mode writes exactly one JSON document to stdout in both modes. Dry-run sets `applied: false`; `--apply` sets `applied: true` and adds `seeded_count`, the safe local `review_db` name, and manifest name in the same document. Do not parse the human-readable default output as JSON.

```powershell
uv run --project external-tools/gold-standard-studio gold-studio sample --data-dir data/gold-studio --format json
```

The Pilot is fixed at 60 articles and an exact 18% negative quota (11/60); it also enforces floors for 储能, 制造业, 输变电建设, 核电, 火电, 地热及余热, and 生物质. The current production category `新能源制造业` is explicitly retained in audit data and additionally mapped to the design stratum `制造业`; this is a taxonomy alias, not a lowered quota. If any quota is otherwise infeasible, the command fails rather than weakening it. An unlabeled current-source article is only a negative sampling proxy, never an automatic gold status.

After inspection, explicitly create local review shells and a manifest:

```powershell
uv run --project external-tools/gold-standard-studio gold-studio sample --apply --data-dir data/gold-studio
```

Candidate generation displays the exact scope. `--all` asks for confirmation unless `--yes` is given; `--resume` processes only samples with no candidate bundle.

```powershell
uv run --project external-tools/gold-standard-studio gold-studio build-candidates --all --data-dir data/gold-studio
uv run --project external-tools/gold-standard-studio gold-studio build-candidates --all --resume --yes --data-dir data/gold-studio
```

The shipped CLI deliberately makes zero external model calls: production and review providers are recorded as `model_calls_disabled`. Before any future provider integration, obtain separate approval, review the exact count/provider/prompt/model version, and record budget, data-retention, and article-disclosure risk.

An explicit `init` of a legacy v1/v2 review DB migrates its data but revokes every legacy reviewed/status claim: prior values survive only as `legacy_migration_hint`, and each row receives a `migration_rereview_required` audit event. Recover missing immutable snapshots only from the configured read-only source repository, then perform a fresh review and confirmation:

```powershell
uv run --project external-tools/gold-standard-studio gold-studio rehydrate-snapshots --all --data-dir data/gold-studio
# Or recover one row: --sample-id S001
```

Ordinary opens reject missing, corrupt, old, or future schema metadata; they never infer authority or auto-migrate. `valid` requires at least one named Chinese entity, a controlled non-`Unknown` role, and exact evidence. `chinese_unnamed` means the article indicates Chinese participation but does not name the Chinese entity; it is published as a zero-project negative state, not as a placeholder entity in a valid record.

Serve the review UI on loopback:

```powershell
uv run --project external-tools/gold-standard-studio gold-studio serve --data-dir data/gold-studio
# Open http://127.0.0.1:8765/queue
```

Non-loopback hosts require `--allow-network`; do not expose article snapshots or a review DB without approved access control.

## Backup, report, and publication

The UI immediately saves local decisions and audit events. Before maintenance, call `ReviewStore.backup()` from a local maintenance script/Python shell; it verifies SQLite integrity, all table counts, and the audit tail. Stop the server before restoring a verified backup into the Studio-owned `review.db`; never restore over a production source DB.

```powershell
uv run --project external-tools/gold-standard-studio gold-studio report `
  --data-dir data/gold-studio --out data/gold-studio/reports/pilot.html
```

The report is accessible, self-contained HTML: inline styling, semantic table/captions, and no scripts or remote assets.

Publication is separate from Pilot dry-run. It requires every article to be human-confirmed with explicit `sampling_provenance` and rejects unreviewed drafts, adjudications, structural conflicts, missing required evidence, or an existing target version.

```powershell
uv run --project external-tools/gold-standard-studio gold-studio publish `
  --version v0.1.0 --output eval/gold_sets/database_core --data-dir data/gold-studio
```

This writes immutable artifacts to `eval/gold_sets/database_core/v0.1.0/`: rich gold, eval projection, manifest, schema, distribution, and changelog. Do not manually alter a released file. Evaluate a released projection with the standard eval suites.

## Troubleshooting and acceptance

| Symptom | Action |
| --- | --- |
| `Pilot quotas are infeasible` | Preserve the error and pool counts; inspect coverage/category spelling. Never reduce a floor or negative quota silently. |
| `source paths are required` | Run `init` with all three paths, or pass all three paths to `sample`. |
| no review DB | Inspect dry-run output, then use `sample --apply` for the intended data directory. |
| migrated row has no snapshot | Run `rehydrate-snapshots` for the exact row or `--all`, then re-review it; never copy a legacy reviewed flag. |
| `model_calls_disabled` | Expected safe default, not a label. Do not treat it as a failed gold review. |
| publication rejected | Resolve every draft/evidence/adjudication/structure issue in the UI; do not edit release JSONL. |
| version exists | Select a new semantic version; releases are immutable. |
| browser unavailable | Confirm `serve` is running, then use the exact loopback URL it prints. |

Pilot acceptance requires 60 manually reviewed samples, 9–12 human-confirmed negatives, all rare-category floors, no unresolved states, and evidence for critical present facts. Track simple/complex review medians against the design targets (<2/<5 minutes) before expanding.

## Verification

```powershell
uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests -q
uv run pytest tests -q
uv run --project s01b_project_extractor pytest s01b_project_extractor/tests -q
uv run --project s02a_duplicate_merger pytest s02a_duplicate_merger/tests -q
uv run --project pipeline --with pytest pytest pipeline/tests -q
git diff --check
```
