# BRI_RE_Database_v2 项目状态文档

> 最后更新：2026-03-13

---

## 项目概述

**"一带一路"可再生能源项目数据库** — 追踪中国企业参与的海外可再生能源项目。

**流水线架构：**
```
数据收集 → 去重合并 ──→ 数据库整理 → 网页可视化 → 数据分析 → 报告撰写
   ✅       ✅    ↓      ✅         🚧         ⬜        ⬜
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
  - **原始新闻备案**：所有抓取新闻同时写入 `data/raw_news.db`（URL 唯一去重，跨批次持久化）
- **AI 提取筛选**：`filter.py`（独立脚本）+ `tools/project_extractor_tool.py`（crewai 工具）
  - 模型：Gemini gemini-2.5-flash-lite（google.genai SDK）
  - 包含完整的 9 条筛选规则（剔除非电站、制造业、招标阶段等）
  - 中文项目名强制约束（防止 LLM 输出英文）
  - **双语支持**：LLM 同时输出 `Project Name (EN)`、`Implementing Chinese Entity (EN)` 等英文字段
  - **输出改为 SQLite**（原 Excel 三文件已废弃）：
    - `data/projects.db` — 有效项目（`extracted_projects` 表，含 `imported_to_s02a` 增量标志）
    - `data/review.db` — 无中方参与（`no_chinese_records`）+ 被过滤文章（`discarded_records`）
    - `data/All_AI_Results_*.json` — AI 原始结果（存放在 projects.db 同级目录）
  - **重试机制**：AI 调用失败时最多重试 3 次（指数退避 2s/4s），全部失败才记入 `discarded_records`

### 输出字段（projects.db → extracted_projects 表）
| 字段 | 说明 |
|------|------|
| project_name / project_name_en | 中英文项目名 |
| name_en_quality | ai_translated（当前默认） |
| country / country_iso / bri_country / ldc | 国家信息 |
| category | 光伏/风电/水电/地热及余热/生物质/储能/综合能源 |
| capacity_mw / capacity_details | 装机容量 |
| project_stage | 收购/中标/签约/开工/在建/竣工/投入运营 |
| implementing_entity / _en | 中方实体（中英文） |
| subsidiary_name / _en | 子公司（中英文） |
| involvement_type | 参与方式 |
| 日期、投融资、信源等 | 其他结构化字段 |
| batch_id | 来源批次（Excel 文件名） |
| imported_to_s02a | 0=未导入，1=已导入（s02a 处理后标记） |

### crewai 配置
- **两个 Agent**：`news_crawler`、`project_extractor`（均指定 `llm: gemini/gemini-2.5-flash-lite`）
- **顺序执行**：crawl → extract（通过 context 传递文件路径）

---

## 阶段二A：数据去重合并（duplicate_merger）✅ 已完成

**子项目路径：** `s02a_duplicate_merger/`

### 已实现功能

#### DuplicateMergerTool（去重合并与入库）
五阶段流程：
1. **数据加载**（Python）：从 `s01/data/projects.db` 读取 `imported_to_s02a=0` 的未处理记录
2. **候选筛查**（Python）：多信号去重检测（四路信号 OR 逻辑）：
   - Signal 1：CN 名称 `token_sort_ratio ≥ 85`
   - Signal 2：EN 名称 `token_sort_ratio ≥ 80`
   - Signal 3：CN 名称 `partial_ratio ≥ 90`（子串包含）
   - Signal 4：容量精确匹配（误差 ≤ 1%，≥ 0.5MW）— 专用于跨语言历史数据安全网
   - 实体辅助通道：共有中方实体 + 名称中度相似（≥ 60）
3. **LLM 确认**（Gemini）：确认候选对是否为同一物理项目
4. **字段仲裁**（Python + LLM）：合并实体列表、LLM 解决冲突字段
5. **SQLite 写入 + 标记已导入**：规范化三表结构写入；处理完成后将 `projects.db` 中对应记录标记为 `imported_to_s02a=1`

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

### 数据接口
- **输入**：`s01_data_collector_filter/data/projects.db`（读取 `imported_to_s02a=0` 的记录）
- **输出**：`data/db/bri_re_database.db`；处理完成后回写 `projects.db` 标记已导入记录

### crewai 配置
- **一个 Agent**：`data_verifier`（指定 `llm: gemini/gemini-2.5-flash-lite`）
- **运行方式**：`crewai run` / `uv run s02a_duplicate_merger`

---

## 阶段二B：外部交叉验证（cross_verification）⚠️ 未完成（待修改，可选步骤）

**子项目路径：** `s02b_cross_verification/`

> 此步骤可选：去重合并后可直接进入「数据库整理」，也可先做交叉验证再整理。

### 已实现功能

#### CrossVerificationTool（外部信源交叉验证）
四阶段流程：
1. **GDELT 批量查询**：按国家×能源类型从 GDELT 国际新闻数据库拉取标题（本地缓存，零 token 消耗）
2. **多语言向量匹配**：Gemini text-embedding-004 嵌入，余弦相似度筛候选对（阈值可配置，默认 0.65）
3. **LLM 确认**：逐对判断文章是否报道该项目（high/medium/low 置信度）
4. **写回数据库 + 导出清单**：低置信度记录输出 Excel 人工核查清单

### 问题
- **GDELT 批量查询问题**：GDELT数据库中找不到与BRI数据库中匹配的数据（至少近期的数据找不到），需要以其他方式寻找其他信源

### 数据接口
- **输入**：读取 `../s02a_duplicate_merger/data/db/bri_re_database.db`（`verification_status = 'unverified'`）
- **输出**：更新同一 SQLite 中的验证字段；低置信度记录输出 Excel 至 `data/manual_review/`

### crewai 配置
- **一个 Agent**：`cross_verifier`（指定 `llm: gemini/gemini-2.5-flash-lite`）
- **运行方式**：`crewai run` / `uv run s02b_cross_verification`

---

## 阶段三：数据库整理 ✅ 已完成 （基本功能已完成，细节待调整）

**子项目路径：** `s03_data_exporter/`

### 已实现功能

#### 五步导出流程
1. **省份提取**（`province_extractor.py`）：Gemini 从 `location` 字段提取一级行政区英文名，结果缓存至 `data/province_cache.json`，空值直接跳过
   - **Natural Earth 约束**（`admin1_lookup.py`）：从 `s04/scripts/_ne_admin1_cache.zip` 加载各国已知省份列表（`{ISO3: [name_en, ...]}`），随 Prompt 传入 Gemini，输出必须来自列表或 null，防止幻觉；zip 不存在时退化为无约束行为
2. **扁平表构建**（`flat_builder.py`）：将 s02a 三表结构（canonical_projects / project_events / chinese_entities）合并为每项目一行的 `projects_flat` 表
3. **聚合统计**（`stats_builder.py`）：构建 3 张预聚合表，供可视化前端直接查询
4. **导出**：输出 `projects_flat.xlsx` 和 `projects_flat.csv` 供离线分析

#### projects_flat 核心字段
| 字段 | 说明 |
|------|------|
| `project_id / project_name / _en` | 项目标识与中英文名 |
| `country_iso / bri_country / ldc` | 国家信息 |
| `province` | AI提取的省级行政区英文名（可为null） |
| `category` | 能源类型 |
| `capacity_mw` | 最终状态事件的装机容量 |
| `latest_stage / latest_stage_date / event_year` | 最终里程碑信息 |
| `timeline_notes` | 多事件时的阶段时间线文字摘要 |
| `primary_entity / _en / all_entities` | 中方实体（主体 + 全列表JSON） |
| `verification_status` | 来自 s02b 的交叉验证状态 |

#### 聚合统计表（3张）
| 表名 | 维度 | 用途 |
|------|------|------|
| `stats_country` | country_iso × category × year | 地图热力图 |
| `stats_entity` | primary_entity × category × year | 企业排名图表 |
| `stats_timeline` | year × category × latest_stage | 时间趋势图 |

#### 阶段优先级（"最终状态"判定）
`投入运营 > 竣工 > 在建 > 开工 > 签约 > 中标 > 收购`

### 数据接口
- **输入**：`../s02a_duplicate_merger/data/db/bri_re_database.db`
- **输出**：`data/output/bri_re_export.db`（主）/ `projects_flat.xlsx` / `projects_flat.csv`

### 运行方式
```bash
cd s03_data_exporter
uv sync       # 首次安装
uv run run_export
```

---

## 阶段四：网页可视化 🚧 调试中（基本功能可用，部分问题已修复）

**子项目路径：** `s04_web_visualization/`

### 技术栈
- React 18 + TypeScript + Vite
- Tailwind CSS（自定义品牌色/类别色）
- react-simple-maps（地图）+ recharts（图表）
- 无重型 UI 库，纯 Tailwind 实现组件
- 双语 i18n（中/英，自制轻量实现，无 react-i18next）

### 数据流
```
s03_data_exporter（uv run run_export）
  → data/output/projects_flat.json
  → data/output/stats_country.json
  → data/output/stats_entity.json
  → data/output/stats_timeline.json
        ↓ 手动复制到 s04_web_visualization/public/data/
s04 前端直接 fetch 静态 JSON（纯前端，零后端）
```

### 已实现功能
1. **世界热力图**：country_iso 匹配，支持按项目数/装机容量着色，对数色阶
2. **省级热力图**：按需加载 `/geo/provinces/{ISO3}.geojson`，点击省份过滤项目
3. **无省级信息按钮**：单独列出省份字段为空的项目
4. **项目列表**：国家/省份级别项目列表，含能源类型标签/阶段标签/容量
5. **项目详情**：中英文切换、实体列表、时间线、来源链接
6. **筛选栏**：年份滑块、多选（类型/阶段/企业）、BRI 筛选、着色方式切换
7. **图表面板**（世界视图）：堆叠柱状图（年份趋势）+ 水平柱状图（企业排名）
8. **数据表格**：全量数据，支持搜索、多列排序、分页（50条/页）、CSV 导出
9. **中英文切换**：整个界面（菜单、标签、字段名）全部双语

### 省级地图数据准备
```bash
cd s04_web_visualization
pip install pyshp            # 首次：安装 shapefile 解析依赖
python scripts/download_geo.py    # 下载 Natural Earth 10m admin1，按国家拆分
```
- 数据源：Natural Earth 10m admin1（NACI CDN / Amazon S3，zip 约 10-15 MB）
- 注意：50m 版本仅含 9 个大国，必须用 10m 版才有全球完整覆盖（~4500+ feature）
- 输出到 `public/geo/provinces/{ISO3}.geojson`（~242 个国家）

### 启动方式
```bash
# 1. 生成 JSON 数据（会自动同步到 s04/public/data/）
cd s03_data_exporter && uv run run_export

# 2. 下载省级地图（首次，需 pip install pyshp）
cd s04_web_visualization && python scripts/download_geo.py

# 3. 启动开发服务器
npm install && npm run dev
```

### 构建发布（本地离线运行）
```bash
cd s04_web_visualization
npm run build    # 输出到 dist/（已配置 base="./" 支持相对路径）

# 启动本地服务（无需 Node.js，Python 即可）
cd dist && python -m http.server 8080
# 访问 http://localhost:8080
```
> 注意：dist/ 必须通过 HTTP 服务器访问，不可直接双击 index.html（fetch 请求受浏览器 CORS 限制）

### 待优化（后续讨论）
- 地图 hover tooltip 样式和数据展示
- 省级匹配精度（AI 提取省名与 GeoJSON feature 名的模糊匹配）
- 移动端响应式布局
- 省级地图 projection 自动居中
- 省级地图数据：需 VPN 或国内镜像源下载 10m zip（两个官方源在国内均有访问限制）

---

## 阶段五：数据分析 ⬜ 未开始

---

## 阶段六：报告撰写 ⬜ 未开始

---

## 流水线编排（crewAI Flows）

**子项目路径：** `pipeline/`

一次命令运行完整四阶段流水线，支持条件跳转：

```
BRIPipelineFlow
  stage_01_collect  (@start)          ← 可通过 skip_collection=True 跳过
       ↓
  stage_02a_merge   (@listen)
       ↓
  route_cross_verification (@router)
    ├── "run_verify" → stage_02b_verify ← run_cross_verification=True（默认）
    └── "done"       → finish_no_verify ← run_cross_verification=False
              ↓ (两路均汇入)
         _run_stage_03()               ← 可通过 run_export=False 跳过
              ↓
         s03_data_exporter.run()       ← 省份提取 + 扁平表 + JSON 同步到 s04
```

**运行方式：**
```bash
cd pipeline
uv sync                      # 首次安装（会自动安装四个本地子项目）
uv run run_pipeline          # 完整流水线（含交叉验证 + 导出）
uv run run_merge_only        # 采集 + 去重 + 导出（跳过交叉验证）
uv run run_collect_and_merge # 仅采集 + 去重合并（阶段一 → 阶段二A，不导出）
uv run run_export_only       # 仅重新导出（数据库已有时）
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
| s04 省级地图数据（10m zip）在国内网络下载受限，需 VPN | 中 | 已知，待解决 |
