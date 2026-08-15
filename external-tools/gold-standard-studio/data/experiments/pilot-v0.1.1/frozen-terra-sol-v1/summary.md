# Frozen Terra → Sol development-set experiment

> 这 10 篇在人工复核时展示过模型候选；结果仅用于开发诊断，不是无泄漏泛化评估。

## Primary results

- Provider four-class/binary gate accuracy: 1.000/1.000
- Reviewer four-class/binary gate accuracy: 1.000/1.000
- Provider project P/R/F1: 1.000/1.000/1.000
- Reviewer project P/R/F1: 1.000/1.000/1.000
- Provider typed field micro accuracy: 0.874
- Reviewer typed field micro accuracy: 0.937
- Reviewer field micro delta: +0.063

- Reviewer resolved/introduced/shared mismatches: 14/1/12

## Reviewer field deltas

- Improved `entities`: +0.357
- Improved `planned_complete_date`: +0.250
- Improved `planned_start_date`: +0.250
- Improved `financing_mode`: +0.143
- Improved `project_developer`: +0.071
- Regressed `project_stage`: -0.071

## Attribution limits

- A Reviewer-introduced mismatch means disagreement with the current Gold, not automatically a Reviewer reasoning error; inspect `comparison.json` before changing a Prompt.
- `category` and final `subcategory` are excluded because the frozen candidates do not contain a complete deterministic taxonomy trace.
- S02A duplicate merging, S02B normalization, and S03 admin1 are not part of this Provider/Reviewer experiment.
- Article 9027 predates the rendered Prompt pack; its candidate JSON records source paths but not the complete rendered Prompt bytes.
- No model was rerun and no Prompt, policy, Gold, candidate, or upstream database was modified.
