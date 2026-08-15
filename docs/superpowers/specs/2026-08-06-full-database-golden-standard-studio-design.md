# 全数据库 Golden Standard Studio 设计规格

> 日期：2026-08-06  
> 状态：已完成对话评审，等待用户复核书面规格  
> 目标分支：`claude/database-refactor-expansion-aa6198`

## 1. 背景与目标

项目已有文章级 `eval/gold_set.jsonl`、JSONL 校验器、CSV 转换器和字段级评测框架，但当前正式 gold 只有 55 篇，字段主要覆盖 status、项目拆分、类别、阶段、融资模式、装机、总投资、地点和中方实体，人工复核体验仍以 CSV/Markdown 为主。现有 gold 不足以长期支撑全数据库范围的 prompt 调整、模型替换、确定性解析器修改和能源分类 policy 回归。

本项目建设一个位于 `external-tools/` 的本地 Golden Standard 校审工具，形成约 250 篇、覆盖所有主要类别与抽取风险的版本化基准集。工具使用数据库当前值、生产 extractor、独立审校模型和确定性解析器生成候选答案，通过“冲突优先 + 难度排序 + 高一致样本抽检”的浏览器界面完成人工确认。只有人工确认后的 rich gold 才是权威标准。

### 1.1 成功标准

- Pilot 先完成 60 篇，随后扩展至 200 篇，最终冻结约 250 篇 v1.0。
- 负样本约占 15%–20%，能评测 irrelevant、no_chinese 和 chinese_unnamed。
- 覆盖全类别、单/多项目、阶段拆分、储能、金额、开发商和中企角色等核心风险。
- 简单文章人工复核中位时间低于 2 分钟，复杂文章低于 5 分钟。
- 人工操作可自动保存、恢复、审计和版本化发布。
- 任意模型、prompt、解析器或 policy 变更均可与固定 gold 重跑比较。
- 工具对生产数据库始终只读，不改变 canonical/event ID、正式字段或原始新闻。

### 1.2 非目标

- 不把全库数千项目逐条人工确认成 Golden Database。
- 不在本轮修改生产 prompt、切换模型、迁移正式分类或重导 s03/s04。
- 不把 canonical ID、event ID、ISO、BRI/LDC、类别码、英文翻译、缓存结果、批次号和时间戳纳入人工 gold。
- 不允许未经人工确认的 AI 候选直接成为正式 gold。
- 不建设联网、多用户或云端部署系统；v1 是单机本地工具。

## 2. 目录与组件边界

工具放在独立目录，避免根目录散落脚本和运行数据：

```text
external-tools/
└── gold-standard-studio/
    ├── pyproject.toml
    ├── README.md
    ├── src/
    │   └── gold_standard_studio/
    │       ├── sampling.py
    │       ├── candidate_builder.py
    │       ├── difficulty.py
    │       ├── review_store.py
    │       ├── exporter.py
    │       ├── schemas.py
    │       └── web/
    │           ├── app.py
    │           ├── templates/
    │           └── static/
    ├── tests/
    └── data/
        ├── review.db
        ├── model_cache/
        ├── snapshots/
        └── reports/
```

- 后端采用 Python + FastAPI + SQLite。
- 前端采用服务端模板和少量原生 JavaScript，不引入 Node 构建链。
- `data/` 是本地运行产物并加入 Git 忽略；review DB 定期在该目录生成可恢复备份。
- 工具读取 `raw_news.db`、`projects.db` 和 `bri_re_database.db` 时使用 SQLite 只读 URI。
- 工具可复用 s01b extractor、公共枚举与确定性解析器，但不把校审逻辑接入生产 pipeline。

正式人工成果属于 eval 资产并进入版本控制：

```text
eval/gold_sets/
├── database_core_v1/
│   ├── rich_gold_v1.jsonl
│   ├── eval_projection_v1.jsonl
│   ├── manifest.json
│   ├── schema.json
│   ├── distribution.json
│   └── CHANGELOG.md
└── challenge_sets/
    ├── storage_v1.jsonl
    ├── financing_v1.jsonl
    └── entity_v1.jsonl
```

rich gold 是唯一人工维护的权威数据；eval projection、分布统计和报告都由工具生成，禁止手工双写。

## 3. 样本设计

### 3.1 规模与阶段

- Pilot：60 篇，验证字段契约、候选质量、校审速度和界面操作。
- Core v0.9：200 篇，形成可用于日常 prompt 与模型比较的基准。
- Core v1.0：约 250 篇，补齐覆盖缺口后冻结。
- 当前 55 篇 gold 作为候选种子计入上述规模，但必须补齐新增字段、证据和复核状态。

### 3.2 样本构成

最终约 250 篇中：

- 约 190 篇有效项目文章。
- 约 45 篇负样本，覆盖 irrelevant、no_chinese、chinese_unnamed。
- 约 15 篇初始争议/极难文章进入裁定队列；裁定完成后才可进入已发布 gold。

抽样不是简单随机，而是“类别分层 + 风险加权 + 固定随机种子”：

- 光伏、风电、水电、综合能源按数据库规模分配较多样本。
- 储能、制造业、输变电、核电、火电、地热及余热、生物质设置最低样本配额。
- 覆盖国家、年份、来源、项目阶段和单/多项目文章。
- 风险样本包括一期/二期、项目与设备订单混合、多个金额、多个中企角色、开发商/承包商混淆、储能状态边界和项目拆分冲突。
- Pilot 从最终配额中抽取，后续扩样不作废已有复核。

manifest 为每篇记录 inclusion reason、类别/风险标签、数据分区、来源快照哈希、原文可用性和复核状态。重复运行相同版本的 sampling 配置必须得到相同样本。

### 3.3 数据分区

- Development：约 150 篇，可查看逐篇错误，用于诊断和修改 prompt/规则。
- Regression：约 70 篇，固定评估通用回归；决定保留修改前先看汇总指标，避免针对单篇答案调参。
- Challenge：约 30 篇，集中高价值边界案例，专项报告且不混入代表自然分布的总体分数。

一旦 regression 样本被用于针对性修改，应将其转入 development，并从保留候选池补充新的 regression 样本，同时发布新的 gold 次版本和变更记录。

## 4. Gold 字段边界

### 4.1 纳入人工确认

文章级：

- status：valid / no_chinese / irrelevant / chinese_unnamed。
- 项目数量和项目拆分关系。

项目级：

- 项目名称、国家、地点。
- generation sources、storage status、主类别和项目阶段。
- 发电装机、储能功率、储能能量。
- 项目开发商。
- 中方实体及各自参与方式。
- 开工、完工、投资/财务关闭等日期。
- 总投资、合同金额、融资金额及融资模式。
- 项目摘要。
- 支撑关键字段的原文证据。

### 4.2 不纳入人工确认

- canonical ID、event ID、国家 ISO、BRI/LDC、类别代码。
- 英文翻译和名称归一缓存结果。
- source link、batch ID、创建/更新时间和 imported 标志。
- 可由已确认事实无歧义派生的展示字段。

这些字段可以作为样本定位或运行元数据保存，但不计入人工复核和准确率。

## 5. 候选生成与难度评分

每篇文章生成四路相互可辨识的候选：

1. 数据库当前值：生产系统现状。
2. 当前生产 extractor：使用 S1–S5 workflow 从固定原文快照重新抽取。
3. 独立审校模型：使用独立 review prompt，不读取生产 extractor 输出；模型/provider 可配置并记录版本。
4. 确定性解析器：校验数值、单位、日期、枚举、储能语境与派生分类。

候选均缓存，缓存键至少包含 article hash、候选来源、模型名、prompt hash、解析器版本和代码版本。单路失败不阻断其余候选，失败来源在 UI 中明确显示。

系统基于以下信号计算可解释的难度等级，而不是训练黑箱置信模型：

- 候选之间的项目数、字段值或证据是否冲突。
- 是否缺证据、只有单一 AI 支持或数据库值无法由原文支撑。
- 是否多项目、多金额、多实体或包含阶段/项目边界词。
- 数值单位是否冲突，是否出现年发电量、工厂年产能等高风险负语境。
- 数据库为空但候选找到明确值，或数据库非空但候选一致认为原文未提及。

难度结果包含具体 reason codes，供排序、抽检和报告使用。

## 6. Rich Gold 数据契约

### 6.1 单一权威结构

每条 rich gold 保存：

- 稳定 `sample_id`、source link、article ID、标题、发布日期和原文 hash。
- dataset split、sampling reasons、risk tags 和 gold schema version。
- 文章级 status 字段对象。
- 按文章内部稳定 `gold_project_id` 排列的 projects。
- 每个纳入字段的 state、value、evidence 和人工审计信息。

字段 state 受控为：

- `present`：原文明确存在且人工确认。
- `not_mentioned`：原文没有提供。
- `not_applicable`：字段不适用。
- `uncertain`：原文有歧义，不进入准确率分母。
- `needs_adjudication`：尚未完成裁定，不允许进入已发布回归集。

`unreviewed` 仅存在于 review DB 草稿，不允许出现在正式 rich gold。

### 6.2 证据结构

关键字段必须至少有一条人工确认的证据：项目拆分、类别/能源构成、装机/储能、金额、日期、开发商和实体角色。国家、地点等简单字段可不强制逐字段证据。

证据保存原文 quote、字符 start/end、article hash 和 evidence source。AI 自动预选证据；人工正常情况下只确认。原文 hash 或字符位置失效时，系统先按 quote 重新定位，再标记人工复核，不静默迁移。

### 6.3 发布投影

发布器从 rich gold 生成现有 eval 可读取的兼容 JSONL。投影规则明确映射 state：

- `present` 输出实际值。
- `not_mentioned` 输出显式 null，并通过伴随 eligibility 元数据进入“正确为空”的评分。
- `not_applicable`、`uncertain` 不进入该字段分母。
- `needs_adjudication` 和 `unreviewed` 导出失败。

现有 `gold_loader` 和 metrics 需要扩展 eligibility 语义，但旧 gold JSONL 仍可加载。

## 7. 人工复核工作流

### 7.1 队列

样本状态为 pending candidate、ready、in review、reviewed、needs adjudication、published 或 blocked。默认先显示高一致/低难度文章，以最快形成可用 gold；用户可按分区、类别、风险、难度和状态筛选。

### 7.2 先结构、后字段

每篇先确认文章 status 与项目拆分。项目结构冲突时支持新增、删除、合并、拆分和重排项目。项目结构确认后才呈现项目字段，避免在错误项目映射上浪费复核时间。

### 7.3 冲突优先界面

- 左侧显示固定原文快照和候选证据高亮。
- 右侧显示数据库、生产 extractor、审校模型、解析器/规则结果。
- 三方或四方一致且证据充分的字段默认折叠。
- 冲突、缺证据、低置信、数据库空值补充和疑似历史污染字段自动展开。
- 支持接受建议、保留数据库、手工修改、证据错误、uncertain 和送裁定。
- 支持键盘快捷键、自动保存、返回修改、仅看人工改动和一键确认本篇。

### 7.4 抽检与动态展开

- 高一致文章至少 10% 被强制展开抽检，随机种子和抽检原因写入审计记录。
- 某字段/风险类型抽检发现错误后，后续同类字段的展开比例自动提高。
- 批量接受仅适用于同一低风险规则类型，执行前展示范围和数量；项目拆分、开发商、实体角色和多金额字段禁止无预览批量接受。
- 即使全部候选一致，每篇仍需人工执行一次整体确认；系统不得自动发布。

### 7.5 保存与发布

每次操作立即写入独立 review DB，记录旧值、新值、候选来源、操作时间和可选原因。关闭浏览器后可恢复到上次位置。发布前校验未处理冲突、必要证据、项目数量、枚举、数值、状态和裁定队列；发布失败不得覆盖上一版本。

## 8. 评测体系

评测按层报告，禁止只以一个 weighted total 作决策：

- 文章级 status accuracy、各类 precision/recall/F1 和混淆矩阵。
- 项目拆分 precision/recall/F1。
- 类别、阶段、融资模式、参与方式等枚举准确率。
- generation sources 和中方实体等集合 precision/recall/F1。
- 装机、储能、投资、合同和融资金额的 ±5% 数值准确率，以及缺失/概念串位统计。
- 标准化日期精确准确率，并区分原文明示和规则推断。
- 项目名和开发商归一化相似度及人工可读差异。
- 正确值证据覆盖率和证据支持准确率。
- 储能、融资、多项目、实体角色等 challenge 专项指标。

所有指标支持按 split、类别、难度、单/多项目、正/负样本、模型和 prompt 版本切片。只有人工确认且 eligible 的字段进入分母。

每次运行 manifest 记录 gold 版本、模型/provider、prompt hash、Git commit、解析器/policy 版本、推理参数、时间和失败样本，并输出相对生产基线及上一次运行的字段改善、退步、新增错误和已修复错误。

默认发布 gate：

- regression 项目召回不得明显下降。
- regression 总体核心指标不得超过配置的容忍退步。
- 关键字段不得出现统计或样本数支持的显著退步。
- challenge 单独展示，不允许被总体平均分掩盖。
- gate 阈值在 Pilot 基线完成后依据实际波动写入版本化配置，不在没有数据前虚设数值。

## 9. 版本治理

- `v0.x`：Pilot 和持续补标。
- `v1.0`：约 250 篇全部复核、无未裁定/未复核字段后冻结。
- 标签纠错：patch 版本，例如 v1.0.1。
- 增加文章、字段或专项集：minor 版本，例如 v1.1。
- 改变字段定义、eligibility 或分类口径：major 版本，例如 v2.0。

每个版本必须包含 rich gold、eval projection、manifest、schema、distribution 和 CHANGELOG。已发布版本不可原地覆盖。开发集可用于逐篇调试；regression 被用于针对性调试后必须转入 development 并补充保留候选，随 gold 新版本记录。

## 10. 错误处理与安全

- AI 单篇失败记录错误并允许断点重跑，不中断其他文章。
- 缺失原文、hash 不一致且证据无法重定位的样本进入 blocked，不可发布。
- review DB 自动保存并定期 SQLite backup；备份失败时停止发布但不影响继续只读查看。
- 所有生产数据库连接强制只读；测试验证文件 hash、表行数与 ID 集合不变。
- 所有外部路径通过配置显式传入，工具不猜测或覆盖生产数据。
- 发布采用临时文件写完、schema 验证、原子替换新版本目标；目标版本已存在时拒绝覆盖。

## 11. 测试与验收

### 11.1 自动化测试

- rich gold schema、state/eligibility 和旧 gold 兼容测试。
- 固定随机种子的分层抽样、最低配额和去重测试。
- 四路候选缓存键、部分失败和断点恢复测试。
- 难度 reason code、冲突识别和动态抽检测试。
- review DB 自动保存、历史审计、恢复和人工锁定测试。
- 项目新增/删除/合并/拆分的结构一致性测试。
- 证据 offset、quote 重定位和 article hash 变化测试。
- rich gold → eval projection → loader 的 round-trip 测试。
- 发布拒绝未复核/未裁定/缺必要证据数据及不覆盖旧版本测试。
- 临时 SQLite 全链路测试，断言生产库 hash、行数和 ID 集合不变。
- Web API、关键页面和键盘操作的浏览器测试。

### 11.2 Pilot 验收

- 完成 60 篇并满足计划的类别、负样本和风险覆盖。
- 简单文章复核中位时间低于 2 分钟，复杂文章低于 5 分钟。
- 自动保存、关闭恢复和备份恢复无数据丢失。
- 负样本可一键确认，多项目可正确编辑结构。
- 关键字段证据可定位，抽检错误可提升同类展开比例。
- rich gold 与 eval projection 导出、加载和重跑稳定。
- 生产数据库无写入。

Pilot 未达到时间目标时，优先优化候选质量、折叠规则和界面操作，再决定是否扩至 200 篇；不以牺牲人工确认和抽检为代价盲目扩样。

## 12. 分阶段交付

1. 工具骨架、schema、只读数据访问和 60 篇确定性抽样。
2. 四路候选生成、缓存、证据定位和难度评分。
3. 冲突优先 Web 校审、项目结构编辑、自动保存和审计。
4. rich gold 发布器、兼容投影和扩展 eval 指标。
5. Pilot 复核与速度/错误率分析，依据结果优化。
6. 扩展到 200 篇 v0.9，再按覆盖报告补齐约 250 篇 v1.0。

本设计不授权在实现阶段自动调用外部模型完成全量候选，也不授权发布正式 gold；AI 批量调用、Pilot 人工复核和每次 gold 发布均是后续独立运行步骤。
