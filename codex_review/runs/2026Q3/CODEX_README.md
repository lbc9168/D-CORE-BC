# 2026Q3 季度复核包

生成时间：2026-07-06 19:26:26 UTC

**你的任务**：读取本文件夹里的 guide_T*.md 分拣规则，逐条判断 queue_T*.json 里的条目，把结果写成 verdicts_T*.json。

## 文件清单

| 任务 | 输入 | 规则 | 输出 | 条数 |
| --- | --- | --- | --- | --- |
| T1 漏合并候选对 | queue_T1.json | guide_T1.md | verdicts_T1.json | 1022 |
| T2 范围复核 | queue_T2.json | guide_T2.md | verdicts_T2.json | 668 |
| T3 实体归属补录 | queue_T3.json | guide_T3.md | verdicts_T3.json | 42 |

## 硬规则

1. 先读对应 guide 再动手。
2. 输出信封 `{"task":"T?","quarter":"2026Q3","verdicts":[...]}`，字段以 guide 内 JSON 示例为准，**禁止任何额外字段**（严格校验，多余字段整条拒收）。
3. evidence_quote 必须逐字引用 item 上下文或来源原文。
4. T3 归属类结论必须联网核实，且 source_urls 为 http(s) 链接。
5. 只写三个 verdicts 文件，不改动本文件夹其他任何文件。

## 分批说明

允许部分完成——verdicts 里只包含已判条目即可，未判条目下轮重新入队；量大（如 T1 上千对）建议分批追加，每批写完自行 `json.load` 校验可解析。

## 完成后

告知操作者运行：`uv run --no-sync python -m codex_review.quarterly ingest --quarter 2026Q3`
