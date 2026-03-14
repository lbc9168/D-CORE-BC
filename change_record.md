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

### · `feat: replace WorldCountryList.csv with JSON + aliases`

#### 新增 `s01_data_collector_filter/WorldCountryList.json`
- 由 `WorldCountryList.csv` 转换生成（233 个国家）
- 每条记录新增 `aliases_cn` 字段，收录 30 个国家的 38 条常见中文别名，例如：
  - `印度尼西亚` → `["印尼"]`
  - `沙特阿拉伯` → `["沙特", "沙特阿拉伯王国"]`
  - `刚果（金）` → `["刚果民主共和国", "民主刚果", "刚果金"]`
  - `科特迪瓦` → `["象牙海岸"]`
  - `中国香港 / 澳门 / 台湾` → `["香港"] / ["澳门"] / ["台湾"]`
  - 中亚五国（哈萨克、乌兹别克、土库曼、吉尔吉斯、塔吉克）等
- 字段结构：`iso / name_cn / name_en / continent / subregion / bri / ldc / aliases_cn`

#### `s01_data_collector_filter/tools/project_extractor_tool.py`
- `_load_country_db(json_path)`：改为读取 JSON（不再依赖 pandas 的 `read_csv`），所有 `aliases_cn` 展开为独立键，精确匹配即可覆盖别名场景
- `ProjectExtractorInput.country_db_path` 默认值：`WorldCountryList.csv` → `WorldCountryList.json`
- `ProjectExtractorTool._run()` 同步更新默认值

---

### · `feat: improve duplicate candidate detection with multi-signal matching`

#### `s02a_duplicate_merger/tools/duplicate_merger_tool.py`

**新增常量**
| 常量 | 值 | 说明 |
|------|----|------|
| `FUZZY_THRESHOLD_EN` | 80 | EN 名称 token_sort_ratio 阈值（略低，因 AI 翻译质量参差） |
| `FUZZY_PARTIAL_THRESHOLD` | 90 | CN 名称 partial_ratio 阈值（捕获子串包含关系） |
| `FUZZY_ENTITY_ASSIST` | 60 | 实体重叠辅助：名称达此分 + 共有中方实体 → 送 LLM 确认 |
| `CAPACITY_BYPASS_SCORE` | 92 | 任一名称信号 ≥ 此分时豁免容量硬过滤 |

**新增辅助函数**
- `_name_similarity(a, b) → (bool, int)`：三信号 OR 逻辑，返回是否通过及最高分
  - 信号一：CN 名称 `token_sort_ratio ≥ 85`（原有逻辑）
  - 信号二：EN 名称 `token_sort_ratio ≥ 80`（双语互验，新增）
  - 信号三：CN 名称 `partial_ratio ≥ 90`（子串包含，新增）
- `_entity_overlap(a, b) → bool`：检查两条记录是否共有同名中方实体（集合交集）

**`_find_duplicate_candidates` 重写**
- 名称匹配从单一 `token_sort_ratio` 升级为调用 `_name_similarity`（三信号 OR 逻辑）
- 新增实体重叠辅助通道：`entity_boost = 实体重叠 AND 名称 ≥ 60`
- 容量豁免：`best_score ≥ 92` 时跳过容量差检查，放行至 LLM 确认
- 整体逻辑：`(name_ok OR entity_boost) AND 容量检查（或豁免）→ 进入候选`

**`_llm_confirm` 补充上下文**
- Prompt 新增两行：`项目名称（英）` 和 `中方实体` → LLM 拥有更多证据，减少误判

---

### · `feat: add archive_old_files to s01 run()`

#### `s01_data_collector_filter/src/s01_data_collector_filter/main.py`
- 新增 `archive_old_files()` 函数：每次 `run()` / `run_parallel()` 前，将 `data/raw/` 和 `data/processed/` 中的旧文件移入各自的 `Archived/` 子目录（跳过 `.gitkeep`）
- 新增模块级常量 `_S01_ROOT`（避免重复调用 `Path(__file__).resolve().parents[2]`）

---

### · `feat: add s03_data_exporter subproject`

#### 新增 `s03_data_exporter/` 子项目（阶段三：数据库整理）

**子项目结构：**
```
s03_data_exporter/
├── pyproject.toml               # 依赖：pandas, openpyxl, google-genai, python-dotenv
└── src/s03_data_exporter/
    ├── main.py                  # 入口，4步流程编排
    ├── province_extractor.py    # Gemini 批量省份提取（带本地缓存 + 3次重试）
    ├── flat_builder.py          # 构建 projects_flat 扁平表
    └── stats_builder.py         # 构建 3 张聚合统计表
```

**`province_extractor.py`**：
- 输入：`canonical_projects.location` + `country`
- 逻辑：location 为空 → null；已缓存 → 读取；其余 → 批量送 Gemini（每批 30 条）
- 缓存：结果写入 `data/province_cache.json`；失败不写缓存（下次可重试）
- 重试：每批最多 3 次，指数退避（2s, 4s）

**`flat_builder.py`**：
- 每个 `canonical_project` 对应一行
- 阶段优先级：投入运营(7) > 竣工(6) > 在建(5) > 开工(4) > 签约(3) > 中标(2) > 收购(1)
- `capacity_mw`：取最终状态事件的容量（若为空则向下顺延）
- `timeline_notes`：多事件时生成"签约: 2022-03 → 开工: 2023-06"格式文字备注
- `all_entities`：所有事件的中方实体合并去重（JSON 字符串）
- `primary_entity`：最终状态事件的第一个实体

**`stats_builder.py`**：构建 3 张聚合表（幂等，每次 DROP + 重建）
| 表名 | 维度 | 指标 |
|------|------|------|
| `stats_country` | country_iso × category × year | project_count, total_capacity_mw |
| `stats_entity` | primary_entity × category × year | project_count, total_capacity_mw |
| `stats_timeline` | year × category × latest_stage | count, capacity_mw |

**输出文件（均在 `data/output/`）：**
- `bri_re_export.db`：主导出数据库（4张表，Streamlit 直接读）
- `projects_flat.xlsx`：供 Excel / R 分析
- `projects_flat.csv`：供 Python pandas 分析

**运行方式：**
```bash
cd s03_data_exporter
uv sync
uv run run_export
```

#### `.gitignore`
- 新增 `s03_data_exporter/data/` 排除规则

#### Bug 修复（发现于测试运行）
- **`stats_builder.py`**：`stats_entity` PRIMARY KEY 为 `(primary_entity, category, year)`，但原 GROUP BY 含 `primary_entity_en`，导致同一实体有不同英文名时主键冲突 → 改为 `GROUP BY primary_entity, category, year`，英文名用 `MAX()` 聚合

---

## 2026-03-08（续）

### · `feat: add s04_web_visualization subproject`

#### 新增 `s04_web_visualization/` 子项目（阶段四：网页可视化）

**技术栈：** React 18 + TypeScript + Vite + Tailwind CSS + react-simple-maps + recharts

**子项目结构：**
```
s04_web_visualization/
├── package.json / tsconfig*.json / vite.config.ts
├── tailwind.config.js / postcss.config.js
├── index.html
├── .gitignore                    # 排除 node_modules/, dist/, public/data/, public/geo/provinces/
├── scripts/
│   └── download_geo.py           # 下载 Natural Earth admin1，按国家拆分为 {ISO3}.geojson
├── public/
│   ├── data/                     # 运行 s03 后自动同步的 JSON（不入 git）
│   └── geo/provinces/            # 省级 GeoJSON（由 download_geo.py 生成，不入 git）
└── src/
    ├── types/index.ts             # 数据类型 + 枚举常量（CATEGORIES, STAGES, 颜色）
    ├── i18n/{zh,en,index}.ts      # 中英文翻译（自制轻量 i18n，无第三方库）
    ├── contexts/
    │   ├── LanguageContext.tsx    # 语言上下文 + t() 翻译函数
    │   └── DataContext.tsx        # 数据加载、过滤逻辑、useCountryStats
    ├── hooks/useProvinceGeo.ts    # 省级 GeoJSON 按需加载（带缓存）
    ├── utils/
    │   ├── colorScale.ts          # 对数色阶计算（浅蓝→深蓝）
    │   └── isoMapping.ts          # ISO 数字代码 → alpha-3 映射（供 world-atlas 使用）
    ├── components/
    │   ├── layout/Header.tsx      # 顶部导航 + 语言切换按钮
    │   ├── map/WorldMap.tsx       # 世界热力图（react-simple-maps + world-atlas TopoJSON）
    │   ├── map/ProvinceMap.tsx    # 省级热力图（按需加载 GeoJSON）
    │   ├── filters/FilterBar.tsx  # 筛选栏（年份滑块/类型/阶段/企业多选/BRI开关/着色方式）
    │   ├── projects/ProjectList.tsx    # 项目列表面板
    │   ├── projects/ProjectDetail.tsx  # 项目详情（含实体列表、时间线、来源链接）
    │   ├── charts/TimelineChart.tsx    # 年份趋势堆叠柱状图
    │   └── charts/EntityChart.tsx      # 企业排名水平柱状图
    ├── pages/MapPage.tsx          # 地图页（世界→省级→项目列表→详情 层级导航）
    ├── pages/TablePage.tsx        # 表格页（全量数据 + 搜索/排序/分页/CSV导出）
    ├── App.tsx / main.tsx / index.css
```

**已实现功能：**
1. 世界热力图：country_iso 匹配，按项目数/装机容量着色（对数色阶），hover 显示数值
2. 省级热力图：点击国家进入，按需加载该国 GeoJSON，点击省份过滤项目列表
3. "无省级信息"按钮：单独显示 province=null 的项目
4. 项目列表 → 项目详情（含中英文双语显示）
5. 筛选栏：年份范围、能源类型、项目阶段、中方企业多选、BRI 国家筛选
6. 图表面板（世界视图右侧）：年份趋势图 + 企业排名图
7. 数据表格：全量项目、搜索、多列排序、分页（50条/页）、CSV 导出
8. 中英文切换：整个界面（菜单、标签、字段名、数据内容）全部双语

**省级地图数据准备（首次）：**
```bash
cd s04_web_visualization
python scripts/download_geo.py    # 下载 Natural Earth admin1（约 3MB），按国家拆分
```

**启动方式：**
```bash
cd s04_web_visualization
npm install && npm run dev
```

---

### · `feat: s03 json_exporter + auto sync to s04`

#### `s03_data_exporter/src/s03_data_exporter/json_exporter.py` ✨ 新文件
- 从 `bri_re_export.db` 导出四个静态 JSON 文件供前端使用：
  - `projects_flat.json`：所有项目完整字段（`all_entities` 反序列化为数组）
  - `stats_country.json`：国家维度聚合（地图热力图）
  - `stats_entity.json`：企业维度聚合（排名图表）
  - `stats_timeline.json`：时间维度聚合（趋势图）

#### `s03_data_exporter/src/s03_data_exporter/main.py`
- 新增 Step 5：调用 `export_json()` 导出静态 JSON
- 同时自动同步到 `s04_web_visualization/public/data/`（若目录存在）
- Excel 导出加 `PermissionError` 捕获：文件被 Excel 打开时跳过而不崩溃
- 步骤编号更新为 1/5 … 5/5

---

### · `feat: add s03 to pipeline`

#### `pipeline/pyproject.toml`
- `dependencies` 新增 `s03_data_exporter`
- `[tool.uv.sources]` 新增 `s03_data_exporter = { path = "../s03_data_exporter", editable = true }`
- `[project.scripts]` 新增 `run_export_only = "bri_pipeline.flow:run_export_only"`
- `[tool.pyright].extraPaths` 新增 `"../s03_data_exporter/src"`

#### `pipeline/src/bri_pipeline/flow.py`
- 导入 `s03_data_exporter.main.run` 为 `_run_s03`
- `PipelineState` 新增 `run_export: bool = True` 流程控制开关
- `stage_02b_verify`：执行完交叉验证后调用 `self._run_stage_03()`
- `finish_no_verify`：跳过交叉验证后同样调用 `self._run_stage_03()`
- 新增 `_run_stage_03()` 共享方法：打印阶段头，调用 `_run_s03()`，支持 `run_export=False` 跳过
- 新增 `run_export_only()` 入口：直接调用 `_run_s03()`，不经过 Flow
- 文档字符串更新，新增 `run_export=False` 示例

**新增流水线架构（完整）：**
```
s01 → s02a → router ──→ s02b ──┐
                   └──────────┘
                               ↓ _run_stage_03()
                              s03（省份提取 + 扁平表 + JSON 导出 + 同步到 s04）
```

---

### · `feat: add Makefile + bri.sh for root-level shortcuts`

#### 新增 `Makefile`（根目录）
- 目标：`pipeline` / `merge` / `export-only` / `collect` / `s03` / `dev` / `build` / `geo`
- 内部使用 `uv run --directory <subproject> <script>` 实现无 cd 执行
- `make help` 显示所有可用命令

#### 新增 `bri.sh`（根目录）
- 与 Makefile 功能完全一致，供未安装 make 的 Windows 用户使用
- 用法：`bash bri.sh <命令>`，支持 `pipeline` / `merge` / `export-only` / `collect` / `s03` / `dev` / `build` / `geo`
- 使用绝对路径（`$ROOT`），在任意工作目录下均可调用

---

## 2026-03-08（续）

### · `fix: use absolute path for WorldCountryList.json in ProjectExtractorTool`

#### `s01_data_collector_filter/src/s01_data_collector_filter/tools/project_extractor_tool.py`

**问题根因：**
`country_db_path` 的默认值为相对路径 `"WorldCountryList.json"`。当 crewai agent 调用该 tool 时，CWD 不一定是 `s01_data_collector_filter/` 根目录，导致 JSON 文件加载失败，静默回退到仅含"中国"的最小列表，所有其他国家均返回 `iso = "NA"`。s02a 的 `_clean("NA")` 会将 "NA" 转为空字符串，再经 `or "UNK"` 默认值，最终导致数据库中所有项目的 `country_iso` 均为 `"UNK"`。

**修复内容：**
- 新增模块级常量：
  - `_S01_ROOT = Path(__file__).resolve().parents[3]`（从 tool 文件上溯三层到子项目根目录）
  - `_DEFAULT_COUNTRY_DB = str(_S01_ROOT / "WorldCountryList.json")`（绝对路径）
- `ProjectExtractorInput.country_db_path` 默认值改为 `_DEFAULT_COUNTRY_DB`
- `ProjectExtractorTool._run()` 对应参数默认值同步更新
- 新增 `from pathlib import Path` 导入

**影响范围：** 修复后新运行的 s01 将正确匹配国家并写入 ISO 代码；已有数据库中的 `"UNK"` 记录需重新跑 s01 → s02a → s03 才能修正。

---

## 2026-03-09

### · `fix: add repair_country_iso.py to fix UNK country_iso in existing database`

#### 新增 `repair_country_iso.py`（根目录，一次性修复脚本）

**背景：**
数据库中存在大量 `country_iso = "UNK"` 的记录，原因是历史上部分 s01 运行（20260308_1232、20260308_2007 两批）因 CWD 不在 s01 根目录导致 country_db 加载失败，Excel 中写入 `"NA"`，pandas 读取时将 `"NA"` 视为 NaN，s02a `_clean(NaN)` → `""` → `"" or "UNK"` → `"UNK"`。同一国家在数据库中既有正确 ISO（来自其他运行批次）又有 UNK（来自问题批次），导致省级热力图无法显示。

**脚本功能（四步）：**
1. 加载 `WorldCountryList.json`（233 国，273 个名称/别名键）
2. 对所有 `BRI_RE_Final_Database_*.xlsx`（含 Archived/）中 `Country ISO` 为空/NaN 的行，重新执行国家名匹配并回填 Country ISO / BRI country / LDCs；合计修复 76 行
3. 将 Archived/ 中 5 个历史文件复制回 `processed/`，供 s02a 重建数据库
4. 删除旧数据库 `s02a_duplicate_merger/data/db/bri_re_database.db`

**运行方式：**
```bash
cd s03_data_exporter   # 借用已有 pandas venv
uv run python ../repair_country_iso.py
# 完成后：uv run --directory s02a_duplicate_merger s02a_duplicate_merger
#         uv run --directory s03_data_exporter run_export
```

---

### · `fix: rewrite download_geo.py to use 10m admin1 via zip+pyshp`

#### `s04_web_visualization/scripts/download_geo.py`

**问题根因（两个）：**
1. 原脚本从 `raw.githubusercontent.com` 下载 GeoJSON，但该仓库使用 Git LFS，raw CDN 仅返回 LFS 指针文件（~2.4 MB），只含 294 个 feature / 9 个国家
2. 经确认，Natural Earth **50m** admin1 本身就只包含 9 个大国省级数据；必须使用 **10m** 版本才有全球完整覆盖（~4500+ feature / 242 国）

**修复内容：**
- 改为下载官方 zip 包（naciscdn.org / Amazon S3 双源备用）
- 使用 `pyshp`（`pip install pyshp`）解析 zip 内 shapefile，转为 GeoJSON feature 列表
- 数据源从 50m 升级为 **10m**：`ne_10m_admin_1_states_provinces.zip`（约 10-15 MB）
- 新增缓存完整性检查：zip < 2 MB 视为截断，自动重新下载
- 新增 feature 数量校验：< 4000 时报错提示用 VPN 重试
- 多源下载：优先 naciscdn.org，失败时自动切换 Amazon S3

**已知限制：** 两个下载源在国内均有访问限制，需 VPN 或手动下载后放入 `scripts/_ne_admin1_cache.zip`。

---

### · `feat: add base="./" to vite.config.ts for portable build`

#### `s04_web_visualization/vite.config.ts`

- 新增 `base: "./"` 配置，使构建产物 `dist/` 使用相对路径
- `dist/` 可移动到任意目录，通过 `python -m http.server 8080` 启动即可访问
- 注意：不可直接双击 `index.html`（fetch 请求受浏览器 CORS 限制，需 HTTP 服务器）

---

## 2026-03-13

### · `refactor: s01 output → SQLite; s02a input → projects.db; s03 province constraint`

#### 架构动机
原来 s01 输出 Excel 文件、s02a 读取 Excel 目录，每次运行会覆盖旧文件，批次间无法累积。
改为 SQLite 后，s01 增量写入、s02a 按标志位增量消费，多批次数据自然合并，无需手动管理文件。

#### s01_data_collector_filter

**`tools/news_crawler_tool.py`**
- 新增 `_init_raw_news_db()`：初始化 `data/raw_news.db`（原始新闻备案库）
  - 表 `raw_news`：URL 唯一去重，跨批次持久化，含 `passed_keyword_filter`、`batch_id` 字段
- `NewsCrawlerInput` 新增 `raw_news_db_path` 字段（默认 `data/raw_news.db`）
- `_run()` 每条新闻写入 raw_news.db（`INSERT OR IGNORE`，DB 写入失败不影响主流程）

**`tools/project_extractor_tool.py`**
- 新增 `_init_projects_db()`：初始化 `data/projects.db`（有效项目库）
  - 表 `extracted_projects`：33 个字段，含 `batch_id`、`imported_to_s02a`（默认 0）、`created_at`
- 新增 `_init_review_db()`：初始化 `data/review.db`（审核记录库）
  - 表 `no_chinese_records`：URL 唯一，记录无中方参与文章
  - 表 `discarded_records`：URL 唯一，记录被过滤文章（含 `discard_reason`）
- `ProjectExtractorInput`：`output_dir` → `projects_db_path` + `review_db_path`
- `_run()`：提取结果由写 Excel 改为写 SQLite；AI 原始 JSON 存放路径改为 `projects.db` 同级目录

**`main.py`**
- 所有入口函数（`run/train/test/run_parallel/run_with_trigger`）：`processed_data_dir` → `projects_db_path` + `review_db_path`
- `archive_old_files()`：只归档 `data/raw/`（`data/processed/` 已废弃）

**`config/tasks.yaml`**
- `extract_projects_task` 描述更新：输出目标改为 SQLite 数据库路径

#### s02a_duplicate_merger

**`tools/duplicate_merger_tool.py`**
- 新增 `_load_from_db(input_db)`：从 `extracted_projects` 表读取 `imported_to_s02a=0` 的记录，重命名列为 Excel 格式列名（兼容后续逻辑），返回 `(DataFrame, record_ids)`
- 新增 `_mark_imported(input_db, record_ids)`：处理完成后将已处理记录标记为 `imported_to_s02a=1`
- `_find_duplicate_candidates()` 新增 **Signal 4（跨语言容量信道）**：
  - 触发条件：名称 Signals 1-3 均未命中 + 容量误差 ≤ 1% 且 ≥ 0.5MW
  - 解决历史数据中一方以英文为 project_name、另一方以中文导致名称分低（5~30）的漏判问题
  - 新增常量 `CAPACITY_EXACT_TOLERANCE = 0.01`、`CAPACITY_MIN_MW = 0.5`
- `_find_or_create_canonical()` 扩展为 **4 路名称对比较**（取最高分）：
  - incoming CN vs DB CN（原有）
  - incoming EN vs DB CN
  - incoming EN vs DB EN
  - incoming CN vs DB EN
- `DuplicateMergerInput`：`input_dir` → `input_db`（路径指向 `projects.db`）
- `_run()`：调用 `_load_from_db` 替代 `_load_excel_files`；新增阶段五调用 `_mark_imported`

**`main.py`**
- `run()` 中 `input_dir` → `input_db`，路径指向 `../s01_data_collector_filter/data/projects.db`

**`config/tasks.yaml`**
- 描述更新：从 Excel 目录改为从 SQLite 读取，新增第 5 步（标记已导入）

#### s03_data_exporter

**`admin1_lookup.py`** ✨ 新文件
- 从 `s04_web_visualization/scripts/_ne_admin1_cache.zip` 读取 Natural Earth 10m admin1 shapefile
- 构建 `{ISO3: [省份英文名, ...]}` 字典，模块级单例缓存（进程内只读一次 zip）
- zip 不存在或未安装 `pyshp` 时静默返回空字典（省份验证退化为无约束行为）

**`province_extractor.py`**
- 集成 `admin1_lookup.build_admin1_dict()`，为每个项目附加 `known_provinces` 列表
- Prompt 更新：Gemini 输出必须来自 `known_provinces` 列表或 null，不得自行创造列表外名称
- `records` 新增 `country_iso` 字段（供 admin1_lookup 查找对应省份列表）

**`main.py`**
- `canonical_projects` 查询新增 `country_iso` 字段

**`pyproject.toml`**
- 新增依赖 `pyshp>=2.3.0`（admin1_lookup 解析 shapefile 所需）

#### pipeline

**`flow.py`**
- `PipelineState`：移除 `processed_data_dir`、`input_dir`，新增 `projects_db_path`、`review_db_path`、`input_db`
- `stage_01_collect()`：kickoff inputs 换用新参数
- `stage_02a_merge()`：`input_dir` → `input_db`
- `_archive_s01_files()`：签名从 `(raw_dir, processed_dir)` 改为仅 `(raw_dir,)`，只归档原始爬虫文件

---

## 2026-03-13（续）

### · `feat: add retry mechanism to project_extractor_tool`

#### `s01_data_collector_filter/tools/project_extractor_tool.py`

- AI 调用 + JSON 解析部分新增最多 3 次指数退避重试：
  - 第 1 次失败：等 2s 后重试
  - 第 2 次失败：等 4s 后重试
  - 第 3 次失败：放弃，记入 `discarded_records`（原因注明"重试3次失败"）
- 覆盖所有异常类型（API 超时、限流、JSON 解析失败等）
- `result` 先初始化为 `None`，成功解析后赋值；后续逻辑改为 `if result is None / else` 结构

---

### · `feat: add run_collect_and_merge entry point to pipeline`

#### `pipeline/src/bri_pipeline/flow.py`
- 新增 `run_collect_and_merge()` 入口函数：仅执行阶段一（数据采集）+ 阶段二A（去重合并），跳过交叉验证和导出
- 文件头部用法注释新增 `uv run run_collect_and_merge` 说明

#### `pipeline/pyproject.toml`
- `[project.scripts]` 新增 `run_collect_and_merge = "bri_pipeline.flow:run_collect_and_merge"`

---

---

## 2026-03-14

### · `fix+feat: dedup/forward-fill/new-categories/FI-refactor`

#### s01_data_collector_filter — `tools/project_extractor_tool.py`

**1. 增量去重修复（避免重复入库）**

原 `_run()` 每次启动都会重新处理所有 Excel 行，无法跳过已处理的新闻 URL，导致 `projects.db` 中出现重复记录。

修复方案：在主循环开始前，从 `projects.db`（`extracted_projects.source_link`）和 `review.db`（`no_chinese_records.source_link` + `discarded_records.source_link` UNION）各加载已处理 URL，合并为 `already_processed: set[str]`。主循环中检测到 URL 已在集合内则跳过；处理完一条后立即将 URL 加入集合。

**2. 发布日期前向填充（publish_date forward-fill）**

原逻辑：当新闻 `publish_date` 无法正确解析（"NA"/"nan"/空）时，直接保留 "NA" 写入数据库。

修复方案：在主循环外初始化 `last_valid_pub_date = ""`；每条记录处理时：
- 若 `pub_date` 含数字字符且非 "NA" → 更新 `last_valid_pub_date`
- 否则若 `last_valid_pub_date` 非空 → 用其填充当前 `pub_date`

**3. 新增项目类别：火电、新能源制造业、输变电建设**

Prompt 中 `category` 枚举由原有 7 类扩展为 10 类：
```
[新能源制造业，输变电建设，火电，光伏, 风电, 水电, 地热及余热, 生物质, 储能, 综合能源]
```

**4. 中方金融机构处理方式重构（FI Refactor）**

问题：Prompt 规则要求"不在 chinese_entities 中列金融机构"，但 LLM 仍持续将银行/基金写入 chinese_entities 并赋予 `投融资+EPC` 类型，导致混淆。

解决方案：改为允许金融机构进入 `chinese_entities`，以 `融资方` 作为专属 involvement_type，同时移除冗余的独立字段 `Involvement of Chinese Financial Institution` / `chinese_banks`。

具体改动：
- **`extracted_projects` 表 Schema**：删除 `chinese_banks TEXT` 列（34→33个字段）
- **Prompt JSON schema**：从 `common_info` 中删除 `"Involvement of Chinese Financial Insitution": "String"` 字段
- **Prompt involvement_type 枚举**：新增 `融资方`（纯金融机构专用）
  ```
  [BOT/BOOT, EPC总承包, 设计/施工服务方, 股权投资方, 设备供应商, 投融资+EPC, 融资方, Unknown]
  ```
- **Prompt 规则 4（中方企业填写规则）**：重写为"所有参与中方企业（含金融机构）均填入 chinese_entities；金融机构 involvement_type 必须为 '融资方'；'投融资+EPC' 仅用于同一企业既投股权又承担EPC的情况"
- **`base` dict**：删除 `"Involvement of Chinese Banks"` 键
- **INSERT 语句**：删除 `chinese_banks` 列和对应值（34→33个参数）

---

#### s02a_duplicate_merger — `tools/duplicate_merger_tool.py`

**1. 新增类别代码：TH / MF / TX**

`CATEGORY_CODE` 字典新增三条：
```python
"火电": "TH", "新能源制造业": "MF", "输变电建设": "TX"
```

**2. 去重阈值收紧（Recommendation A）**

| 常量 | 旧值 | 新值 | 说明 |
|------|------|------|------|
| `CAPACITY_TOLERANCE` | 0.10 | 0.05 | 容量误差容忍度（10%→5%） |
| `CAPACITY_BYPASS_SCORE` | 92 | 97 | 豁免容量检查的名称分数门槛 |
| `CAPACITY_MIN_MW` | 0.5 | 50 | 容量检查最小装机（避免小容量误判） |

**3. LLM 确认 Prompt 增强（Recommendation B）**

Prompt 新增上下文字段：`省/地区`（location）、`项目开发商`（project_developer）。

新增判断依据说明（按优先级）：
1. 省/地区、装机容量均一致 → 支持合并
2. 项目开发商明显不同（非同一企业或集团）→ 倾向不合并
3. 装机容量差异较大（>10%）→ 倾向不合并

**4. 金融机构重构（与 s01 同步）**

- **`project_events` 表 Schema**：删除 `chinese_banks_involved TEXT` 列
- **`_load_from_db()` 列名映射**：删除 `"chinese_banks": "Involvement of Chinese Banks"` 条目
- **`_group_into_logical_projects()`**：删除 `"chinese_banks": _clean(...)` 字段
- **`_write_to_db()` INSERT**：删除 `chinese_banks_involved` 列和 `project["chinese_banks"]` 值（17→16个参数）

> 注意：以上改动需重新从零建库（删除旧 `bri_re_database.db` 并重跑 s02a）。

---

## 说明

- 所有 crewai agent 均显式指定 `llm: gemini/gemini-2.5-flash-lite`，确保不使用默认 OpenAI 模型
- 子项目命名规范：`sNN_name`（目录、包名、模块名三者一致）
- 双语字段（EN）贯穿整个流水线：AI 提取阶段输出 → SQLite 存储 → 向量匹配使用
- `.db` 数据库文件已排除 git，需本地运行生成
