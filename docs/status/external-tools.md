# external-tools 状态

> 最后更新：2026-08-15（Gold Standard Studio 独立策略导入路径修复）

Gold Standard Studio 独立启动时会显式加载仓库根目录与 S01B `src`，确保确定性能源策略及 `bri_enums` 可在未安装主流水线包的情况下导入。

---

## 概述

`external-tools/` 目录存放不属于主流水线的一次性辅助工具。这些工具独立运行，不依赖 crewai 框架，可直接用 `uv run --with <deps>` 执行。

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
| fill_links.py | 历史工具，已有运行日志与填链结果；仅搜索标题/摘要，不满足新版正文核验准入要求。旧结果不得直接作为新版网页参考。 |

新版 [s02e_source_finder](s02e.md) 按候选独立核验并展示合格链接，不设覆盖率硬门槛。搜索/网页/格式失败不阻断其他成功结果，制造年产能单位已补齐，已有抽取可跨规则重判。13条候选仅作开发验证；本轮零外部API，混合批次和真实详情成功/空状态已验证。来源待核实及范围歧义单独隐藏。旧工具及step1/step2历史脚本不作为本轮回填路径；已有核验结果已接入本地s03/s04正式JSON并构建dist，线上未发布本轮链接，不启动全量找源。

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

---

## gold-standard-studio — Pilot 与 Stage 4E 五十篇复核队列

> 最后更新：2026-08-13；状态：十篇人工复核已发布 `pilot-v0.1.1`；Stage 4E 固定 40 篇（24 development + 16 challenge）已完成 40 对 Terra Provider/Sol Reviewer 候选，23627 异常已审计重跑，累计 50 篇可进入复核，blind 仍为 0。

### 当前范围

- Pilot 固定 10 篇真实文章并全部计入 development；Stage 4E 新增 40 篇且与 Pilot ID 完全不相交，分为 development 24、challenge 16、blind 0。累计划分为 development 34、challenge 16。
- `/queue` 分组显示 Pilot development、Stage 4E development 和 Stage 4E challenge 的真实标题、候选链状态与本地草稿/确认进度；复核页在全部 50 篇间提供上一篇、下一篇和返回队列导航。
- development/challenge 都显示冻结的 Terra/Sol 辅助候选；challenge 表示未来固定困难回归集，不再用于网页“首次判断隐藏候选”实验。未触碰的 revision=1 自动草稿在 Provider 到达时安全升级，任何已编辑或已确认草稿均不覆盖。
- 保留阶段 2 双栏证据复核、冲突优先、任意字段宽松草稿、刷新恢复、严格确认、确认锁定和显式重新打开语义；项目候选按名称与 Terra 项目对齐，不按数据库行号错配。
- 复核字段现分离 S01B 事实层与 S02B 规则层：`entities/location` 只保存原文实体、角色和地点，`normalized_entities/normalized_location` 单独保存标准实体、母公司和标准地点；Terra/Sol 不因未执行母子公司归一化而产生伪错误。
- S02B-2 标准地点预填遵循正式去重逻辑：`location_norm` 非空时优先使用，否则回退原始 `location`；只有两者都为空才显示未提及。旧版空预填在文章重新打开时立即持久化为 `unreviewed` 新候选，避免不刷新页面便确认时仍读取旧 JSON；明确的人工 `modified` 值不会被覆盖。
- 规则层字段可以独立采用、编辑、确认和保存；母公司或标准地名不要求逐字出现在新闻。事实层只有 `present` 字段要求可精确定位的原文证据，`not_mentioned/not_applicable/uncertain` 可以无 evidence；候选适配器不再把 unknown 或字面 not_mentioned 误转成 present。旧草稿只补齐缺失的新字段，不覆盖既有人工值。
- 负样本页面仍显示 Terra/Sol 文章级判断、原因及数据库/Pipeline 历史关联项目；最终确认要求非项目原因，但草稿不受该约束。
- 不记录人工复核耗时、停留时间或操作速度；Master Plan 当前规划点按最新目标调整为 200 篇（development 90、challenge 50、blind 60），但当前只实施 Stage 4E/4F 40 篇，不建设完整 200 篇、权限或远程部署。

### 阶段 4A：不可变 Gold 发布与分层评估

- 新增 `pilot_release.py`，以只读方式重新校验十篇草稿的确认状态、完整字段、review action、原文证据和文章快照 hash；任何问题均逐篇返回并 fail closed。
- `pilot-vX.Y.Z` 发布使用同级临时目录生成并重新读取校验，成功后原子改名；既有版本拒绝覆盖。manifest 固定文章清单、复核库 hash、状态/项目统计和每个 artifact 的 SHA-256。
- 一份 rich Gold 同时投影为 S01B 事实层、现有 `eval/gold_loader.py` 兼容层、S02A 项目边界层、S02B 实体/地点归一化层及端到端最终值层。
- 基线明确标记为 `stored_pipeline_snapshot`：S01B 读取已有 `projects.db`，S02A/S02B/端到端读取当前主库快照；不调用模型，报告同时写入只读源数据库路径及 hash 和指标限制。
- 隔离发布测试覆盖未确认阻断、完整发布、artifact hash、legacy loader 兼容、同版本不可覆盖和一致样本基线；2026-08-11 真实发布预检为 `10/10 ready`，无 blocker。
- 已生成 `data/releases/pilot-v0.1.0`：10 篇、14 个 Gold 项目，文章状态为 valid=6、no_chinese=3、irrelevant=1；rich Gold 和五种投影均为 10 行，manifest artifact hash 全部复核一致，legacy Gold loader 成功读取 10 条。
- stored Pipeline 首轮诊断基线：valid/non-valid 准确率 `0.800`；S01B 项目 precision/recall/F1=`0.737/1.000/0.848`；S02A 文章内项目集合 precision/recall/F1 同为 `0.737/1.000/0.848`；S01B 字段 micro accuracy=`0.441`、S02B 字段=`0.429`、端到端字段=`0.489`。这些是 10 篇 Pilot 的快照诊断，不用于直接选择模型。

### 可复现双 Subagent 候选链

- 命名统一为“顶层模块 + 内部步骤”：S01B 是 Project Extractor，S01B-S1/S2/S34/S5 是其内部步骤；S02A 是项目 duplicate merger/入库，S02B 才包含公司实体归一化及 S02B-2 地点归一化。旧 artifact 的 `s1/s2/s34/s5` 键保留为稳定机器契约。
- 新增 `prompt_pack.py`，代码直接调用当前 production `build_s1_prompt/build_s2_prompt/build_s34_prompt/build_s5_prompt`；保存固定文章快照、完整 rendered Prompt、Prompt SHA-256、builder 文件字节 SHA-256、逐步骤原始输出和动态 S01B-S5 输入。
- Provider 使用 `gpt-5.6-terra` 严格执行 S01B-S1→S01B-S2→逐项目 S01B-S34→逐项目 S01B-S5；`irrelevant/no_chinese_participation` 按 production 规则在 S01B-S1 短路。
- Reviewer 使用 `gpt-5.6-sol`，只读取同一固定原文和 Terra 完整候选，通过独立 critic prompt 逐字段审校，并以 `parent_candidate_id` 指回 Terra。
- 两种 Subagent 均不读取数据库候选、人工草稿或 Gold；Web 应用不调用模型、不使用 API key。所有非空 evidence 均验证为对应正文的单一连续逐字子串。
- 模型只提取结构化事实；页面继续按每项目自己的证据调用 `energy_taxonomy_v1` 确定性派生 proposed category。

### Pilot 真实发现

- 当前 production S01B-S1 将文章 `10069` 与 `18491` 判为电力项目但无中方参与，Sol 独立同意；工具保留它们与历史数据库项目的冲突，而不为匹配旧库强改候选。
- `1586` 的 Sol Reviewer 将港口描述保留为“首选地点”，未提升为确定项目地址。
- `14595` 明确 8MWp 是光伏容量、12MWh 是本次储能容量，未混入全球案例背景。
- `1533` 的 Terra 将瑞达一期/二期合并，排除换电站、纯 MOU 和战略内容；保留原文 `9Wh`，且未把 `480GWh` 年发电量当装机或储能容量。
- 浏览器验收发现并修正 policy 展示的证据串扰：`95MWp` 光伏装机不再被解析成储能功率；发电证据与储能证据分别进入确定性 parser，当前显示光伏、confirmed 储能、`storage_power=null`、`storage_energy=218MWh`。

### 验证与门控

- 阶段 4A 定向测试当前 `12 passed`，覆盖原有单篇/队列/分层交互、标准地点显式映射/原地点回退/旧草稿安全升级与 reopen 持久化，以及发布 fail-closed、无半成品、hash、legacy loader 兼容和不可覆盖；未运行全量 pytest。
- 9 篇新增文章的 Prompt manifest 文件/hash 已逐项核验无不匹配；所有候选 JSON、父候选关系和 evidence 子串均已校验。
- 真实浏览器已完成：队列 → 8 项目复杂文章 → policy 容量检查 → 三一硅能事实层/三一集团规则层展示 → 规则层修改/自动保存/刷新恢复/还原 → irrelevant 负样本编辑/保存/刷新恢复/恢复原值 → no_chinese 样本历史数据库/Pipeline 关联显示；没有替用户确认新增文章。
- 阶段 4A 未修改上游三份 SQLite，也未创建提交。发布后 raw/extraction/main SHA-256 仍分别为 `3CFE53B...B60DD`、`B7D364C...8FC6B`、`6AB48986...B87F`；后续任何 Gold 修订必须使用新版本号，不能覆盖 `pilot-v0.1.0`。

### 阶段 4B：类型化分层评估器

- 新增 `layered_evaluator.py`：运行前核验不可变 release 的 artifact hash，使用全局一对一项目对齐和字段类型语义，原子生成 `report.json`、`errors.jsonl`、`summary.md` 与 `run-manifest.json`；同一 run id 拒绝覆盖。
- 比较规则区分数值容差、部分精度 ISO 日期、规范化文本、集合、实体元组及投资金额结构；Gold 有证据但没有可比较规范值、快照没有存储该字段、或货币/结构不兼容时记为 `unscorable`，不伪装成模型错误。
- 明确支持 article validity gate、S01B 事实字段、S02B 实体/地点归一化和 stored end-to-end；完整跨文章 S02A duplicate clustering 与 S03 省级地点推断在当前固定快照中标记为 unsupported。
- 修正历史 Pipeline 页面适配器残留的原型兜底：不再为无储能信息的文章注入 `300MWh`，非储能项目的 `capacity_mw` 不再映射为储能功率；该修正只影响本地展示/评估适配，不修改数据库。
- 已生成不可变评估运行 `data/evaluations/pilot-v0.1.0/stored-snapshot-v2`：valid/non-valid accuracy=`0.800`，S01B 项目 P/R/F1=`0.737/1.000/0.848`，S01B/S02B/end-to-end 可评分字段 micro accuracy=`0.644/0.643/0.656`；共记录 mismatch=`180`、unscorable=`66`。这些仍是历史存储快照诊断，不是新模型实验。
- 阶段 4B 定向测试为 `16 passed`，未运行全量 pytest；未调用模型，未修改 Prompt、taxonomy、Gold、上游数据库或正式导出，也未创建提交。

### 阶段 4C：冻结 Terra/Sol 对照实验

- 新增 `frozen_candidate_experiment.py`，严格读取 Gold 发布前由 Codex Subagents 生成的十篇 `gpt-5.6-terra` Provider 与 `gpt-5.6-sol` Reviewer artifact；校验完整文章 ID 集合、模型名、文章 hash、Reviewer 父候选关系和可用 Prompt provenance 后再评分。
- 实验只评分可直接归因的 S01B 文章状态、项目结构及事实字段；候选 `category` 没有完整 deterministic taxonomy trace，`subcategory_raw_fact` 也不等于最终 subcategory，因此不进入模型主指标。S02A/S02B/S03 同样排除。
- 已生成不可变实验 `data/experiments/pilot-v0.1.0/frozen-terra-sol-v1`：Provider 与 Reviewer 四分类/二元门控均为 `1.000/1.000`，项目 P/R/F1 均为 `1.000/1.000/1.000`；类型化字段 micro accuracy 分别为 `0.917/0.903`，Reviewer delta=`-0.015`。
- Reviewer 相对 Provider 修复 7 个 mismatch、新增 10 个、共有 10 个不变；日期起止字段各提升 `+0.250`，但实体、开发商、项目名和阶段相对当前 Gold 下降。新增差异包含“拒绝把收购方当开发商”“拒绝把区域角色复制到各项目”“保留首选址限定”等保守判断，属于下一轮 Gold/规则审计候选，不能自动判作 Reviewer 推理错误。
- 这十篇复核时曾展示模型候选，因此报告明确标记为 development set，不作为无泄漏泛化排名。文章 9027 早于完整 rendered Prompt pack，只能记录 candidate source paths；其余九篇保留 rendered Prompt/manifest hash。Subagent runtime 不暴露 temperature/seed，本轮诚实记录为 unavailable，且新模型调用数为 0。
- 当前 Studio 定向测试为 `19 passed`，未运行全量 pytest；实验不修改 Prompt、模型、taxonomy、Gold、候选、上游数据库或正式导出，且同 experiment id 拒绝覆盖。

### 阶段 4D：标注口径审计与 `pilot-v0.1.1`

- 新增长期标注合同 `docs/rules_and_plans/golden_standard_annotation.md`，固定五条规则：收购方不自动等于开发商、组合/区域能力不下放为逐项目角色、候选场址不进入项目名且地点只保留确定层级、收购不等于融资、参建可以支持在建但完工/投运优先。
- 定向核对冻结实验的 10 个 Reviewer 新增 mismatch：修改 2 篇文章的 9 个字段；保留达尔汗 P07 的 `参建→在建`。其中罗马尼亚项目按正文将开发商从收购方“三一集团”纠正为明确的“Enero”，融资改为未提及；P02–P06 保留三一硅能实体但移除无逐项目证据的统一角色；塞尔维亚 P08 收购方不再标开发商；1586 项目名移除候选阿德赛尔港而保留确定的英国/苏格兰。
- 审计脚本 `scripts/apply_v011_guideline_audit.py` 对旧值 fail closed，先做 SQLite backup，再逐篇 reopen/save/strict confirm；产物位于 `data/audits/pilot-v0.1.1-guideline-audit`，含 9 项 before/after、保留决定、release hash 和审计前 review DB。第一次 Windows 文件句柄失败已自动恢复到原 revision，显式关闭连接后重试成功，没有半完成 Gold。
- 已不可变发布 `data/releases/pilot-v0.1.1`：10 篇、14 项目、状态 valid=6/no_chinese=3/irrelevant=1；rich Gold SHA-256=`F748DC...ADDC5`。`pilot-v0.1.0` manifest SHA-256 仍为 `83A50B...AF664`，未覆盖旧版。
- 相同冻结候选重跑到 `data/experiments/pilot-v0.1.1/frozen-terra-sol-v1`：Provider/Reviewer 门控和项目 F1 均为 `1.000`；字段 micro 从旧 Gold 下的 `0.917/0.903` 变为 `0.874/0.937`，Reviewer delta 从 `-0.015` 变为 `+0.063`。Reviewer 修复/新增/共享 mismatch 为 `14/1/12`，唯一新增项是其拒绝“参建→在建”。
- 这仍是候选可见的 10 篇 development set，只能证明口径修正改变了错误归因，不能作为最终泛化排名；在新增 30–50 篇前不据此修改 Reviewer Prompt。
- 当前 Studio 定向测试为 `21 passed`，未运行全量 pytest；候选、Prompt provenance、模型、taxonomy 和三份上游数据库均未修改，也未创建提交。

### 阶段 4E/4F：40 篇分层扩展与 Terra→Sol 候选链

- Master Plan 将当前最终规划点固定为 200 篇：development 90、challenge 50、blind 60；Pilot 十篇是 development 起点且永久不得进入 blind。分层 Gold 规划覆盖 S01B、跨文章 S02A、S02B、S03 和至少 80 篇端到端样本，并定义人工复核、不可变版本发布和分层回归门槛。
- Stage 4E 采用定向分层扩展而非最终总体随机样本：`core_balanced=10`、`s01b_complex=8`、`s02a_family=8`、`s02b_s03_normalization=8`、`boundary_gate=6`；四个 S02A family 各含两篇且 family 不跨 split。
- 新增 `stage4e_manifest.py`：固定 40 个互异且非 Pilot 的 article ID，从只读源库冻结正文 hash 与历史选择信号，严格校验 24/16 和各 stratum 配额，先写临时目录、重读 hash 后原子发布，同版本拒绝覆盖。
- 已生成 `data/cohorts/stage-4e-40-v1`：`articles.jsonl` SHA-256=`78E72D...FB649`；33 篇有历史 valid 信号、7 篇保持 unreviewed，历史项目复杂度为单项目 18、2–3 项目 4、4+ 项目 11、无历史项目 7。这些历史信号明确不是 Gold。
- cohort manifest 固定 raw/extraction/main SHA-256 为 `3CFE53B...B60DD`、`B7D364C...8FC6B`、`6AB48986...B87F`，并记录实现与 artifact hash；没有调用模型、修改 Prompt/taxonomy/registry/location rules 或写入上游数据库。
- Stage 4F 使用 cohort manifest 而非执行器硬编码，`gpt-5.6-terra` 对 40 篇逐一执行 production S01B-S1→S2→逐项目 S34→S5；每篇保存固定正文、rendered prompts、builder hash、stage outputs、候选和 prompt manifest，并由 fail-closed `candidate_contract.py` 校验 model/role/article hash/schema/evidence。
- `gpt-5.6-sol` 已对全部 40 篇读取相同固定文章与完整 Terra artifact 做独立逐字段 Reviewer 审校；40/40 Provider、40/40 Reviewer 均通过合同。初始 `stage-4f-terra-sol-40-manifest.json` 因 23627 S1/最终状态错位被标记为已取代；修复后的权威清单为 `data/runs/stage-4f-terra-sol-40-manifest-r1.json`，SHA-256=`BDA704E...2DA395`。
- `prompt_pack.py` 已从 Pilot-only 泛化到完整 review cohort；网页只导入通过 Stage 4E 严格合同的候选。新候选只升级完全未触碰的初始草稿，人工编辑、确认记录与既有 Pilot release 保持不变。
- Pilot 冻结实验加载器只读取 release manifest 指定的文章 ID，Stage 4E 新候选与 Pilot 使用同一 candidate 目录时不再被误判为 Pilot hash 冲突；Studio 全量测试为 `32 passed`（仅 1 个既有 TestClient/httpx deprecation warning）。
- 23627 审计发现旧 Terra `stage_outputs.s1.status=valid_project`，但最终 artifact 被错误写成 `other_non_project` 并跳过 S2/S34/S5；合同现强制 S1 与最终状态一致，并强制 valid 分支的 S2 in-scope、S34、S5、final project 数量一致。重跑后均为 1，Sol parent 已更新并通过合同。
- 修复前 revision 23 草稿、旧 Provider/Reviewer 和旧 run 已完整备份到 `data/audits/stage4f-23627-rerun-20260813`；仅在确认草稿字节未变化且仍未确认后更新到 revision 25。实体适配兼容 `entity/name/entity_name` envelope，当前 CHINT 名称与原文 evidence 已保留；没有替用户确认文章。
- 标题与正文现为并列的固定原文证据来源：候选合同、网页导入、人工最终确认和 Pilot release 预检都会在两者中逐字校验；多段 evidence 的每一段可分别来自标题或正文。复核页可直接高亮标题证据，提示文案也明确 `present` 可引用标题或正文。
- Studio 全部定向测试现为 `25 passed`；唯一 warning 是当前 TestClient/httpx 兼容层的既有 deprecation 提示，不影响功能。本轮未运行全仓库 pytest。

### 2026-08-13：Golden Standard 暂停封存

- 本地 8765 服务已停止；Pilot release、Stage 4E 40 篇候选、人工草稿及 23627 revision 25 均按旧 guideline 保持原样，不参与能源分类 v2 迁移。
- 暂停快照位于 `data/archives/golden-standard-paused-20260813/`，包含 WAL-safe `review.db`、规则副本、737 个文件 SHA-256 和恢复 README；manifest SHA-256=`7628077D...073967`。
- 快照状态：19 article snapshots、102 candidates、19 drafts、18 confirmed；23627 revision 25 仍为 unconfirmed。恢复时必须另开 guideline/release 版本，不能覆盖既有 Pilot。
- Studio 全量测试 `33 passed`，仅保留一个既有 Starlette/httpx deprecation warning。

### 2026-08-15：暂停快照的只读 Development 评测复用

- 暂停快照没有解封或迁移；`eval/archived_gold.py` 以 SQLite query-only 模式读取 archived `review.db`，只消费 18 篇 confirmed 草稿，23627 revision 25 保持 unconfirmed 并排除。
- 评测输入标题/正文来自 archived `article_snapshots`，生成 run-local `raw_news.db`；封存源库 SHA-256 仍为 `095E7F4A...A9E8`，旧 guideline 与草稿没有被当前 taxonomy v2.1 改写。
- 该 18 篇集合只标记为 development/non-blind，不发布为新 Pilot，也不替代未来当前 guideline 的正式 Gold release。
