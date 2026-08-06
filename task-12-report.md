# Task 12 — CLI, Pilot Dry Run, Documentation, and Verification

Date: 2026-08-06
Workspace: `E:\Projects\BRI_RE_Database_v2\.claude\worktrees\database-refactor-expansion-aa6198`
Mode: source databases read-only; no `--apply`, no external model calls, and no formal production gold publication were used for the real-data run.

## Delivered CLI and operator controls

- `gold-studio init` validates all three source paths, saves only local configuration, and initializes a Studio-owned review DB.
- `gold-studio sample` is dry-run by default; `--apply` is required to seed local review shells and a manifest. Its exact 60/11 Pilot quota and rare-category floors fail closed.
- `gold-studio build-candidates` requires exactly `--sample-id` or `--all`; bulk scope prints its exact count and requires confirmation unless `--yes` is explicit. `--resume` processes only samples with no saved bundle.
- The shipped CLI makes zero external model calls. Its production/review candidates retain `model_calls_disabled` as explicit structured state, so no cost-bearing operation is silently started.
- `gold-studio serve` defaults to `127.0.0.1:8765`; non-loopback hosts require `--allow-network`.
- `gold-studio publish` requires an explicit semantic version and delegates complete/incomplete/existing-version refusal to the immutable publisher.
- `gold-studio report` generates accessible, self-contained HTML with inline styling, semantic headings, tables, and captions; it has no scripts or remote assets.

The production taxonomy uses `新能源制造业`; the CLI retains that tag and explicitly adds the documented Pilot coverage alias `制造业`. This is not a lowered floor or a silent data transformation.

## Temporary SQLite end-to-end evidence

The integration test uses four fake candidate runners and a real temporary raw/extracted/main SQLite triplet. It performs `init` → `sample --apply` → `build-candidates --all --yes` → human confirmation → `publish` → `report`. It asserts that every source file SHA-256, every source-table row count, and every `id` set is identical before and after.

Command:

```powershell
uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests/test_integration.py -q
```

Result: `1 passed`.

## Real configured-database dry run

Configured read-only paths:

| Input | Path |
| --- | --- |
| raw news | `E:\Projects\BRI_RE_Database_v2\s01a_news_crawler\data\raw_news.db` |
| extracted projects | `E:\Projects\BRI_RE_Database_v2\s01b_project_extractor\data\projects.db` |
| main database | `E:\Projects\BRI_RE_Database_v2\s02a_duplicate_merger\data\db\bri_re_database.db` |

Command (no `--apply`):

```powershell
uv run --project external-tools/gold-standard-studio gold-studio sample `
  --raw-news-db E:\Projects\BRI_RE_Database_v2\s01a_news_crawler\data\raw_news.db `
  --projects-db E:\Projects\BRI_RE_Database_v2\s01b_project_extractor\data\projects.db `
  --main-db E:\Projects\BRI_RE_Database_v2\s02a_duplicate_merger\data\db\bri_re_database.db `
  --data-dir .tmp-gold-studio-dry-run
```

Result: `selected=60`, `negatives=11`, statuses `{labeled_current_source: 49, unlabeled_negative_proxy: 11}`, and `source_hashes_unchanged=True`. No review DB, cache, model request, or formal gold output was created.

Selected categories: `Unknown:2`, `储能:6`, `光伏:8`, `制造业:7`, `地热及余热:4`, `新能源制造业:7`, `核电:2`, `水电:3`, `火电:10`, `生物质:3`, `综合能源:2`, `输变电建设:13`, `风电:6`.

Selected risks: `multiple_projects:13`, `rare_category:36`, `unlabeled_source:11`. Required floors are satisfied: 储能 6/3, 制造业 7/3 (same articles retain `新能源制造业`), 输变电建设 13/3, 核电 2/2, 火电 10/2, 地热及余热 4/2, 生物质 3/2.

Source evidence before and after is identical:

| Source / entity set | Rows | ID-set SHA-256 | Logical SHA-256 |
| --- | ---: | --- | --- |
| raw_news | 27,444 | `869f88e768d3cc805229ea225b956160b7af2570279ac9466682ea417c3482f0` | raw_news.db: `08eac2bd0ab7c19687031d827e8b181fae2b5b2f1be9de0530dd07da38d22ff6` |
| canonical_projects | 4,529 | `13f155a1355d7df8dff1266eb38cccc9b397d150dc914566aa4abf2846c8be21` | bri_re_database.db: `1192efbae29ef808c7b9adf45c1b943482f601218b08e11ff79dd75fabae5977` |
| project_events | 5,365 | `68f874840ddbcaa954753e6a912f850e936ff6371c119eb56acd59644fcb6f2c` | (same main DB logical hash) |
| extracted projects DB | n/a | n/a | projects.db: `475c919dd1ab1f8e70812e53c98bd5d2bd2825a1fdc19868060bc1815062f0f4` |

The source scan found 27,444 usable raw articles, 5,654 with current labels, and 21,790 unlabeled negative proxies. Before the explicit manufacturing alias, the literal `制造业` floor was infeasible because the configured production taxonomy has `新能源制造业:195` and `制造业:0`; the documented alias resolves the taxonomy naming difference without lowering any quota.

## Verification commands and results

| Command | Result |
| --- | --- |
| `uv run --project external-tools/gold-standard-studio pytest external-tools/gold-standard-studio/tests -q` | 154 passed (one upstream Starlette deprecation warning) |
| `uv run pytest tests -q` | 304 passed, 1 skipped (pytest cache permission warning) |
| `uv run --project s01b_project_extractor pytest s01b_project_extractor/tests -q` | 605 passed (pytest cache permission warning) |
| `uv run --project s02a_duplicate_merger pytest s02a_duplicate_merger/tests -q` | 54 passed (pytest cache permission warning) |
| `uv run --project pipeline --with pytest pytest pipeline/tests -q` | 2 passed (pytest cache permission warning) |
| `git diff --check` | exit 0 |

The first unmodified pipeline test command could not spawn `pytest` because its project environment does not declare it. Re-running with temporary `--with pytest` passed; no dependency lock was changed.

## Fix round 1 rerun (machine-readable dry-run)

`sample --format json` now produces the complete real-data evidence in one source-only command. Its 2026-08-06 rerun returned `selected_count=60`, `negative_count=11`, status counts `labeled_current_source=49` / `unlabeled_negative_proxy=11`, and all floors satisfied (储能 6/3, 制造业 7/3, 输变电建设 13/3, 核电 2/2, 火电 10/2, 地热及余热 4/2, 生物质 3/2). Risks were multiple projects 13, rare category 36, and unlabeled source 11.

The structured `source_before` and `source_after` objects matched for authoritative logical hashes, physical diagnostic file hashes, raw-news 27,444-row ID digest `869f88e768d3cc805229ea225b956160b7af2570279ac9466682ea417c3482f0`, canonical-project 4,529-row ID digest `13f155a1355d7df8dff1266eb38cccc9b397d150dc914566aa4abf2846c8be21`, and project-event 5,365-row ID digest `68f874840ddbcaa954753e6a912f850e936ff6371c119eb56acd59644fcb6f2c`. Every `unchanged` boolean was true. No `--apply`, model call, or publication was used.

Fix-round verification: Studio `160 passed`; root `304 passed, 1 skipped`; s01b `605 passed`; s02a `54 passed`; pipeline `2 passed` via `uv run --project pipeline --with pytest pytest pipeline/tests -q`; `git diff --check` returned zero. The only test warnings were pre-existing cache-permission notices and FastAPI's Starlette TestClient deprecation warning.
