# Golden Standard Master Plan（目标 200 篇）

日期：2026-08-11  
适用工具：`external-tools/gold-standard-studio`  
目标规模：200 篇文章（仍保留 200–300 篇扩容边界；任何调整必须发布新的样本清单版本）

## 1. 目标与边界

本计划把 Golden Standard 建成覆盖 S01B Project Extractor、S02A Duplicate Merger、S02B Entity/Location Normalizer、S03 行政区推断和完整流水线的分层人工真值。所有层共享同一份有证据的 rich Gold，但分别投影、计分和归因，避免把后续规则错误倒算成抽取错误。

200 篇是当前正式规划点，不要求一次性建设。每一阶段必须先冻结样本和口径、完成人工复核、发布不可变版本并跑回归，再决定是否扩容。当前十篇 `pilot-v0.1.1` 是候选可见的 development set 起点，永久不得转入 challenge 或 blind，也不得计入无泄漏模型排名。development/challenge/blind 是后续使用与发布边界，不是网页人工复核时是否显示 Terra/Sol 辅助候选的开关；只有人工确认后的结果才是 Gold。

## 2. 总体样本与数据划分

| 划分 | 目标篇数 | 用途 | 模型候选可见性 | 可否用于调 Prompt/规则 |
|---|---:|---|---|---|
| development | 90 | 标注口径形成、错误分析、Prompt/规则迭代 | Terra/Sol 辅助候选可见；记录候选与人工动作 | 可以 |
| challenge | 50 | 固定困难案例、阶段发布门槛和回归压力测试 | Terra/Sol 辅助候选可见；人工独立确认 | 不直接调参；只能提出下一版本假设 |
| blind holdout | 60 | 冻结系统的一次性无泄漏评估 | 标注者可见候选并人工确认；文章映射、Gold 和评估结果对调参流程隔离 | 不可以 |
| **合计** | **200** |  |  |  |

约束：

1. `pilot-v0.1.1` 的 10 篇全部计入 development 的 90 篇起点。
2. 文章一旦进入 development 或 challenge，就永远不能转为 blind；看过候选、错误报告、人工草稿或 Gold 的文章同样不能转为 blind。
3. blind 在标注规则、taxonomy、实体登记册和评估代码稳定后才抽取；标注工作区可以使用相同 Terra→Sol→人工确认流程，但开发/调参工作区只保存匿名配额与 sealed manifest hash，不保存可反推文章 ID 的清单。
4. challenge 与 blind 不允许和 development 共享同一 canonical project 家族、转载正文或近重复文章，避免 S02A 家族泄漏；划分单位是 canonical family/near-duplicate group，而非单篇文章。

## 3. 样本分层

抽样采用“概率骨架 + 定向过采样”。development 保留广覆盖和快速反馈，challenge 过采样边界与冲突，blind 从去重后的候选家族中按固定 seed 分层抽取并由独立保管者封存。

### 3.1 文章结果与项目复杂度

- 最终目标约 70–75% `valid`，其余覆盖 `no_chinese_participation`、`irrelevant`、`other_non_project/chinese_unnamed` 等门控边界；这些比例是抽样目标，不是从历史 Pipeline 继承的真值。
- valid 文章按 Gold 项目数覆盖单项目、2–3 项目和 4 项目以上；challenge 重点覆盖项目清单、历史回顾、打包合同、规划/MOU 与真实项目混杂。
- 覆盖中文、英文和中英混合正文；覆盖短讯、长文、列表、转载及正文噪声，但排除正文为空或无法固定内容 hash 的记录。

### 3.2 能源、阶段、地理和时间

- 主类别覆盖光伏、风电、水电、火电、储能、输变电建设、综合能源和新能源制造业；核电、生物质、地热及余热等低频类别设最低样本数，不按生产频率自然消失。
- 阶段覆盖拟开发、签约、中标、收购、开工/在建、竣工和投入运营，并保留同一项目跨阶段报道。
- 地理按大区、国家频次和低频国家分层；S03 子样本要求正文能支持或明确不能支持 admin1/province，不能用数据库既有规范值替代原文证据。
- 时间覆盖数据源主要年份；同一事件的连续报道作为 S02A 家族抽样，不把时间近邻误当独立样本。

### 3.3 分层 Gold 覆盖目标（集合相互重叠）

| 层 | 最终目标覆盖 | 人工真值单位 |
|---|---:|---|
| S01B | 200 篇 | 文章门控、文章内项目边界、事实字段及逐字证据 |
| S02A | 至少 64 篇、24 个以上多文章家族 | article-project 到 canonical project 的归属、同/异项目 pair、拆分/过合并裁定 |
| S02B | 至少 128 篇 valid 文章 | 原文实体到 canonical entity/母公司/角色，以及原地点到标准地点的映射 |
| S03 | 至少 96 篇 valid 文章 | country/admin1/province 的支持状态、标准值及不应推断情形 |
| 端到端 Gold | 至少 80 篇 | 从文章门控到 canonical family、实体、地点、admin1 和最终字段的完整链路 |

## 4. 各层 Gold 合同

### S01B Project Extractor

- 文章级四分类与理由；valid 文章的项目数量和边界。
- 项目名称、类别原始事实、能源/储能、容量、阶段、国家、原始地点、金额、融资、日期、开发商、原文实体与角色。
- 每个 `present` 事实字段必须保存正文连续逐字证据；缺失、不可适用、不确定和待裁定分开表示。

### S02A Duplicate Merger

- 每个 Gold 项目获得稳定的 `gold_project_id`，跨文章同一项目再映射到 `gold_canonical_id`。
- 保存正/负 pair、family 边界、拆分/合并理由和关键区分证据；多项目文章中的项目必须逐个入族。
- 以 canonical family 为 development/challenge/blind 分组单位，禁止同一家族跨 split。

### S02B Entity/Location Normalizer

- 实体保存 `mentioned_name → canonical_entity → parent_company` 与项目级 role；公司级概述不得自动下放。
- 地点保存原文地点、标准地点、映射类型和证据；候选场址、别名、行政层级变化与无法规范化必须显式记录。
- entity registry/location rules 的版本和 hash 随 release 固定，后续登记册变化不得覆盖旧 Gold。

### S03 Admin1/Province

- 独立保存 `country`、`admin1_raw`、`admin1_normalized`、推断依据和 `not_supported/ambiguous` 状态。
- 只有正文或已批准的确定性地名层级映射能支持 admin1；不得根据项目名常识或当前数据库值无证据补全。

### 端到端 Gold

- 关联 article hash、article status、article-project、gold canonical family、所有最终项目字段、实体、地点/admin1 及其层级来源。
- 端到端投影只消费已裁定字段；任一中间层仍为 `needs_adjudication` 时禁止发布正式 end-to-end Gold。

## 5. 人工复核与裁定

1. 标注前冻结文章正文、样本清单、规则版本和 Terra/Sol 候选；网页不在人工复核过程中调用模型。
2. development 由一名主标注者逐字段复核；至少 25% 按风险分层做独立二审，规则新增或高风险字段必须二审。
3. challenge 与 blind 100% 双人复核；两位复核者可以查看同一份已冻结 Terra/Sol 辅助候选，但不查看彼此尚未提交的人工判断，也不显示数据库最终值。两人不一致时由第三位裁定者按原文裁决。
4. S02A 家族整体复核，不能把同一家族拆给互不知情的标注者；S02B/S03 的规则层值与事实层证据分别确认。
5. 每轮计算字段一致率、项目边界一致率和 S02A pair agreement；低于发布门槛的字段先修正规则并回审受影响样本。
6. 工具继续采用“草稿宽松、确认严格”；确认后锁定，修订必须 reopen 并留下 action/revision，不允许直接改已发布 artifact。

## 6. 版本与发布

- 样本清单：`cohort-<stage>-vN`，固定 article ID、正文 hash、split、strata、family/group 和源数据库 hash；同名目录不可覆盖。
- 标注规则：日期化版本，并在 release manifest 中记录文件 SHA-256。
- Gold release：development 可使用 `dev-v0.x.y`；challenge 联合发布使用 `gold-rcX`；首次 blind 评估完成后形成 `gold-v1.0.0`。任何 Gold 修订都发布新版本。
- 每个 release 原子生成 rich Gold、S01B/S02A/S02B/S03/end-to-end 投影、manifest 和 artifact hash；发布器 fail closed。
- blind Gold 与文章映射由独立目录/保管者保存；评估运行只接收匿名预测和 sealed evaluator 输出。完成一个正式 benchmark 周期后再决定是否揭示、退休或补充新的 blind。

## 7. 回归评估与发布门槛

### 指标

- S01B：文章四分类 macro F1、valid/non-valid、项目 precision/recall/F1、字段 micro/macro accuracy 与关键字段分项。
- S02A：pairwise precision/recall/F1、B-cubed F1、family exact match、over-merge/under-merge 数；按 canonical family bootstrap。
- S02B：canonical entity set、entity-role pair、parent mapping、标准地点 exact/alias match 和 abstention correctness。
- S03：admin1 exact accuracy、支持/不支持分类、层级一致率与错误推断率。
- 端到端：文章到最终 canonical project 的 project F1、关键字段 macro、完整项目 exact match 和 error provenance。

### 回归流程

1. 每个 Prompt、模型、taxonomy、registry、location rule 或 pipeline 变更使用新 run id，并固定代码、配置、模型参数和输入 hash。
2. development 可反复运行；challenge 只在候选版本冻结后运行，并与最近已接受版本逐层比较。
3. 默认发布门槛：challenge 的文章门控/项目 F1/关键字段 macro 不得出现超过 1 个百分点的绝对回退；S02A over-merge、S03 无依据推断等高代价错误不得增加。小样本阶段同时报告原始计数，不用单一百分比掩盖退化。
4. blind 只对最终候选运行一次；先登记版本和 sealed hash，再执行，报告 95% cluster-bootstrap 区间。blind 结果不得用于同一版本调参。
5. 每次 Gold 修订重跑不可变历史候选，区分“模型变化”和“真值变化”；release 与评估产物均不可覆盖。

## 8. 分阶段实施

| 阶段 | 累计规模 | 主要产物 | 门控 |
|---|---:|---|---|
| 已完成 Pilot 4A–4D | 10 | `pilot-v0.1.1`、分层 evaluator、冻结候选实验 | 全部作为 development |
| Stage 4E | 50 | 新增 40 篇：24 development + 16 challenge；冻结清单并接入复核队列 | 不建立 blind，不批量确认 |
| Stage 4F | 50 | 完成 40 篇人工复核、家族裁定与首个 expansion release | 一致率和分层投影通过 |
| Stage 5A | 100 | 扩充低频类别、S02A 家族和 S03 | challenge 回归稳定 |
| Stage 5B | 140 | 补齐 development=90、challenge=50 | 规则与 evaluator 冻结 |
| Stage 6 | 200 | 按 family 分层抽取并封存 blind=60，运行一次正式评估 | 形成 `gold-v1.0.0` |

本计划不授权一次性建设 200 篇系统。当前只进入 Stage 4E 的 40 篇扩展实施。
