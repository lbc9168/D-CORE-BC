# external-tools 状态

> 最后更新：2026-08-06（Golden Standard Studio 最终加固 A 修复轮 2 完成）

---

## 概述

`external-tools/` 目录存放不属于主流水线的一次性辅助工具。这些工具独立运行，不依赖 crewai 框架，可直接用 `uv run --with <deps>` 执行。

---

## gold-standard-studio — 全库金标准复核工作台

### 功能

为 Full-Database Golden Standard Studio 提供与主流水线隔离的人工复核数据契约。富金标准记录保留字段状态、文章级证据、项目实体角色和候选来源；正式发布前要求整篇文章已人工复核，且不存在 `needs_adjudication` 字段。

### 当前状态

| 项目 | 状态 |
|------|------|
| Pydantic 富金标准、证据与候选契约 | ✅ 已完成 |
| 统一字段注册表（证据要求 + 评测映射） | ✅ 已完成 |
| Schema 边界回归覆盖 | ✅ 已完成 |
| 只读数据源与不可变快照（仅待导入标签、稳定排序、严格链接 JSON、WAL 逻辑哈希） | ✅ 已完成 |
| 确定性分层抽样（去重、严格负样本/类别配额、风险加权、Pilot/正式分区与 manifest） | ✅ 已完成 |
| SQLite 复核存储（规范 JSON 草稿/候选、`BEGIN IMMEDIATE` 人工决策、完整 before/after 审计、会话恢复与队列） | ✅ 已完成 |
| SQLite 原生备份（完整性、全表行数与审计尾部校验） | ✅ 已完成 |
| 四路独立候选（数据库、生产提取、独立审校、确定性解析）、严格缓存与单次渲染审校 prompt | ✅ 已完成 |
| 证据精确定位与安全重定位（原文 hash、offset、歧义阻断） | ✅ 已完成 |
| 字段注册表驱动冲突识别（全局一对一跨源身份对齐、枚举/日期、±5% 数值、集合及名称归一） | ✅ 已完成 |
| 可解释难度评分与固定种子抽检（风险标签动态提升） | ✅ 已完成 |
| 本地复核 API（队列、文章上下文、字段/结构/裁决、事务内确认守卫、已确认草稿不可变与完整审计） | ✅ 已完成 |
| 冲突优先 Web UI（并发安全只读源库、审计顺序结构门控、候选与人工证据 SHA 校验、结构编辑、审计、自动保存与快捷键） | ✅ 已完成 |
| rich gold 发布器（原子版本目录、证据/审计保留、兼容 eval 投影与发行清单） | ✅ 已完成 |
| 安全 CLI（init/sample/build-candidates/serve/publish/report）与 Pilot runbook | ✅ 已完成 |
| 最终核心加固（权威文章状态、完整状态机、严格确认门、路径身份隔离、快照身份、顺序迁移） | ✅ 已完成 |
| 最终加固 A 修复轮 1（迁移撤权/恢复、竞态隔离、完整 UI/API、严格发布契约） | ✅ 已完成 |
| 最终加固 A 修复轮 2（安全备份/发布、可验证 provenance、Host/CSRF/actor、真实 UI 发布） | ✅ 已完成 |

发布器只读访问 Studio review DB；仅当草稿和索引均为 `reviewed`、存在匹配 `article_confirmed` 审计且其后没有 gold 草稿变更时才写入版本目录，确认后的候选观测不影响资格。每次发布先在同级临时目录生成并以 Pydantic 校验 rich/eval JSONL、manifest、schema、distribution 与 changelog，再 fsync 后以协作版本锁和平台 no-replace rename 原子提交；版本必须是 `vMAJOR.MINOR.PATCH` 且不可覆盖。投影将 `field_eligibility` 保留在每个项目内：`present`/`not_mentioned` 为可评测，`not_applicable`/`uncertain` 为不可评测；发布 rich JSON 仅保留 allowlist 的确认审计 ID、动作、时间和字段路径，不发布任意原始 audit metadata。

Studio 现提供 `gold-studio` 命令：`sample` 默认为真正的无写入 dry-run，只有 `--apply` 才向 Studio-owned review DB 和 manifest 写入；60 篇/11 个负样本与稀有类别下限不足时 fail-closed。`build-candidates --all` 显示精确数量并要求确认（或 `--yes`），CLI 默认完全禁用外部模型调用。`serve` 默认为 `127.0.0.1`，非 loopback 必须 `--allow-network`。`publish` 要求显式版本并复用不可变发布 gate；`report` 输出无网络依赖、带语义表格的可访问 HTML。完整运行、备份/恢复、成本和故障处理见 `external-tools/gold-standard-studio/README.md`。

Task 12 fix round 1 将候选难度/风险保存为 review DB 的观察性 `sample_scoring`（`BEGIN IMMEDIATE` + 独立审计），不重开或改写已确认 rich gold；队列和完成后报告均读取该持久评分。`build-candidates --all` 现覆盖所有已种子样本，`--resume` 只跳过已保存 candidate bundle 的样本。`init` 在创建 Studio 文件前检查所有只读 SQLite 表/列契约；`sample --format json` 输出可复算的前后逻辑哈希、物理诊断哈希、行数/ID digest、配额与 unchanged 证据。

Task 12 fix round 2 规定 `sample --format json` 的 stdout 永远只有一个 JSON document：dry-run 为 `applied:false`，`--apply` 在同一对象中附加 `applied:true`、`seeded_count`、安全的 review DB/manifest 文件名，不再将 apply 进度文本混入机器输出。

评测器现已消费逐项目 eligibility、分派全字段指标并输出 split/category/risk 切片：`not_mentioned` 的 null 按显式空值计入分母，`not_applicable`/`uncertain` 仍排除；地点采用与 Studio evidence 相同的 normalized-exact。评测的显式 FIELD_PRIMARY_METRIC 映射将发电源指定为 set F1（精确集合准确率仅供诊断），避免 full/core 汇总退化为通用 accuracy。Studio exporter 通过不污染全局 `sys.path` 的私有 importlib 包加载仓库唯一的 `eval.gold_loader` 契约，因此从 Studio 独立工作目录导入也可用。

发布投影的唯一校验契约现为 `eval.gold_loader.GoldRecord`：写入前和临时 JSONL 重读时均使用该模型，Studio 不再维护宽松的重复 projection schema。Loader 对 canonical gold 使用严格 JSON 类型、受控 category/stage/financing enum、受控发电源和严格数值字段，拒绝例如布尔值伪装的数值；验证失败在版本目录可见前清理 staging。

最终核心加固将文章级 gold status 与 workflow review status 完全分离，只允许人工审计的 `valid`、`no_chinese`、`irrelevant`、`chinese_unnamed` 进入发布，并把抽样种子、原始类别分层、风险、纳入原因与 negative proxy 固定为独立 `sampling_provenance`。`valid` 必须至少有一个项目且每个项目精确覆盖 19 个注册字段；负样本必须为空项目，并由单事务 `confirm-negative` 写入 status 与 confirmation。字段类型/受控值、实体角色、证据 hash/bounds/quote 都在保存、确认和发布边界重复校验。

Review DB schema 现以显式版本和顺序事务迁移管理，新增不可变 `article_snapshots` 与索引化 article status，拒绝损坏、缺失或未来版本元数据。证据重定位只接受 canonical `ArticleSnapshot`（标题、正文、发布日期共同计算 hash）。所有 Studio 可写路径在配置和写入边界与三份只读源库比较 resolved path、symlink/samefile 及 device/inode hardlink 身份；回归覆盖普通路径、符号链接和硬链接别名。临时三库端到端测试包含一个全字段、实体角色与原文证据齐全的真实 `valid` 项目以及三种负样本状态，发布前后源库物理 hash、行数和 ID 集保持不变。

最终加固 A 修复轮 1 进一步规定：v1/v2 数据迁移一律撤销旧 reviewed/status 权威，只保留 `legacy_migration_hint` 并写 `migration_rereview_required` 审计；`rehydrate-snapshots` 只能从已配置只读 `SourceRepository` 恢复缺失快照，恢复后必须重新作出 status 与 confirmation。review SQLite 打开会持有 no-follow 文件句柄并核对 claimed/path/SQLite 实际 inode，配置、manifest、candidate cache 与 report 采用 sibling-temp、fsync、身份复查和原子 replace。正式/API Pydantic 边界同时启用 `strict=True` 与 `extra=forbid`，候选 envelope 保持宽容；FIELD_REGISTRY 的 `evidence_required` 在保存、确认、发布重复强制。UI 暴露专用 adjudication 动作、多证据 span、实体增改删与受控角色。`valid` 至少需要一个具名中方实体、非 `Unknown` 角色及证据；`chinese_unnamed` 明确为“有中方参与但原文未具名”的零项目负样本，不是 valid 占位实体。投影逐项目输出全部 19 个字段键，`not_mentioned` 为显式 null 且 eligible=true。

最终加固 A 修复轮 2 将 SQLite backup 的临时目标纳入 held-handle/no-follow/identity 校验，并将 rich、projection、schema、distribution、changelog、manifest 六类 staged artifacts 全部切换到源库别名感知的安全原子写入。ReviewStore schema 升至 v4：迁移值只成为显式 `legacy_unknown`/`verified=false` provenance 和审计提示，必须经 Store/API/`verify-provenance` CLI 的 operator-reviewed 决定替换后才能确认或发布；其中 operator provenance 必须与 confirmation 之前的 `sampling_provenance_decision` canonical payload 精确一致，单独伪造 verified flag 无效。v1/v2 会撤销推断 status，v3→v4 则保留 article status、重置复核 workflow。eval loader 对旧 gold 继续兼容缺少 verification 字段的 provenance。Web mutation 只接受本地 TrustedHost、`X-Gold-Studio-Request: 1`、非空 bounded actor 及可选的精确同源 Origin，真实 Chromium 回归从空项目通过页面完成 19 字段、专用裁决、双字段证据、具名实体/受控角色/双实体证据、状态与确认，再由公共 CLI 发布并校验 rich/eval projection。安全边界假定可信本地操作员；原子写/no-follow/源别名检查用于防止误操作和已检测路径替换，不承诺抵御同一 OS 身份的恶意进程在最终校验与 commit 之间发起微竞态。

最终验证：本次 provenance audit-binding 最小收尾回归 `8 passed`，覆盖伪造/已审计 payload、Store/API/CLI 修复、正负确认、v1 重新复核与真实 Chromium UI→CLI 发布；此前 Studio 完整测试（含真实 Chromium）`212 passed`，eval loader/CSV 兼容测试 `15 passed`（仅保留现有 Windows pytest cache ACL warning）。

---

## data-combination — 外部 Excel 数据补充工具

### 功能

将两个手动收集的 Excel 数据源中的网址和财务信息，补充到 `s01b_project_extractor/data/projects.db` 的 `extracted_projects` 表中。

**核心约束**：不修改已有数据、不新增项目行，只填充 NULL / 空 / "Unknown" 字段。

### 文件

| 文件 | 说明 |
|------|------|
| `step1_match_simple.py` | 处理 File 1（精确匹配） |
| `step2_match_ai.py` | 处理 File 2（模糊 + Gemini AI 匹配） |
| `requirements.txt` | 依赖：pandas, openpyxl, rapidfuzz, google-genai, python-dotenv |
| `2025-BRI RE-10to12_Intern_checked.xlsx` | 数据源 1（459 行，微信公众号手工整理） |
| `202210-202509-full-old.xlsx` | 数据源 2（943 行，旧数据库） |
| `logs/step1_log.csv` | 运行时生成：Step 1 匹配审计日志 |
| `logs/step2_log.csv` | 运行时生成：Step 2 匹配审计日志 |

### 字段映射

**File 1 → projects.db**（匹配键：`Source` ↔ `source_link` 精确匹配）

| Excel 列 | DB 列 |
|---|---|
| Official Website link | official_website |
| Non-official Website Link | non_official_website |
| Total Project Investment (Million USD) | total_investment_usd |
| Subcontract Amount (Million USD) | subcontract_amount_usd |
| Investment (or Financial Close) Date | investment_date |
| Financing Model | financing_model |
| Additional Information | additional_remarks |

**File 2 → projects.db**（匹配方式：rapidfuzz 模糊评分 + Gemini 确认）

| Excel 列（索引） | DB 列 |
|---|---|
| col 15 Remarks (Official Website Link) | official_website |
| col 16 Remarks (Non-Official Source Link) | non_official_website |
| col 17 Total Project Investment (Million USD) | total_investment_usd |
| col 18 Subcontract Amount (Million USD) | subcontract_amount_usd |
| col 19 Investment (or Financial Close) Date | investment_date |
| col 21 Remarks（备注） | additional_remarks |
| col 22 Financing Model | financing_model |

### 匹配策略（Step 2）

1. **预过滤**：按 `country_iso` 精确过滤 DB 候选集
2. **模糊评分**：project_name 40% + implementing_entity 30% + capacity 20% + location 10%
   - ≥ 90 分：自动匹配
   - 55–90 分：Gemini（gemini-2.5-flash-lite）二次确认
   - < 55 分：跳过
3. **填充规则**：DB 字段为 NULL / 空 / "Unknown" 时才填入

### 运行方式

```bash
cd external-tools/data-combination

# Step 1（精确匹配，无需 API Key）
uv run --with pandas --with openpyxl step1_match_simple.py
uv run --with pandas --with openpyxl step1_match_simple.py --dry-run

# Step 2（AI 辅助匹配，需要 GEMINI_API_KEY）
uv run --with pandas --with openpyxl --with rapidfuzz --with google-genai --with python-dotenv step2_match_ai.py
uv run ... step2_match_ai.py --dry-run
uv run ... step2_match_ai.py --skip-gemini   # 仅执行 ≥90 分的自动匹配
```

### 状态

| 项目 | 状态 |
|------|------|
| step1_match_simple.py | ✅ 已完成（待运行验证） |
| step2_match_ai.py | ✅ 已完成（待运行验证） |

---

## external-link-adder — 自动填充项目网站链接

### 功能

为 `projects_by_source.xlsx`（约 6389 条全球能源项目记录）的 `official_website` 和
`non_official_website` 两列自动搜索并填充链接。

**跳过条件**：任意一列已有值则跳过该行。

**搜索策略（两轮）**：
1. 第1轮：`{implementing_entity} {project_name}`（`implementing_entity` 为空时跳至第2轮）
2. 第2轮：`{project_name} {country}`（第1轮无结果时执行）

**链接判断**：Gemini 2.5 Flash Lite 分析搜索结果，区分 official / non-official 类型：
- official：实施企业官网或项目专属官网
- non-official：行业媒体、政府公告、国际机构报告等可信第三方

### 文件

| 文件 | 说明 |
|------|------|
| `fill_links.py` | 主脚本 |
| `requirements.txt` | 依赖：pandas, openpyxl, google-genai, python-dotenv, tqdm, requests |
| `.env` | 存放 `SERPER_API_KEY`（`GEMINI_API_KEY` 从根目录 .env 读取） |
| `projects_by_source.xlsx` | 输入数据（约 6389 行） |
| `projects_by_source_filled.xlsx` | 运行时生成：填充后的输出 |
| `search_log.txt` | 运行时生成：TSV 格式搜索日志 |

### 工程特性

| 特性 | 实现 |
|------|------|
| 断点续传 | 启动时读取已有输出文件合并进度；每50条自动保存 |
| 速率限制 | Serper 搜索后 sleep 0.5s；Gemini 调用后 sleep 0.3s |
| 错误处理 | 单条异常记录 status=error 并继续 |
| 进度显示 | tqdm 进度条 |
| 日志 | search_log.txt（TSV），字段：row_num / query1 / query2 / 链接 / reason / status |

### 运行方式

```bash
cd external-tools/external-link-adder

# 测试：仅处理前10条，不保存文件
uv run --with pandas --with openpyxl --with google-genai --with python-dotenv --with tqdm --with requests fill_links.py --limit 10 --dry-run

# 正式运行
uv run --with pandas --with openpyxl --with google-genai --with python-dotenv --with tqdm --with requests fill_links.py
```

### 状态

| 项目 | 状态 |
|------|------|
| fill_links.py | ✅ 已完成（待运行验证） |

---

## codex_review — Codex 季度分拣管线

### 功能

季度分拣闭环：从主库/审计清单/实体解析未命中记录导出待复核队列 → 交给外部 LLM（或人工）分拣判定 → 校验回收判定结果（分级信任 + 抽检）→ 抽检确认后落库/生成补录产物。三条任务线：

- **T1**：漏合并候选对——同国别同类别下名称相似度高的项目对，判断是否应合并。
- **T2**：范围复核——疑似不应收录（无具名中企实体 / 命中 MF 边界关键词 / 审计清单点名）的项目，判断保留/排除/降级。
- **T3**：实体归属补录——高频出现但未收录进集团层级库的原始实体名，判断其归属（新集团/已有集团别名或子公司/非中国实体/非公司/存疑）。

### 文件

| 文件 | 说明 |
|------|------|
| `codex_review/schemas.py` | Pydantic v2 数据契约：队列条目（`T{1,2,3}QueueItem/File`）与判定结果（`T{1,2,3}Verdict`、`VerdictsFile`） |
| `codex_review/ledger.py` | 判定台账：内容哈希幂等 key（`t1_key`/`t2_key`/`t3_key`）+ 原子写（`load_ledger`/`save_ledger`/`record`/`is_settled`） |
| `codex_review/export_queue.py` | 导出 CLI：从主库（T1/T2）或 misses CSV（T3）生成 `queue_T*.json` + 复制 `guide_T*.md` |
| `codex_review/ingest_verdicts.py` | 回收 CLI：校验 verdicts 信封+schema，按规则分级（自动收/gated/uncertain 跳过），`--apply` 时写台账 + 生成 `apply_plan_T*.json` + 15% 抽检 `manual_check_T*.csv` |
| `codex_review/apply_actions.py` | 落库 CLI：T1 事件迁挂合并、T2 置排除标记（均含备份+单事务+幂等自检+重放检查）；T3 只生成 `registry_additions_<quarter>.json`，不动库 |
| `codex_review/guides/guide_T{1,2,3}.md` | 分拣规则+输出格式说明模板（导出时复制到对应季度目录，供分拣者/Codex 阅读） |
| `codex_review/runs/<quarter>/` | 每季度产物目录：`queue_T*.json`、`guide_T*.md`、`verdicts_T*.json`（分拣者产出）、`ingest_report_T*.md`、`apply_plan_T*.json`、`manual_check_T*.csv`、`apply_report_T*.md`、（T3）`registry_additions_<quarter>.json` |
| `codex_review/ledger.json` | 全局判定台账（跨季度累积，不存在时视为空台账） |

### 运行方式

完整五步命令与常见问题见 `docs/rules_and_plans/codex_quarterly_runbook.md`：① 导出三队列 ② 分拣（Codex/人工，数据契约不绑供应商）③ ingest_verdicts 回收校验（dry-run → --apply）④ apply_actions 落库（人工复核抽检后再 --apply）⑤ 收尾（docs/changes 记录 + 视情况走 en_backfill → run_export → 部署）。

### 关键约束

- 主库（`s02a_duplicate_merger/data/db/bri_re_database.db`）在 export/ingest 阶段全程只读；只有 `apply_actions --apply` 才写库，且写前自动备份、写后自动跑外键+前缀一致性自检与重放检查，任一失败非零退出。
- 台账（`ledger.json`）幂等：已定案的 pair/project/raw_name 不会重复入队，也不会被 ingest 重复处理。
- gated（要改数据）类判定强制抽检：`ingest_verdicts --apply` 默认按 15% 比例（`--sample-rate`）确定性抽样进 `manual_check_T*.csv`，人工复核后才可执行 apply_actions。
- T3 不直接写 registry/主库，产出物需人工三步（合并进 draft → 重跑 `build_hierarchy_v2.py` → s01b 跑 `entity_reparse_maindb --apply`）才生效。
