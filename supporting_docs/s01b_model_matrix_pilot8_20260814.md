# S01B 模型替换可行性评测：早期 Gold Pilot-8

> 日期：2026-08-14  
> 性质：development smoke / 方向性诊断，不是正式模型排名  
> 生产默认：未修改

## 目标

验证现有 `Gemini 2.5 Flash-Lite + DeepSeek` 分阶段 workflow 能否在不改变 Prompt 和确定性后处理的前提下，替换为 Gemini 3.5 Flash-Lite、GPT-5 Nano、GPT-5.4 Nano 或固定混合路由。

## 固定样本与路由

- 来源：`eval/gold_set.jsonl` 早期 Gold，共 55 篇；本轮用 seed `20260814` 固定抽取 8 篇。
- 样本：valid 6、irrelevant 1、no_chinese 1。
- sample SHA-256：`a2d83d9b2b21fc77da6ac16debbdba9aa0cfa3eacf5adaa866e9f119e61c5ba4`。
- 所有组合启用 `S01B_MERGE_S34=1`。
- 基线与三个单主模型组合：S1/S2 使用主模型，S34/S5 使用 `deepseek-v4-flash`。
- 固定混合组合：S1=`gpt-5-nano`，S2/S5=`gpt-5.4-nano`，S34=`deepseek-v4-flash`。
- 原始响应按 profile + URL + Prompt/路由哈希隔离缓存；未写生产数据库。

## 现有 canonical evaluator 结果

| Profile | n_failed | Status accuracy | Project set F1 | Weighted total | 首次 live wall time |
|---|---:|---:|---:|---:|---:|
| Gemini 2.5 Flash-Lite + DeepSeek | 1 | 0.8571 | 0.0000 | 0.8571 | 183.920s |
| Gemini 3.5 Flash-Lite + DeepSeek | 0 | 0.8750 | 0.4000 | 0.8187 | 120.245s |
| GPT-5 Nano + DeepSeek | 0 | 0.8750 | 0.2000 | 0.9688 | 386.123s |
| GPT-5.4 Nano + DeepSeek | 0 | 1.0000 | 0.1667 | 1.0000 | 177.433s |
| GPT-5 Nano / GPT-5.4 Nano / DeepSeek | 0 | 0.8750 | 0.4000 | 0.7937 | 179.600s |

`weighted_total` 不能单独用于本轮排名：旧 evaluator 只对项目名阈值成功对齐的项目计算字段；当匹配数很少时，少量正确字段会把 weighted total 放大。基线的一篇失败为 Gemini endpoint 两次远端断连，不是 schema 校验错误。

## 一文一项目 URL 锁定诊断

本轮 6 篇 valid 均为一文一项目。为隔离旧项目名阈值造成的假性低分，额外按同一 URL 且两边各一个项目强制对齐；此诊断不改变 canonical evaluator。

| Profile | Pair recall | Category | Stage | Financing | Capacity | Investment | Entity F1 | Role |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Gemini 2.5 + DeepSeek | 0.8333 | 0.6000 | 1.0000 | 1.0000 | 0.8000 | 0.8000 | 0.5000 | 1.0000 |
| Gemini 3.5 + DeepSeek | 0.8333 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 0.8000 | 0.4333 | 1.0000 |
| GPT-5 Nano + DeepSeek | 0.8333 | 0.8000 | 0.8000 | 1.0000 | 1.0000 | 0.8000 | 0.5000 | 0.6667 |
| GPT-5.4 Nano + DeepSeek | 1.0000 | 0.6667 | 0.6667 | 1.0000 | 0.6667 | 0.8333 | 0.5278 | 1.0000 |
| Mixed Nano + DeepSeek | 0.8333 | 0.8000 | 0.8000 | 1.0000 | 0.8000 | 0.8000 | 0.5000 | 1.0000 |

## 口径漂移审计

早期 Gold 可以验证 endpoint、JSON contract、stage routing、失败率和粗粒度 status，但不能直接作为当前 `energy_taxonomy_v2.1` 的最终准确率基准：

- 14078 的旧 Gold 将“光伏电站配套储能”归为“储能”，当前口径应归“光伏”并在 confirmed 时加“光储”标签；候选输出光伏不能按当前规则视为错误。
- 19312 的旧 Gold 将合同金额放入 `total_investment_usd`；当前契约已将总投资与合同金额分离。
- 当前项目名包含容量、区域、阶段或项目形态，旧 Gold 名称更短；中文无分词情况下 `token_set_ratio >= 80` 系统性漏配。8 篇中候选项目名平均相似度只有约 60–79，但逐篇人工核对多数指向同一项目。
- 实体 canonical 名与旧 Gold 原文短名存在差异，entity F1 同时测到了规则版本漂移。

## 结论

- 技术可行：三个新增模型和固定混合路由均完成端到端 workflow；OpenAI 两个 Nano 可继续与 DeepSeek 组合。
- 暂不切换生产默认：8 篇且 Gold 口径过时，不能据此宣称 GPT-5.4 Nano 或任一模型准确率更高。
- 下一次严肃比较应使用按当前 guideline 发布的 development Gold，并固定模型 snapshot、Prompt hash、stage route、reasoning 参数和 sample manifest。
- 若只做下一轮低成本筛选，优先保留 `Gemini 3.5 Flash-Lite + DeepSeek` 与 `GPT-5.4 Nano + DeepSeek`：前者本轮字段一致性与延迟较好，后者 status/pair coverage 最好；GPT-5 Nano 本轮延迟最高。

## 产物

- 本地 run：`eval/model_matrix_runs/20260814_pilot8/`
- 总结：`summary.json`
- 固定样本：`sample.jsonl`
- 分 profile 原始响应缓存：`cache/<profile>/`
- 分 profile canonical 报告：`reports/<profile>/`

