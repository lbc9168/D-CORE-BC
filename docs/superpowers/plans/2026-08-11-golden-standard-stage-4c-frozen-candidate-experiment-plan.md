# Golden Standard Studio 阶段 4C：冻结候选对照实验计划

## 1. 实验问题

在不修改 `pilot-v0.1.0`、production Prompt、模型、taxonomy policy 或数据库的前提下，回答两个问题：

1. Gold 发布前生成的 `gpt-5.6-terra` Provider 候选相对人工 Gold 的文章门控、项目识别和 S01B 字段表现如何；
2. `gpt-5.6-sol` Reviewer 在读取同一固定原文和 Terra 候选后，带来了哪些净改善、退化和无变化。

本轮使用现有冻结 artifact，不重新调用模型。它是 development-set 诊断：人工确认 Gold 时看过这些候选，因此结果不能作为无泄漏的最终模型排名，也不能据此直接宣称泛化能力。

## 2. 固定输入

- Gold：`data/releases/pilot-v0.1.0`，运行前核验 manifest 与全部 artifact hash；
- Provider：`data/candidates/terra-provider-<article-id>.json`，模型必须为 `gpt-5.6-terra`；
- Reviewer：`data/candidates/sol-reviewer-<article-id>.json`，模型必须为 `gpt-5.6-sol`，且 `parent_candidate_id` 必须指向对应 Terra candidate；
- 文章：候选 `article_sha256` 必须与 rich Gold 的 `article.content_sha256` 一致；
- Prompt provenance：记录 `data/runs/<article-id>` 中 manifest 与 rendered Prompt 的 hash，不改变原文件。

十篇文章必须全部存在且 ID 集合严格等于 release manifest；缺失、额外、模型名、父候选或文章 hash 不匹配时 fail closed。

## 3. 评分范围与适配

正式评分：

- 四分类文章状态：`valid / no_chinese_participation / irrelevant / other_non_project`；
- 二元 valid/non-valid；
- S01B 文章内项目 precision/recall/F1；
- S01B 可直接归因给抽取候选的类型化事实字段指标。

候选 envelope 映射：

- `valid_project → valid`；其他文章状态保持原语义；
- 项目 envelope 取其 `value` 与 `value_state`，保留 absence/unknown 状态；
- `chinese_entities → entities`，映射为 `{name, role}`；
- `category` 是候选建议而不是完整的确定性 taxonomy trace，`subcategory_raw_fact` 也不等于 Gold 的最终 `subcategory`；两者均保留但不进入本轮模型主指标；
- 不评分 S02A、S02B、S03 和 deterministic taxonomy，避免把未运行的后续阶段归因给 Provider/Reviewer。

## 4. 输出

输出到不可变目录：

`data/experiments/pilot-v0.1.0/frozen-terra-sol-v1/`

包含：

- `provider-report.json` 与 `provider-errors.jsonl`；
- `reviewer-report.json` 与 `reviewer-errors.jsonl`；
- `comparison.json`：文章、项目、总体字段和逐字段 delta；
- `summary.md`：Reviewer 改善/退化字段、样本限制和下一步建议；
- `run-manifest.json`：Gold、候选、Prompt provenance、实验器及所有输出 SHA-256。

同一 experiment id 不可覆盖；核心 JSON 不写运行时间戳，确保同输入可复现。

## 5. 验证

- 候选 schema、模型、文章 hash、父候选关系和文章 ID 集合 fail-closed；
- envelope absence 与实体映射单测；
- Terra 与 Sol 两份报告可重读，错误 JSONL 可逐行解析；
- 同一 experiment id 二次执行失败；
- release、候选、Prompt 和三份上游数据库 hash 在实验前后不变；
- 只运行 Studio 相关定向测试，不运行全量 pytest。

## 6. 完成条件

- 生成 Terra、Sol 和 Reviewer delta 的可复现报告；
- 清楚区分“Reviewer 改变了字段”与“Reviewer 实际提高了 Gold 一致度”；
- 报告醒目标注 development-set leakage；
- 不修改 Prompt、模型、taxonomy、Gold 或生产数据库，不创建提交。
