# Storage Taxonomy v2 Migration Report

Date: 2026-08-13  
Rule version: `storage-taxonomy-v2-20260813`

## Outcome

- Candidate evidence packages: 809/809, exact source URL coverage 809/809.
- Terra decisions: 809/809.
- Final Sol risk decisions: 789/789.
- Final compile: 594 resolved, 215 needs manual review, 0 blocking exceptions.
- Canonical projects actually changed from the pre-apply baseline: 354.
- Resolved events whose legacy storage value was removed from generation-capacity fields: 207.
- Unresolved records received shadow audit rows only; no formal category/event overwrite.

## Database safety

- Final database: `s02a_duplicate_merger/data/db/bri_re_database.db`
- Final SHA-256: `7E60276F449A120A86A9955747BC4E64A3B81714087CA1AB1359C8AEC44FA33B`
- Schema backup SHA-256: `60E2909FD5F5DB13F8AA30BD2C124D8C9C67CC16CFC55FFD2CD57D9AEC530116`
- Pre-data-apply baseline backup SHA-256: `88D0AB924E342E80187A644F5D687529C8E664C14D8A3D65918BDB2F614897EA`
- Pre-capacity-separation backup SHA-256: `DF440C06375C756DB46A492580ED791A141E10AD6AA833AE8E52BD83E6199336`
- SQLite integrity: `ok`; foreign-key violations: 0.
- Row counts remained canonical/events/entities=`4529/5365/6572`; all three ID sets were unchanged.
- Exact repeat apply updated 0 projects and created no additional backup.

## Semantic red-team findings

The first structurally valid Sol pass still confused storage factories, data storage, national targets, global orders, portfolio totals, and other projects in the same article. Browser QA exposed those cases before handoff. The database was backed up, restored to the verified pre-data-apply baseline, recompiled, and replayed after an all-risk semantic review. Examples removed include Brazil/Vietnam battery-factory GWh, Thailand data-center “storage”, Spain 30GW/20GWh national targets, UK 16GW corporate data, Saudi 12.5GWh other-project context, and Australia 12GW portfolio scale.

## Remaining manual queue

215 projects remain `needs_review=1`; their formal values were not auto-overwritten. Among them, 57 retain the legacy `综合能源/光储|其他` subcategory in the database. The s03/s04 consumption layer suppresses those deprecated subcategories, so production exports contain zero legacy `光储/其他` subtype values while preserving the audit trail for later adjudication.

## Artifact hashes

- Campaign manifest: `92A6917E70A50320823FBECACE0F8BB0245BB26859F4E8140BC9F76BD6D37660`
- Final risk manifest: `E156447FE0A7A31AFDD9126F774E554B903A7579863B18430B479D12C7AECF44`
- Golden pause manifest: `7628077D2E80769FDAB8055806CD008ABDEE5CBCBBD015676BB5F9A5D6073967`

## Verification

- S01B: 606 passed.
- S02A: 56 passed.
- S03: 29 passed.
- Repository tests: 299 passed, 1 skipped.
- Golden Standard Studio: 33 passed, 1 existing deprecation warning.
- Frontend: TypeScript typecheck and Vite production build passed.
- Browser: Solar + Storage coexists with Utility-scale; generation MW and storage MW/MWh are separate; storage factories show no project capacity; deprecated Light Storage subtype is absent.

No commit, push, deployment, project merge/split, or entity/financing/stage rewrite was performed.
