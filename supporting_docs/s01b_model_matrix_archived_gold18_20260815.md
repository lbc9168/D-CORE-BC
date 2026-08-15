# S01B 模型组合评测：暂停封存 Development Gold-18

> 日期：2026-08-15  
> 性质：confirmed development benchmark；不是 blind holdout，也不是新 Gold release  
> 生产默认：未修改

## 样本与冻结边界

- 只读来源：`external-tools/gold-standard-studio/data/archives/golden-standard-paused-20260813/review.db`。
- 来源库 SHA-256：`095e7f4a55daf71b732bd64e8f5f2d4aea2faa7b4075674241a5d3a02306a9e8`。
- 只选择 `confirmed=1`：18 篇、24 个项目；valid=13、no_chinese=4、irrelevant=1。
- 组成：`pilot-v0.1.1` 十篇 + 后续人工确认八篇；与此前 early Gold Pilot-8 无重叠。
- 23627 revision 25 仍未确认，明确排除。
- 模型输入从封存 `article_snapshots` 生成 run-local `raw_news.db`，标题、正文和正文 hash 均与人工复核时一致，不读取后来变化的 raw 数据库。
- 评测投影 `gold.jsonl` SHA-256：`98f35b4c6a574050e58ebbf7aad9b17819a875f87cd3304ff47a736b5d93c65a`；sample SHA-256：`2c60af2028140ca1d142bc145d804f68976dab979ce191c82c2d524cb352f745`。
- `financing_mode=not_mentioned/null` 在 evaluator 传输层等义映射为当前枚举 `Unknown`；封存草稿未修改。

## 模型路由与运行条件

| Profile | S1/S2 | S34/S5 |
|---|---|---|
| baseline | `gemini-2.5-flash-lite` | `deepseek-v4-flash` |
| Gemini 3.5 | `gemini-3.5-flash-lite` | `deepseek-v4-flash` |
| GPT-5.4 Nano | `gpt-5.4-nano` | `deepseek-v4-flash` |
| Mixed 3.5/2.5/5.4 Nano | S1=`gemini-3.5-flash-lite`；S2=`gemini-2.5-flash-lite` | S34/S5=`gpt-5.4-nano` |

- 四组使用同一当前 Prompt、确定性后处理和 `S01B_MERGE_S34=1`。
- 单次 provider 请求超时固定 30 秒；provider SDK 内部隐式重试关闭，统一由 S01B 外层最多重试三次。
- 每个 profile 独立缓存，18×4=72 个文章级 workflow 最终均成功，`n_failed=0`。
- 使用模型 alias 而非固定 snapshot；本轮适合方向性选择，正式回归应固定 snapshot。

## Canonical evaluator

| Profile | Status | 项目 P | 项目 R | 项目 F1 | 匹配/预测/Gold | Weighted | 实跑 wall time |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 0.8889 | 0.7500 | 0.8750 | **0.8077** | 21/28/24 | 0.7639 | 852.7s |
| Gemini 3.5 | **1.0000** | 0.4688 | 0.6250 | 0.5357 | 15/32/24 | **0.8150** | 788.1s |
| GPT-5.4 Nano | 0.9444 | 0.4118 | 0.2917 | 0.3415 | 7/17/24 | 0.7736 | **438.1s** |
| Mixed 3.5/2.5/5.4 Nano | **1.0000** | 0.7407 | 0.8333 | 0.7843 | 20/27/24 | 0.6865 | **231.1s** |

`weighted_total` 只基于成功按项目名对齐的项目，且不包含项目集合 F1；三组匹配项目数为 21/15/7，不能单独用 weighted 排名。

### 对齐后字段

| Profile | Category* | Stage | Financing | Capacity | Investment | Entity F1 | Entity role |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 0.9048 | 0.9048 | 0.9048 | 0.9048 | 0.9048 | 0.7619 | 0.5000 |
| Gemini 3.5 | 0.9333 | **1.0000** | 0.9333 | **0.9333** | **1.0000** | **0.8000** | 0.5000 |
| GPT-5.4 Nano | **1.0000** | **1.0000** | **1.0000** | 0.8571 | **1.0000** | 0.4286 | **0.7500** |
| Mixed 3.5/2.5/5.4 Nano | 0.9000 | 0.8500 | 0.9000 | 0.9000 | 0.8000 | 0.5983 | 0.2778 |

`*` Category 仍保留暂停快照的 2026-08-12 口径；与当前 taxonomy v2.1 不一致的条目不能当作现行分类准确率。

## 一文一项目锁定诊断

十篇有效单项目文章按 URL 强制对齐，避免旧中文项目名阈值遮蔽字段表现；Gemini 3.5 因 14595 过拆为 11 项，仅 9/10 可配对。

| Profile | Pair recall | 稳定字段准确率** | Category* | Entity F1 | Role |
|---|---:|---:|---:|---:|---:|
| baseline | 10/10 | 0.8250 | 0.8000 | 0.8000 | 0.5455 |
| Gemini 3.5 | 9/10 | **0.8611** | **1.0000** | 0.8889 | 0.5455 |
| GPT-5.4 Nano | 10/10 | 0.8250 | 0.9000 | **0.9000** | **0.5833** |
| Mixed 3.5/2.5/5.4 Nano | 10/10 | 0.8000 | 0.8000 | 0.3733 | 0.4286 |

`**` 稳定字段为 stage、financing、capacity、investment，排除旧/新 taxonomy 最敏感的 category。

## 主要错误形态

- baseline：1533 从 8 项过拆为 12 项；18491（Gold=no_chinese）误判 valid 并生成 2 项；27309 将 irrelevant 判为 no_chinese。
- Gemini 3.5：1533 为 8→7，9027 为 2→1；14595 从单项目严重过拆为 11 项。文章状态 18/18 正确，但项目边界不稳定。
- GPT-5.4 Nano：1533 从 8 项严重合并为 1 项，是项目召回和实体覆盖下降的主因；27309 将 irrelevant 判为 no_chinese。
- Mixed：文章状态 18/18 正确；14595 恢复为 1→1、9027 为 2→2、281 为 4→4，但 1533 从 8 项过拆为 11 项，且匹配项目的实体名与角色低于 baseline。
- 三组都出现过模型把 TX evidence 填到非 TX Category 的情况，确定性 S2 contract 会拒绝并重试，没有让非法 TX/MF 字段进入最终结果。
- 共享的 DeepSeek S34 偶发 30 秒超时，但外层重试后全部恢复；最终没有文章级失败。

## Taxonomy 次级诊断

| Profile | Generation source F1 | Storage status | Storage power | Storage energy | Proposed category |
|---|---:|---:|---:|---:|---:|
| baseline | 0.9714 | 0.8095 | 1.0000 (2/2) | 0.6000 (6/10) | 0.8095 |
| Gemini 3.5 | **1.0000** | **0.8667** | 1.0000 (1/1) | **0.7778 (7/9)** | **0.9333** |
| GPT-5.4 Nano | **1.0000** | 0.4286 | 0.0000 (0/2) | 0.0000 (0/4) | 0.7143 |
| Mixed 3.5/2.5/5.4 Nano | 0.9697 | 0.8500 | 1.0000 (2/2) | 0.6667 (6/9) | 0.8000 |

该表只覆盖成功项目匹配，分母不同且 Gold 是旧 taxonomy，特别是 GPT-5.4 Nano 的储能小分母不能外推为总体准确率；但它提示该组合在项目拆分和储能归属上需要重点回归。

## 结论与下一步

1. 封存的 18 篇可以安全用于 development 测试，且比 early Pilot-8 更有价值：包含 24 个项目、负样本和 4/8 项目长文；不能当 blind，也不能静默升级为新 guideline release。
2. 暂不替换生产 baseline。baseline 的项目集合 F1 仍最高（0.8077），Mixed 已接近（0.7843）并获得满分状态门控，但对齐后实体与角色明显退化。
3. Gemini 3.5 是下一轮最值得保留的 challenger：状态门控满分、稳定字段最好，但必须先处理 14595 的严重过拆和多项目召回问题。
4. GPT-5.4 Nano 速度最快、对已匹配单项目字段不错，但当前完整 S1/S2 替换导致复杂长文严重欠拆，不宜直接作为 production 主路由。
5. 用户指定的 Mixed 路由已验证可行：它消除了 Gemini 3.5 全链路在 14595 的 1→11 过拆，也避免 GPT-5.4 Nano 全链路在 1533 的 8→1 欠拆；下一轮应针对 1533 的 8→11 和 S34/S5 实体角色退化做 Prompt/路由消融，不宜仅凭本 18 篇旧口径 development Gold 切生产。

## 本地产物

- `eval/model_matrix_runs/20260814_archived18/benchmark/manifest.json`
- `eval/model_matrix_runs/20260814_archived18/benchmark/gold.jsonl`
- `eval/model_matrix_runs/20260814_archived18/benchmark/raw_news.db`
- `eval/model_matrix_runs/20260814_archived18/summary.json`
- `eval/model_matrix_runs/20260814_archived18/cache/<profile>/`
- `eval/model_matrix_runs/20260814_archived18/reports/<profile>/`

这些运行产物均被 `.gitignore` 排除；本报告和适配/runner 代码保留在仓库工作树中。
