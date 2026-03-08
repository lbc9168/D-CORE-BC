# BRI_RE_Database_v2 改动记录

> 记录格式：`[日期 时间] commit 信息 — 改动内容`

---

## 2026-02-28

### 17:56 · `Initial commit`
- 创建项目仓库，建立基本目录结构

---

## 2026-03-01

### 20:49 · `Create project`
- 初始化 `data_collector_filter/` crewai 子项目骨架

### 23:28 · `keep building BRI database`
- 完善 `data_collector_filter/` 子项目：
  - 新建 `tools/news_crawler_tool.py`（NewsCrawlerTool，封装爬虫逻辑）
  - 新建 `tools/project_extractor_tool.py`（ProjectExtractorTool，封装 Gemini AI 提取逻辑）
  - 完整实现 `config/agents.yaml`、`config/tasks.yaml`、`crew.py`、`main.py`
  - 新建 `knowledge/user_preference.txt`（BRI 项目领域知识）
  - `filter.py`：API Key 迁移至 `.env`，从 `google.generativeai` 迁移至 `google.genai` SDK
  - `filter.py`：模型从 `gemini-2.0-flash-lite` 更新至 `gemini-2.5-flash-lite`（旧模型已下线）
  - `project_extractor_tool.py`：同步迁移至 `google.genai` SDK
  - 新建 `data_verification/` crewai 子项目完整结构：
    - `pyproject.toml`（依赖：crewai 1.9.3, pandas, rapidfuzz 等）
    - `tools/duplicate_merger_tool.py`（DuplicateMergerTool，四阶段去重入库）
    - SQLite 三表模型（canonical_projects / project_events / chinese_entities）
    - Project ID 命名规则：`{ISO3}-{CAT}-{NNNN}`（如 IND-PV-0001）
    - `config/agents.yaml`、`config/tasks.yaml`、`crew.py`、`main.py`

---

## 2026-03-02

### 18:23 · `data verification tool push` — 双语支持 + 外部交叉验证

#### data_collector_filter 改动
- **`tools/project_extractor_tool.py`**：
  - 输出 JSON schema 新增双语字段：`Project Name (EN)`、`Implementing Chinese Entity (EN)`、`Subsidiary Name (EN)`
  - 新增 `Name EN Quality` 字段（固定为 `ai_translated`，供后续验证阶段升级）
  - `col_order` 更新，Excel 输出包含所有英文字段
  - Prompt 新增 EN 字段填写规范（优先使用官方英文名，如 PowerChina、CTG 等）

#### data_verification 改动
- **`tools/duplicate_merger_tool.py`**：
  - `canonical_projects` 表新增：`project_name_en`、`name_en_quality`
  - `project_events` 表新增：`verification_status`（默认 unverified）、`verification_confidence`、`verification_sources`、`needs_manual_review`
  - `chinese_entities` 表新增：`implementing_entity_en`、`subsidiary_name_en`、`entity_en_quality`
  - 数据摄入逻辑更新，支持从 Excel 读取英文字段并写入 SQLite

- **`tools/cross_verification_tool.py`** ✨ 新文件（682行）：
  - GDELT 国际新闻数据库查询（按国家 × 能源类型，本地缓存）
  - Gemini text-embedding-004 多语言向量嵌入（中英文均支持）
  - 余弦相似度筛选候选对（可配置阈值，默认 0.65）
  - LLM 确认项目-文章匹配关系（high/medium/low 置信度）
  - 验证结果写回 `project_events.verification_status`
  - 低置信度记录导出 Excel 人工核查清单至 `data/manual_review/`

- **`config/agents.yaml`**：新增 `cross_verifier` agent 配置
- **`config/tasks.yaml`**：新增 `cross_verify_task` 任务配置（GDELT 相关参数均通过 inputs 配置）
- **`crew.py`**：新增 `cross_verifier` agent、`cross_verify_task` task；crew 顺序更新为 merge → verify
- **`main.py`**：
  - 新增 inputs：`cache_dir`、`output_dir`、`gdelt_months`（24）、`similarity_threshold`（0.65）、`use_news_api`（False）
  - 新增 `merge_only()` 函数（仅去重入库）
  - 新增 `verify_only()` 函数（仅交叉验证）

#### 项目根目录新增文件
- **`company_name_cache.json`**：中文公司名 → 英文名映射缓存（41条，供向量匹配时英文化项目描述）
- **`enums_mapping.json`**：枚举字段映射表（category/project_stage/financing_model/involvement_type/name_en_quality）

#### 其他
- **`.gitignore`**：新增规则——排除 `data_verification/data/`、`data_collector_filter/data/`、`verification_cache/`、`manual_review/`、`*.db`、`*.sqlite`（数据文件和缓存不入 git）
- `.claude/settings.local.json`：更新本地 Claude 配置

---

### 18:59 · `minor changes` — LLM 显式指定 + 小调整

- **`data_collector_filter/config/agents.yaml`**：两个 agent（news_crawler、project_extractor）均新增 `llm: gemini/gemini-2.5-flash-lite`（显式指定，防止 crewai 使用默认 LLM）
- **`data_verification/config/agents.yaml`**：两个 agent（data_verifier、cross_verifier）同样新增 `llm: gemini/gemini-2.5-flash-lite`
- **`data_collector_filter/main.py`**：测试用 `start_date` 从 `2026-02-23` 调整为 `2026-02-20`（扩大测试数据范围）
- **`data_verification/pyproject.toml`**：小调整（脚本入口格式整理）
- **`data_verification/data/db/bri_re_database.db`**：数据库文件更新（测试运行后重置）

---

## 2026-03-08

### · `refactor: split data_verification into duplicate_merger + cross_verification`

#### 架构调整
- **`data_verification/`** 保留不删，标注为已归档备份
- **新建 `duplicate_merger/`** crewai 子项目（阶段二A）：
  - `pyproject.toml`（含 rapidfuzz，不含 cross_verification 不需要的依赖）
  - `src/duplicate_merger/main.py`：仅 `run()` 函数（原 `merge_only` 逻辑）
  - `src/duplicate_merger/crew.py`：单 agent（`data_verifier`）+ 单 task
  - `config/{agents,tasks}.yaml`：仅保留 `data_verifier` / `merge_and_store_task`
  - `tools/duplicate_merger_tool.py`：从 `data_verification` 原样复制
  - `data/db/`：SQLite 数据库存放于此（`bri_re_database.db`）
- **新建 `cross_verification/`** crewai 子项目（阶段二B，可选）：
  - `pyproject.toml`（不含 rapidfuzz）
  - `src/cross_verification/main.py`：仅 `run()` 函数（原 `verify_only` 逻辑）
    - 默认 `db_path = "../duplicate_merger/data/db/bri_re_database.db"`
  - `src/cross_verification/crew.py`：单 agent（`cross_verifier`）+ 单 task
  - `config/{agents,tasks}.yaml`：仅保留 `cross_verifier` / `cross_verify_task`
  - `tools/cross_verification_tool.py`：从 `data_verification` 原样复制
  - `data/verification_cache/`、`data/manual_review/`：缓存和核查清单目录

#### 数据流设计
```
data_collector_filter (Excel)
        ↓
  duplicate_merger  →  data/db/bri_re_database.db
        ├──→ 直接进入「数据库整理」阶段
        └──→ cross_verification（读取同一 DB，更新验证字段）
                        ↓
                  「数据库整理」阶段
```

#### 其他
- **`.gitignore`**：新增 `duplicate_merger/data/` 和 `cross_verification/data/` 排除规则
- **`project_status.md`**：阶段二拆分为二A/二B，更新项目结构树

---

### · `refactor: rename subprojects with numeric stage prefix`

#### 命名规范
所有子项目统一改为 `sNN_name` 格式（s = stage，NN = 两位数字），目录名、pyproject.toml 包名、src/ 模块名三者保持一致。

| 旧名 | 新名 |
|------|------|
| `data_collector_filter/` | `s01_data_collector_filter/` |
| `duplicate_merger/` | `s02a_duplicate_merger/` |
| `cross_verification/` | `s02b_cross_verification/` |
| `data_verification/` | 已删除 |

#### 改动内容（每个子项目）
- 根目录重命名
- `src/` 下模块目录重命名
- `pyproject.toml`：`name` 字段 + `[project.scripts]` 入口路径
- `main.py`：绝对 import 路径 + 跨项目相对路径（`input_dir` / `db_path`）
- `crew.py`：绝对 import 路径

#### 其他
- **`.gitignore`**：数据目录排除规则更新为新路径
- **`project_status.md` / `MEMORY.md`**：路径引用全部更新

---

### · `feat: add crewai Flows pipeline + kickoff_for_each parallel collection`

#### 新增 `pipeline/` 子项目（crewAI Flow 全流水线编排）
- **新建 `pipeline/pyproject.toml`**：
  - 类型 `[tool.crewai] type = "flow"`
  - 通过 `[tool.uv.sources]` 以 editable 方式依赖三个本地子项目
  - 入口：`run_pipeline`（完整流水线）/ `run_merge_only`（跳过交叉验证）
- **新建 `pipeline/src/bri_pipeline/flow.py`**：
  - `PipelineState`（Pydantic 模型）：统一管理全流水线参数，含阶段一/二A/二B 配置及流程控制开关（`skip_collection` / `run_cross_verification`）
  - `BRIPipelineFlow`（`Flow[PipelineState]`）：三阶段 Flow：
    - `@start() stage_01_collect` → 调用 `DataCollectorFilter().crew().kickoff()`
    - `@listen(stage_01_collect) stage_02a_merge` → 调用 `DuplicateMerger().crew().kickoff()`
    - `@router(stage_02a_merge) route_cross_verification` → 返回 `"run_verify"` 或 `"done"`
    - `@listen("run_verify") stage_02b_verify` → 调用 `CrossVerification().crew().kickoff()`
    - `@listen("done") finish_no_verify` → 打印完成摘要
  - 所有子项目路径均从 `__file__` 推算为绝对路径，确保跨 CWD 一致性
  - `run_full()` / `run_merge_only()` 入口函数供 pyproject.toml 调用

#### `s01_data_collector_filter` — 新增批量并发采集
- **`main.py`** 新增四个函数：
  - `_add_months(d, months)`：月份安全加法（自动处理月末边界）
  - `split_date_range(start, end, chunk_months, base_inputs)`：将大日期范围拆分为多组 kickoff inputs
  - `_kickoff_parallel_async(inputs_list)`：内部 async 包装器，调用 `kickoff_for_each_async`
  - `run_parallel(start_date, end_date, chunk_months)`：并发批量历史补录入口，通过 `asyncio.run()` 驱动
- **`pyproject.toml`**：新增 `run_parallel = "s01_data_collector_filter.main:run_parallel"` 脚本入口

---

## 说明

- 所有 crewai agent 均显式指定 `llm: gemini/gemini-2.5-flash-lite`，确保不使用默认 OpenAI 模型
- 子项目命名规范：`sNN_name`（目录、包名、模块名三者一致）
- 双语字段（EN）贯穿整个流水线：AI 提取阶段输出 → SQLite 存储 → 向量匹配使用
- `.db` 数据库文件已排除 git，需本地运行生成
