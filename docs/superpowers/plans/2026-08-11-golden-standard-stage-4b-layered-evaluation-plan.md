# Golden Standard Studio 阶段 4B：分层评估与错误归因计划

## 1. 目标

以不可变发布的 `pilot-v0.1.0` 为唯一人工真值输入，建立不会把 schema 差异误算成模型错误的评估器，并生成可追溯、可解释的 stored Pipeline snapshot v2 报告。

本阶段只建设评估基础设施，不修改 Golden Standard、S01B Prompt、模型配置、taxonomy policy、实体登记册、地点规则或生产数据库，也不调用外部模型。

## 2. 当前 Gold 能支持的评估

### 正式支持

- 文章门控的二元诊断：valid 与 non-valid；已存 Pipeline 项目无法恢复 no_chinese/irrelevant 的原始细分类，因此不伪造四分类分数。
- S01B 文章内项目识别、项目拆分和事实字段。
- S02B 标准实体/母公司与 S02B-2 标准地点。
- 当前数据库最终项目集合及字段相对 Gold 的端到端差异。

### 暂不支持

- 完整 S02A 跨文章 duplicate merger：当前 Gold 没有跨文章 canonical 聚类或成对同项目真值。
- S03 province/admin1：当前 Gold 没有独立一级行政区字段。
- 新鲜模型能力：本报告读取已存 projects.db 和主库快照，不重新调用模型。

报告必须显式写出这些能力边界，不能用文章内项目集合 F1 冒充完整 S02A 分数。

## 3. 运行与产物

默认输出：

`external-tools/gold-standard-studio/data/evaluations/pilot-v0.1.0/stored-snapshot-v2/`

包含：

- `report.json`：分层指标、字段指标、项目对齐和 Gold 可评分性统计；
- `errors.jsonl`：逐文章、逐项目、逐字段差异及所属评估层；
- `summary.md`：给人阅读的关键发现和限制；
- `run-manifest.json`：release/Gold/evaluator/source database hash 和所有输出 hash。

运行目录不可覆盖；相同 run id 二次执行必须失败。

## 4. 比较语义

### 项目对齐

- 使用项目名称为主、国家和地点为辅的确定性相似度；
- 一对一贪心匹配，保留每一对的匹配分数和理由；
- 未匹配 Gold 为漏抽，未匹配预测为多抽；
- 不把模糊匹配分数直接当字段正确率。

### 字段状态

- `present` 且有规范值：比较规范值；
- `present` 但 Gold 规范值为空：记录为 evidence-backed but value-unscorable，不作为错误或正确进入 value accuracy；
- `not_mentioned/not_applicable`：预测为空视为 absence 正确；当前 plain snapshot 无法区分两种 absence，报告为 coarse absence；
- `uncertain/needs_adjudication` 不应出现在已发布 Gold；若出现则 fail closed。

### 类型化比较

- 数值：解析数值后使用绝对/相对容差，不比较字符串格式；
- 日期：规范化 ISO 值；无法归一的 Gold 日期不进入 value accuracy；
- 枚举/文本：Unicode、空白和大小写规范化后精确比较；
- 列表：能源来源按集合比较；
- 实体：分别计算实体名称集合与名称-角色 pair，不比较 JSON 序列化字符串；
- 投资：按 total/contract/financing 三类槽位比较；Gold 只有 raw 外币文本而预测只有换算 USD 时标为 incompatible/unscorable，不记假错误。

## 5. 错误归因

- S01B：文章门控、Gold 项目漏抽/多抽、事实字段差异；
- S02B：对齐项目后的 normalized entity/location 差异；
- end-to-end：当前主库最终值差异，仅表示结果偏差，不自动推断根因；
- deterministic policy：只有输入事实和 policy 输出都被实际记录后才评分；stored snapshot v2 不伪造该层指标。

## 6. 可复现性

每次运行固定并记录：

- Gold release version 与 manifest/rich Gold SHA-256；
- evaluator 源文件 SHA-256 与比较配置；
- raw、projects、main SQLite SHA-256；
- baseline kind=`stored_pipeline_snapshot_v2`；
- 不包含会导致同输入不同字节的运行时间戳。

后续新鲜模型实验使用独立 run id，并额外记录 Prompt/model/temperature/schema/policy/registry hash。修改 Prompt 或 policy 后必须产生新 run，不能覆盖旧报告。

## 7. 验证

- 类型化字段单测：null state、数值、集合、实体、投资和 unscorable；
- 项目匹配单测：漏抽、多抽和一对一约束；
- 真实 `pilot-v0.1.0` 运行成功，输出 JSON/JSONL 可重读且 hash 一致；
- 同 run id 拒绝覆盖；
- release 目录和三份上游 SQLite 运行前后 hash 不变；
- 同一输入在不同临时输出根生成的核心报告字节一致。

## 8. 完成条件

- 生成可信的 stored snapshot v2 报告；
- 明确区分可评分、错误和不可评分字段；
- 不再把完整 S02A/S03 能力伪装为已覆盖；
- 更新 `docs/status` 与 `docs/changes/2026-W33.md`；
- 不创建提交。
