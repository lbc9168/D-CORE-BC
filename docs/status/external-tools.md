# external-tools 状态

> 最后更新：2026-08-06（新增 Golden Standard Studio 的富金标准数据契约）

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
| 只读数据源、抽样、复核存储、发布器与 Web UI | ⬜ 待实现 |

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
