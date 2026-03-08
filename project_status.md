# BRI_RE_Database_v2 项目状态文档

> 最后更新：2026-03-08

---

## 项目概述

**"一带一路"可再生能源项目数据库** — 追踪中国企业参与的海外可再生能源项目。

**流水线架构：**
```
数据收集 → 去重合并 ──→ 数据库整理 → 网页可视化 → 数据分析 → 报告撰写
   ✅       ✅    ↓      ⬜         ⬜         ⬜        ⬜
            交叉验证（可选）✅
```
> 阶段二已拆分为两个独立子项目：`s02a_duplicate_merger`（必选）+ `s02b_cross_verification`（可选）。
> 去重合并后可直接进入数据库整理，也可先做交叉验证再整理。

---

## 阶段一：数据收集（data_collector_filter）✅ 已完成

**子项目路径：** `s01_data_collector_filter/`

### 已实现功能
- **新闻爬虫**：`Crawler.py`（独立脚本）+ `tools/news_crawler_tool.py`（crewai 工具）
  - 数据源：goalfore.cn 项目新闻页
  - 支持自定义日期范围、关键词过滤
  - 输出：`data/raw/Energy_News_Raw_*.xlsx`
- **AI 提取筛选**：`filter.py`（独立脚本）+ `tools/project_extractor_tool.py`（crewai 工具）
  - 模型：Gemini gemini-2.5-flash-lite（google.genai SDK）
  - 包含完整的 9 条筛选规则（剔除非电站、制造业、招标阶段等）
  - 中文项目名强制约束（防止 LLM 输出英文）
  - **双语支持**：LLM 同时输出 `Project Name (EN)`、`Implementing Chinese Entity (EN)` 等英文字段
  - 输出三类文件：有效项目库、无中方参与项目、被剔除新闻，以及 AI 原始 JSON

### 输出字段（BRI_RE_Final_Database_*.xlsx）
| 字段 | 说明 |
|------|------|
| Project Name / Project Name (EN) | 中英文项目名 |
| Name EN Quality | ai_translated（当前默认） |
| Country / Country ISO / BRI country / LDCs | 国家信息 |
| Category | 光伏/风电/水电/地热及余热/生物质/储能/综合能源 |
| Capacity (MW) / Capacity Details | 装机容量 |
| Project Stage | 收购/中标/签约/开工/在建/竣工/投入运营 |
| Implementing Chinese Entity / (EN) | 中方实体（中英文） |
| Subsidiary Name / (EN) | 子公司（中英文） |
| Type of involvement | 参与方式 |
| 日期、投融资、信源等 | 其他结构化字段 |

### crewai 配置
- **两个 Agent**：`news_crawler`、`project_extractor`（均指定 `llm: gemini/gemini-2.5-flash-lite`）
- **顺序执行**：crawl → extract（通过 context 传递文件路径）

---

## 阶段二A：数据去重合并（duplicate_merger）✅ 已完成

**子项目路径：** `s02a_duplicate_merger/`

### 已实现功能

#### DuplicateMergerTool（去重合并与入库）
四阶段流程：
1. **候选筛查**（Python）：按国家+类别+容量+阶段分组，rapidfuzz 模糊匹配（阈值 85%）
2. **LLM 确认**（Gemini）：确认候选对是否为同一物理项目
3. **字段仲裁**（Python + LLM）：合并实体列表、LLM 解决冲突字段
4. **SQLite 写入**：规范化三表结构，支持幂等运行

### SQLite 数据模型（s02a_duplicate_merger/data/db/bri_re_database.db）
三张关系表：

**canonical_projects**（物理工程，唯一）
```
PK: {ISO3}-{CAT}-{NNNN}  e.g. IND-PV-0001
字段: project_name, project_name_en, name_en_quality, country, country_iso, bri_country, ldc, location, category
```

**project_events**（里程碑事件）
```
PK: {canonical_id}-{NN}  e.g. IND-PV-0001-01
字段: 容量/日期/投融资/信源 + 验证字段(verification_status/confidence/sources/needs_manual_review)
```

**chinese_entities**（中方主体）
```
PK: 自增整数，FK → project_events.id
字段: implementing_entity, implementing_entity_en, subsidiary_name, subsidiary_name_en, entity_en_quality, involvement_type, subcontract_amount
```

### crewai 配置
- **一个 Agent**：`data_verifier`（指定 `llm: gemini/gemini-2.5-flash-lite`）
- **运行方式**：`crewai run` / `uv run s02a_duplicate_merger`

---

## 阶段二B：外部交叉验证（cross_verification）✅ 已完成（可选步骤）

**子项目路径：** `s02b_cross_verification/`

> 此步骤可选：去重合并后可直接进入「数据库整理」，也可先做交叉验证再整理。

### 已实现功能

#### CrossVerificationTool（外部信源交叉验证）
四阶段流程：
1. **GDELT 批量查询**：按国家×能源类型从 GDELT 国际新闻数据库拉取标题（本地缓存，零 token 消耗）
2. **多语言向量匹配**：Gemini text-embedding-004 嵌入，余弦相似度筛候选对（阈值可配置，默认 0.65）
3. **LLM 确认**：逐对判断文章是否报道该项目（high/medium/low 置信度）
4. **写回数据库 + 导出清单**：低置信度记录输出 Excel 人工核查清单

### 数据接口
- **输入**：读取 `../s02a_duplicate_merger/data/db/bri_re_database.db`（`verification_status = 'unverified'`）
- **输出**：更新同一 SQLite 中的验证字段；低置信度记录输出 Excel 至 `data/manual_review/`

### crewai 配置
- **一个 Agent**：`cross_verifier`（指定 `llm: gemini/gemini-2.5-flash-lite`）
- **运行方式**：`crewai run` / `uv run s02b_cross_verification`

---

## 阶段三：数据库整理 ⬜ 未开始

计划：构建面向可视化和分析的数据导出/转换层。

---

## 阶段四：网页可视化 ⬜ 未开始

计划：前端可视化界面，支持地图展示、中英文切换、项目筛选。

---

## 阶段五：数据分析 ⬜ 未开始

---

## 阶段六：报告撰写 ⬜ 未开始

---

## 流水线编排（crewAI Flows）

**子项目路径：** `pipeline/`

一次命令运行完整三阶段流水线，支持条件跳转：

```
BRIPipelineFlow
  stage_01_collect  (@start)          ← 可通过 skip_collection=True 跳过
       ↓
  stage_02a_merge   (@listen)
       ↓
  route_cross_verification (@router)
    ├── "run_verify" → stage_02b_verify (@listen)   ← run_cross_verification=True（默认）
    └── "done"       → finish_no_verify (@listen)   ← run_cross_verification=False
```

**运行方式：**
```bash
cd pipeline
uv sync                      # 首次安装（会自动安装三个本地子项目）
uv run run_pipeline          # 完整流水线（含交叉验证）
uv run run_merge_only        # 仅采集 + 去重（跳过交叉验证）
```

---

## 批量并发采集（kickoff_for_each_async）

在 `s01_data_collector_filter` 中新增，用于历史数据补录：

```bash
cd s01_data_collector_filter
uv run run_parallel          # 使用默认参数（2023-01-01 → 2026-03-08，按月拆分）
```

或在代码中调用：
```python
from s01_data_collector_filter.main import run_parallel, split_date_range

run_parallel("2024-01-01", "2025-12-31", chunk_months=1)   # 24批并发
run_parallel("2023-01-01", "2026-03-08", chunk_months=3)   # ~13批（限速时推荐）
```

---

## 项目结构

```
BRI_RE_Database_v2/
├── pipeline/                           # 全流水线 crewai Flow 编排
│   ├── pyproject.toml
│   └── src/bri_pipeline/
│       └── flow.py                 # BRIPipelineFlow + PipelineState
│
├── s01_data_collector_filter/          # 阶段一 crewai 子项目
│   ├── pyproject.toml
│   ├── Crawler.py                  # 独立爬虫脚本
│   ├── filter.py                   # 独立 AI 筛选脚本
│   └── src/s01_data_collector_filter/
│       ├── main.py                 # run() + run_parallel() + split_date_range()
│       ├── crew.py
│       ├── config/{agents,tasks}.yaml
│       ├── tools/news_crawler_tool.py
│       ├── tools/project_extractor_tool.py
│       └── knowledge/user_preference.txt
│
├── s02a_duplicate_merger/               # 阶段二A crewai 子项目（去重合并与入库）
│   ├── pyproject.toml
│   ├── data/db/                    # SQLite 数据库（bri_re_database.db）
│   └── src/s02a_duplicate_merger/
│       ├── main.py
│       ├── crew.py
│       ├── config/{agents,tasks}.yaml
│       └── tools/duplicate_merger_tool.py
│
├── s02b_cross_verification/             # 阶段二B crewai 子项目（外部交叉验证，可选）
│   ├── pyproject.toml
│   ├── data/verification_cache/    # GDELT 文章 & 向量嵌入缓存
│   ├── data/manual_review/         # 低置信度人工核查 Excel
│   └── src/s02b_cross_verification/
│       ├── main.py
│       ├── crew.py
│       ├── config/{agents,tasks}.yaml
│       └── tools/cross_verification_tool.py
│
├── company_name_cache.json         # 中文公司名 → 英文缓存（41条）
├── enums_mapping.json              # 枚举字段映射表
├── WorldCountryList.csv            # 国家数据库（ISO/BRI/LDC 信息）
└── .env                            # API Key（不入 git）
```

---

## 技术栈

| 组件 | 技术 |
|------|------|
| AI 框架 | crewai 1.9.3 |
| LLM（主） | Gemini gemini-2.5-flash-lite |
| LLM（嵌入） | Gemini text-embedding-004 |
| AI SDK | google-genai >= 1.49.0 |
| 外部数据源 | GDELT 国际新闻数据库（免费，无 API Key） |
| 模糊匹配 | rapidfuzz >= 3.0.0 |
| 数据处理 | pandas >= 2.0.0 |
| 数据库 | SQLite（sqlite3，内置） |
| 网络请求 | requests >= 2.31.0 |

---

## 已知问题

| 问题 | 优先级 | 状态 |
|------|--------|------|
| 根目录 `pyproject.toml` 的 `requires-python = ">=3.14"` 错误 | 低 | 未修复 |
| `Crawler.py` 第178行重复的 `is_relevant` 赋值 | 低 | 未修复 |
| `s01_data_collector_filter/custom_tool.py` 仍为模板占位符 | 低 | 未修复 |
| Windows 终端 GBK 编码导致 emoji 乱码（crewai 事件总线） | 低 | 未修复（仅影响显示） |
| `pipeline/` 首次 `uv sync` 需要各子项目已安装依赖 | 低 | 已知，文档说明 |
