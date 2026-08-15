# pilot-v0.1.0 · stored snapshot v2

## Supported results

- Binary valid/non-valid accuracy: 0.800
- S01B project precision/recall/F1: 0.737/1.000/0.848
- S01B typed field micro accuracy: 0.644
- S02B typed field micro accuracy: 0.643
- End-to-end typed field micro accuracy: 0.656
- Recorded mismatches: 180; unscorable comparisons: 66

## Lowest S01B fields

- `project_developer`: 0.286 (scored=14, unscorable=0)
- `project_name`: 0.286 (scored=14, unscorable=0)
- `entities`: 0.357 (scored=14, unscorable=0)
- `storage_energy_mwh`: 0.357 (scored=14, unscorable=0)
- `category`: 0.571 (scored=14, unscorable=0)
- `location`: 0.643 (scored=14, unscorable=0)
- `planned_start_date`: 0.667 (scored=12, unscorable=2)
- `planned_complete_date`: 0.750 (scored=12, unscorable=2)

## Unsupported in this release

- Full S02A cross-article duplicate merger: missing canonical cluster Gold.
- S03 province/admin1: missing admin1 Gold field.
- Deterministic taxonomy: stored snapshot lacks a complete fact-input/policy-output trace.

This report did not call a model or modify Gold, Prompt, policy, registry, or upstream databases.
