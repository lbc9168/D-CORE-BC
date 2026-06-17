# 流水线编排与批量采集

> 最后更新：2026-06-02（v2.1 P5：去 crewAI Flow → 纯 Python `run_pipeline(cfg)`）

---

## 流水线编排（纯 Python 顺序编排）

**子项目路径：** `pipeline/`

一次命令运行完整流水线，支持按需跳过各阶段。**v2.1 P5 起**：由 crewAI `Flow`（`@start`/`@listen`）改为纯 Python 顺序函数 `run_pipeline(cfg: PipelineConfig)`——阶段间靠 SQLite 文件接力（不靠内存 state），能力等价、命令名不变。

### 当前支持路径

```
路径一（完整）：    s01a → s01b → s02a → s02b
路径二（跳过爬虫）： s01b → s02a → s02b（使用已有 raw_news.db）
```

### 执行流程

```
run_pipeline(cfg)
  阶段一A：新闻采集            ← cfg.skip_s01a=True 时跳过（→ _run_s01a = s01a.run_single）
       ↓
  阶段一B：AI 提取            ← 始终执行（→ _run_s01b = s01b.run_bulk_extract，仅传 raw_news_db_path）
       ↓
  阶段二A：去重合并入库        ← 始终执行（→ 直调 DuplicateMergerTool()._run(input_db, db_path)，绝对路径）
       ↓
  阶段二B：实体归一化          ← cfg.run_entity_normalize=False 时跳过（不建议）
       ↓
  打印"✅ 流水线执行完毕"

预留（默认关，置标志位即启用，函数内延迟 import）：
  阶段二C：交叉验证            ← cfg.run_cross_verification=True（→ s02c.run）
  阶段三：导出                ← cfg.run_export=True（→ s03.run）
```

> 「串某几段」= 构造 `PipelineConfig(skip_s01a=True, run_entity_normalize=False, run_cross_verification=True)` 之类再调 `run_pipeline(cfg)`；「单独跑某段」仍可进对应子项目 `uv run sNN_xxx`。

### 运行方式

```bash
cd pipeline
uv sync                   # 首次安装（自动安装所有本地子项目依赖）

# 路径一：完整流水线（s01a→s01b→s02a→s02b）
uv run run_pipeline

# 路径二：跳过爬虫，使用已有 raw_news.db（s01b→s02a→s02b）
uv run run_from_s01b

# 仅 s03 导出（数据库已有时使用）
uv run run_export_only
```

或使用根目录快捷命令：

```bash
make pipeline        # 等同 uv run run_pipeline
make from-s01b       # 等同 uv run run_from_s01b
make export-only     # 等同 uv run run_export_only

# bash 脚本（Windows Git Bash / Linux）
./bri.sh pipeline
./bri.sh from-s01b
```

### PipelineConfig 参数（`@dataclass`，替代原 crewAI PipelineState）

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `skip_s01a` | bool | False | 跳过 s01a（使用已有 raw_news.db，即路径二） |
| `run_entity_normalize` | bool | True | 是否执行 s02b 实体归一化（通常不跳过） |
| `run_cross_verification` | bool | False | 是否执行 s02c 交叉验证（后续路径，预留） |
| `run_export` | bool | False | 是否执行 s03 导出（后续路径，预留） |
| `start_date` / `end_date` | str | 见代码 | s01a 爬取日期范围 |
| `raw_news_db_path` | str | s01a/data/raw_news.db | s01a 输出 / s01b 输入 |
| `projects_db_path` / `review_db_path` | str | s01b/data/*.db | 保留供参考；`run_bulk_extract` 内部固定这两路径，当前不透传 |
| `input_db` | str | s01b/data/projects.db | s02a 输入（直调 tool 时传入） |
| `db_path` | str | s02a/data/db/bri_re_database.db | s02a 输出（主数据库），同时作 s02b 入参 |

---

## 批量并发采集（s01a run_bulk_crawl）

**子项目路径：** `s01a_news_crawler/`

用于历史数据批量补录，两阶段执行。

### 运行方式

```bash
cd s01a_news_crawler
uv run run_bulk_crawl    # 使用默认参数（按月拆分，顺序爬取 + checkpoint 接力）
```

---

## AI 批量提取（s01b run_bulk_extract）

**子项目路径：** `s01b_project_extractor/`

并发调用 Gemini AI，从 raw_news.db 批量提取项目结构化信息。

### 运行方式

```bash
cd s01b_project_extractor
uv run run_bulk_extract  # 默认 4 并发
```

---

## 首次运行注意事项

1. `pipeline/` 首次 `uv sync` 需要各子项目目录已存在（自动以 editable 模式安装）
2. `.env` 文件需在根目录存在，包含 `GEMINI_API_KEY`
3. 路径二（`run_from_s01b`）需要 `s01a/data/raw_news.db` 已存在

---

## 2026-06-02（v2.1 P5）去 crewAI Flow → 纯 Python

- `flow.py` 由 crewAI `Flow[PipelineState]`（`@start`/`@listen`）改为 `@dataclass PipelineConfig` + `run_pipeline(cfg)` 顺序编排；删 `crewai.flow.flow` / `pydantic.BaseModel` / pysbd 警告抑制；pyproject 去 `crewai[tools,google-genai]` + `[tool.crewai]`。三入口 `run_full_pipeline`/`run_from_s01b`/`run_export_only` 与脚本名、能力不变。
- 一并修复 P1 后已断的 import：原 `from s01b_project_extractor.crew import ProjectExtractor`（crew 已在 P1 删）导致整个 pipeline import 报错——改为 `from s01b_project_extractor.main import run_bulk_extract`。
- 行为对齐：s01b 段仅传 `raw_news_db_path`（`run_bulk_extract` 内部固定 projects/review 路径）；s02a 段直调 `DuplicateMergerTool()._run(input_db, db_path)` 用**绝对路径**，保持原 crewAI kickoff 行为（不调 s02a.run()，因其写死相对路径会按 pipeline CWD 写错文件）。
- 验证：smoke import 通过（不再因 crewai/已删 crew 报错）；未真跑全流水线。

## 2026-03-22 稳定性修复

- `flow.py` 调整为按需导入 `s03_data_exporter`，降低 `run_pipeline` / `run_from_s01b` 对后续预留阶段模块的导入耦合风险（P5 后仍保留：s02c/s03 在各自 `if` 块内延迟 import）。
- `_archive_s01a_files` 归档逻辑增加目录存在性保护：`s01a/data/raw` 不存在时跳过归档，不再因 `FileNotFoundError` 中断流水线（P5 保留原逻辑不变）。
