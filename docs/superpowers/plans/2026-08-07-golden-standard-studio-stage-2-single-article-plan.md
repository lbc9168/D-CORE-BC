# Golden Standard Studio 阶段 2：单篇真实样本原型计划

> 日期：2026-08-07  
> 状态：阶段 2 单篇闭环及视觉交互已获用户确认；阶段 3 尚未开始  
> 目标 worktree：`E:\Projects\BRI_RE_Database_v2\.claude\worktrees\database-refactor-expansion-aa6198`  
> 目标分支：`claude/database-refactor-expansion-aa6198`

## 1. 本计划的用途

本文档是阶段 2 的持久检查点，用于在对话上下文压缩后恢复任务细节。它只记录已确认的产品合同、真实数据审计结论、实现边界和验收路径，不代表已经开始实现。

阶段 2 未通过用户的浏览器视觉与交互验收前，不进入约 10 篇文章的阶段 3 Pilot。

## 2. 不可偏离的产品合同

实现前必须以以下文件为准：

1. `.superpowers/brainstorm/1114-1785951104/content/review-workflows.html`
2. `.superpowers/brainstorm/1571-1785984846/content/evidence-review-v1.html`
3. `docs/superpowers/specs/2026-08-06-full-database-golden-standard-studio-design.md`
4. `AGENTS.md`
5. `docs/status/`
6. `docs/changes/2026-W32.md` 中的“Golden Standard Studio 失败复盘与实现移除”

核心交互合同：

- 双栏证据复核：左侧固定原文与高亮，右侧候选比较和字段操作。
- 冲突优先：一致且证据充分的字段默认折叠，冲突、低置信、缺证据和历史污染字段默认展开。
- 草稿保存宽松、最终确认严格。
- 任意字段编辑均可立即保存；证据缺失、项目结构未完成或其他字段未完成不得阻止草稿保存。
- 只有“确认文章”或未来“发布 Gold”执行完整校验。
- 保存失败必须显示具体原因并保留浏览器中尚未提交的输入。
- 每个字段可独立确认；无候选值时仍可手工填写并保存。
- 模型提取结构化事实，确定性 policy 派生 proposed category；模型不直接决定最终 taxonomy。
- 年发电量、工厂 GWh/年产能、设备出口量、预留或可选配置不得成为 confirmed 储能容量。
- 生产数据库、正式类别、canonical ID、event ID 和前端导出始终不写入。

## 3. Git 与既有改动保护

阶段 1 确认：

- worktree 和目标分支匹配。
- 工作树已有大量未提交改动，包括旧 Golden Standard Studio 实现的完整删除。
- 这些删除和其他无关改动必须保留，不得 checkout、restore、reset 或从 Git 历史复制旧实现。
- 不创建 commit，除非用户以后明确要求。

主库阶段 1 审计 SHA-256：

```text
6AB48986C8D5B8F9B1A0E9546120F0DAF17621E48C31EE139BA09D84B039B87F
```

阶段 2 结束时必须再次核对该 hash、关键表行数和 canonical/event ID 集合。

## 4. 已选定的真实样本

- `raw_news.id`：`9027`
- URL：`https://news.goalfore.cn/topstories/detail/62720.html`
- 标题：中国在海外投资的单体规模最大电化学储能项目开工
- 发布日期：`2024-03-27 10:23:02`
- 正文长度：694 字符
- 正文 SHA-256：`664c4e4136556e3cc5b009763f6036e1506e2925eed12bd374546b3004aa700f`

文章明确包含两个独立项目：

### 4.1 安集延州洛奇项目

- 文章事实：150MW/300MWh 电化学储能。
- 地点：乌兹别克斯坦安集延州洛奇。
- 阶段：2024-03-25 开工。
- 投资：计划总投资 1.4 亿美元。
- 日期：计划 2024 年 12 月投入商业运营；原文没有具体日，不能静默表示成原文明示的 `2024-12-01`。
- 实体与角色：中国能建葛洲坝海投公司为投资方；中南电力设计院有限公司为 EPC 总承包商。
- 主库映射：`UZB-ES-0009` / `UZB-ES-0009-02`。

### 4.2 费尔干纳州奥兹项目

- 文章事实：150MW/300MWh 电化学储能。
- 地点：乌兹别克斯坦费尔干纳州奥兹。
- 阶段：2024-03-25 同期开工。
- 实体与角色：中南电力设计院有限公司为 EPC 总承包商。
- 当前文章未给出该项目投资额、明确业主、融资模式和完成日期。
- 主库映射：`UZB-ES-0010` / `UZB-ES-0010-02`。

### 4.3 高风险负语境

原文“21.9 亿度电力的调节能力”不是储能 MWh。阶段 1 已用当前确定性 parser 验证：该短语单独输入时为 `storage_status=not_mentioned`，不会生成 MW/MWh。

## 5. 真实数据审计结论

### 5.1 数据路径

目标 worktree 内现有：

- `s02a_duplicate_merger/data/db/bri_re_database.db`

目标 worktree 内缺失、但稳定工作区存在：

- `E:\Projects\BRI_RE_Database_v2\s01a_news_crawler\data\raw_news.db`
- `E:\Projects\BRI_RE_Database_v2\s01b_project_extractor\data\projects.db`
- `E:\Projects\BRI_RE_Database_v2\s01b_project_extractor\data\review.db`

用户已同意阶段 2 通过显式路径只读访问稳定工作区的上游库。不得猜测路径或写入这些库。

### 5.2 数据关系

```text
raw_news.url
  → extracted_projects.source_link
  → project_events.source_links（JSON 数组）
  → project_events.canonical_id
  → canonical_projects.id
  → chinese_entities.event_id
```

关键规模：

- `raw_news`：27,444 篇文章。
- `extracted_projects`：11,110 行。
- 主库：4,529 canonical projects、5,365 events、6,572 entities、1,531 non-project records。
- 590 篇文章关联多个 canonical project；单篇最多关联 15 个项目。

`extracted_projects` 可能因多实体而让同一项目出现多行，不能把数据库行数当作项目数。

### 5.3 当前样本的来源合并风险

- 洛奇 event 同时合并文章 `62720` 与 `62626`。
- 奥兹 event 同时合并文章 `62720` 与 `68427`。
- 奥兹 event 中的投资额、融资、developer、计划完成日期和发布日期可能来自其他来源，不应自动视为文章 `62720` 的证据。
- UI 必须区分“当前数据库聚合值”和“当前文章可支持的值”。
- 数据库没有逐字段来源或 quote/offset；阶段 2 必须建立文章级证据。

### 5.4 schema 差距

目标主库尚无以下 W32 字段/表：

- `generation_sources_json`
- `storage_status`
- `storage_power_mw_raw`
- `storage_energy_mwh_raw`
- `energy_composition_evidence`
- `project_classification_shadow`

阶段 2 不迁移主库。缺失的当前 policy 候选在工具内从固定文章快照确定性生成，并写入本地 review DB。

### 5.5 候选差距

- `projects.db` 是历史抽取结果，不是当前 W32 extractor 的可复现结果。
- 必须对 `raw_news.id=9027` 单独重跑一次当前 production extractor。
- Reviewer 必须读取固定原文和 Provider 输出，逐字段复核项目拆分、事实、证据与置信度；不得读取人工草稿或最终 Gold。
- 两路调用都要保存 provider、model、prompt hash、代码版本和错误状态。
- 当前确定性 policy 已验证两个项目均应得到：`generation_sources=[]`、`storage_status=confirmed`、`storage_power_mw=150`、`storage_energy_mwh=300`、`proposed_category=储能`。

## 6. 阶段 2 最小数据流

```text
稳定工作区 raw_news.db，id=9027（只读）
        ↓ 固定 title/content/date/url/hash
目标 worktree 主库（只读）
        ↓ URL → events → canonical projects → entities
Provider Subagent（`gpt-5.6-terra`，原文 + production prompt）─┐
Reviewer Subagent（`gpt-5.6-sol`，原文 + Provider 输出）───────┼→ 统一字段候选 + evidence
当前确定性 parser/policy ─────────────────────────────────────┘
        ↓
本地 review.db：候选快照、结构草稿、字段草稿、证据与确认状态
        ↓
FastAPI localhost 双栏复核页
```

确定性 parser 必须消费每个项目自己的候选证据片段，不能把整篇文章的一个解析结果复制给所有项目。

## 7. 阶段 2 最小文件范围

从零实现，不恢复旧 Studio：

- `external-tools/gold-standard-studio/pyproject.toml`
- `external-tools/gold-standard-studio/README.md`
- `external-tools/gold-standard-studio/src/gold_standard_studio/__init__.py`
- `external-tools/gold-standard-studio/src/gold_standard_studio/app.py`
- `external-tools/gold-standard-studio/src/gold_standard_studio/single_article.py`
- `external-tools/gold-standard-studio/src/gold_standard_studio/draft_store.py`
- `external-tools/gold-standard-studio/src/gold_standard_studio/templates/review.html`
- `external-tools/gold-standard-studio/src/gold_standard_studio/static/review.js`
- `external-tools/gold-standard-studio/src/gold_standard_studio/static/style.css`
- `external-tools/gold-standard-studio/data/.gitignore`
- 一个只覆盖单篇路径的定向集成测试。
- 完成实现后更新 `docs/status/external-tools.md` 和 `docs/changes/2026-W32.md`。

阶段 2 不创建 sampling、queue、difficulty、exporter、gold versioning、eval projection、迁移框架、权限、远程部署或批量模型调用。

## 8. 草稿与确认数据语义

字段保存两类状态，避免混淆：

1. `value_state`：`present / not_mentioned / not_applicable / uncertain / needs_adjudication`。
2. `review_action`：`unreviewed / confirmed / modified`。

草稿阶段允许：

- value 为空或结构未完成；
- evidence 为空；
- 任意冲突未解决；
- 独立保存任何单个字段；
- article status 和项目结构随时切换。

整篇确认阶段才校验：

- article status 与项目结构一致；
- 所有需要进入 Gold 的字段已处理；
- 必要证据存在且 quote/offset/hash 可定位；
- 枚举和数值合法；
- 没有未处理冲突或 `needs_adjudication`。

## 9. 页面验收合同

### 9.1 左栏

- 显示真实标题、正文、发布日期、作者和来源链接。
- 显示固定 article hash。
- 点击右侧字段可定位并高亮对应原文证据。
- 高亮区分发电构成、储能、实体角色、金额和高风险负语境。

### 9.2 右栏

- 首先显示文章状态和两个项目的拆分关系。
- 每字段显示数据库、production extractor、review model 和 parser/policy 四路候选。
- 数据库候选标明是否为跨来源聚合。
- 一致字段默认折叠；冲突、缺证据、低置信和历史污染默认展开。
- 支持接受建议、保留数据库、修改、证据错误、unknown/not applicable/uncertain/待裁定。
- 支持单字段确认和一致字段批量确认。
- 没有候选的字段仍可编辑。

### 9.3 保存与错误

- 任意编辑立即写本地 review DB。
- 草稿保存不执行发布级校验。
- 保存失败返回具体字段和原因；页面输入不清空。
- 刷新或重新打开后恢复所有修改和上次位置。

## 10. 阶段 2 必须实际走完的用户路径

1. 启动 localhost 页面。
2. 打开 `raw_news.id=9027`。
3. 核对文章原文与两个项目结构。
4. 检查数据库和三路新候选。
5. 修改至少一个真实冲突字段；推荐把奥兹项目缺乏当前文章证据的 developer 或投资额改为 `not_mentioned`，而不是接受跨来源聚合值。
6. 保存草稿。
7. 刷新页面。
8. 验证项目结构、修改值、证据和状态完整恢复。
9. 独立确认字段并批量确认一致字段。
10. 点击确认文章，验证严格校验和最终成功状态。
11. 复核生产库 hash、行数、canonical/event ID 集合不变。
12. 停止并等待用户视觉与交互验收。

## 11. 阶段 2 验收标准

- 使用真实 article 9027，不使用合成或占位数据。
- 正确识别两个项目，不把三个上游 entity 行当三个项目。
- 页面显示完整正文、数据库当前值、新候选、证据和冲突。
- 两项目分别确认 150MW/300MWh。
- 21.9 亿度调节能力不成为储能容量。
- 奥兹项目的跨来源投资、developer、融资和完工日期不被自动当作文章事实。
- 任意字段可编辑；字段无候选时也可编辑。
- 项目结构或证据未完成时仍可保存草稿。
- 保存失败不丢输入。
- 刷新后恢复修改。
- 字段可独立确认，整篇确认执行严格校验。
- 用浏览器真实完成主路径，而不是只依赖后端测试。
- 只运行与单篇路径有关的最小测试，不运行全量 pytest。
- 不写生产库、不创建 commit、不进入阶段 3。

## 12. 已确认的 Provider / Reviewer 编排

用户于 2026-08-07 明确要求采用顺序执行的两个 Codex Subagent，不为模型调用新增 OpenAI API key：

1. Provider Subagent 读取数据库中的固定新闻原文，严格复用现有 production extractor 的阶段划分、prompt、枚举和输出契约，产出项目拆分、结构化事实、逐字段证据与置信度。
2. Reviewer Subagent 随后读取同一固定原文和 Provider 的完整结构化输出，用独立 review/critic prompt 逐字段判断 `ok / wrong / unsupported / exclude`，并给出建议值、证据和置信度。
3. 两份原始输出及规范化结果都写入本地 review DB；记录执行方式、实际模型、prompt hash、代码版本、文章 hash、父候选 ID、时间和错误状态。Reviewer 必须可追溯到它审阅的 Provider 输出。
4. 两个角色均不得读取人工草稿、人工确认结果或最终 Gold，避免答案泄漏。

最终确认的 Subagent 模型为：

- Provider：`gpt-5.6-terra`。
- Reviewer：`gpt-5.6-sol`。

当前 Codex 会话的 Subagent 运行时明确提供 `gpt-5.6-terra` 和 `gpt-5.6-sol`，因此可以忠实执行 Terra → Sol 双 Subagent 组合。Provider 与 Reviewer 必须顺序运行：只有 Provider 产出并通过 JSON 契约检查后，才能把固定原文和该输出交给 Reviewer。

Codex Subagent 是本次开发会话的编排能力，不是 localhost FastAPI 应用可以在以后自行调用的运行时接口。阶段 2 由当前开发会话依次运行两个 Subagent，为固定文章生成候选快照，再由工具导入并展示；不引入 Codex CLI/SDK、API key 或新的后台模型服务。

当前阶段 2 执行方案固定为：

- Terra Provider 读取 `raw_news.id=9027` 的不可变文章快照，使用现有 production extractor 的原始阶段职责、prompt、枚举和结构化输出契约；它不读取数据库聚合项目值、人工草稿或 Gold。
- Sol Reviewer 读取同一文章快照与 Terra Provider 的完整输出，使用独立的批判式 review prompt，逐字段输出核验结论、建议值、证据和置信度。
- Reviewer 不重新伪装成一份彼此独立的候选；其输出语义是对 Provider 的审阅，必须保存 `parent_candidate_id`。
- 两个 Subagent 的原始输出先保存为工作区内 Studio-owned、可追溯的 JSON artifact，再导入本地 review DB；任何 schema 错误都明确显示，不静默补造字段。
- 不调用现有 DeepSeek/Gemini provider，不新增 `OPENAI_API_KEY`，也不修改 production extractor 的模型配置。

若未来改变 Subagent 型号，必须显式记录为新的候选运行，不能覆盖 Terra → Sol 基线。

## 13. 确认、重新打开与发布边界

用户在阶段 2 视觉验收后确认布局和主要交互可接受，并要求为误确认保留容错。最终规则为：

- 未发布的工作副本在确认后锁定字段、结构和文章状态，避免确认状态下继续编辑造成隐式保存失败。
- 确认页提供显式“重新打开复核”；重新打开后恢复编辑，可再次执行严格确认。
- 每次 `confirmed` 和 `reopened` 都写入本地 review DB 的 workflow history；当前工作副本以最后一次确认为准，但不删除此前动作。
- 未来“发布 Gold”才是不允许原地修改的不可变边界；发布后的修正必须形成新版本，不使用本阶段的 reopen 覆盖发布产物。

## 14. 阶段 3 Subagent Prompt 可复现性合同

用户确认阶段 3 应加强双 Subagent 候选链的 Prompt 可复现性。Pilot 开始前必须满足：

1. 不再只让 Terra 阅读 Prompt 源文件并一次性综合作答；由代码直接调用当前 S01B Project Extractor 的 production builder，为每篇文章渲染 `build_s1_prompt()`、`build_s2_prompt()`、`build_s34_prompt()` 和 `build_s5_prompt()` 的实际输入。
2. Terra Provider 按 S01B 内部顺序执行 S01B-S1 → S01B-S2 → 每项目 S01B-S34 → 每项目 S01B-S5；后一内部步骤只读取 production Pipeline 在同一位置本来能够读取的原文和结构化中间结果。
3. 每阶段保存 prompt template 路径、完整 rendered prompt、SHA-256、builder 代码版本、输入文章 hash、Subagent model、推理配置、原始输出、规范化输出、错误状态和耗时。
4. S01B-S5 必须基于 Terra 前序步骤的结构化输出重新渲染，不能预先静态生成；多项目文章必须保存每个项目独立的 S01B-S34/S01B-S5 prompt 与 parent run ID。
5. Sol Reviewer 只读取固定原文、Terra 最终输出、受控枚举和独立 review/critic prompt；保存 review prompt/hash，并通过 `parent_candidate_id` 指向被审阅的 Terra run。
6. production Prompt 或 builder 代码发生变化时必须生成新的 candidate run/version，不覆盖旧候选；同一次 Pilot 不得静默混用不同 Prompt hash。
7. Subagent 仍由 Codex 会话编排，不新增 OpenAI API key；若离开当前会话运行，必须先单独设计可重放的执行入口，不能让本地 Web 页面假装能够直接创建 Subagent。

该合同是阶段 3 的进入条件，不回溯重跑已经通过交互验收的阶段 2 样本，也不代表已获准开始 Pilot。

## 15. 阶段 3 人工验收方式

用户于阶段 2 验收后明确取消阶段 3 的人工复核计时和速度统计。阶段 3 改为：

- 完成约 10 篇真实文章的队列和完整复核交互，供用户直接使用测试。
- 不采集首次打开时间、活跃操作时间、单篇耗时、平均/中位复核时间、停留时间或用户操作速度。
- 不为计时增加前端埋点、会话追踪或报告逻辑；本地草稿仍保留正常的更新时间和 workflow history，用于数据一致性而非绩效分析。
- 用户在实际使用中发现的卡点，由用户直接反馈后再定向修正，不预先建设卡点分类或自动分析系统。
- 阶段 3 验收以“约 10 篇真实样本可顺利打开、比较、编辑、保存、刷新、重新打开和确认”为准，随后停下等待用户决定是否进入阶段 4。

这里将用户口述的“把视频做出来让我测试”按上下文理解为“把这批约十篇 Pilot 做出来让我测试”；当前不把录制演示视频列为交付物。如用户以后明确要求视频，再单独增加。
