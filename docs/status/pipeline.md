# 流水线编排与批量采集

> 最后更新：2026-03-22

---

## 流水线编排（crewAI Flows）

**子项目路径：** `pipeline/`

一次命令运行完整流水线，支持按需跳过各阶段。

### 当前支持路径

```
路径一（完整）：    s01a → s01b → s02a → s02b
路径二（跳过爬虫）： s01b → s02a → s02b（使用已有 raw_news.db）
```

### 执行流程

```
BRIPipelineFlow
  stage_01a_crawl   (@start)    ← skip_s01a=True 时跳过
       ↓ @listen
  stage_01b_extract             ← 始终执行
       ↓ @listen
  stage_02a_merge               ← 始终执行
       ↓ @listen
  stage_02b_normalize           ← run_entity_normalize=False 时跳过（不建议）
       ↓
  打印"✅ 流水线执行完毕"

预留（后续路径扩展）：
  stage_02c_verify              ← s02c 交叉验证（代码已预留，未挂载）
  stage_03_export               ← s03 数据库导出（代码已预留，未挂载）
```

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

### PipelineState 参数

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `skip_s01a` | bool | False | 跳过 s01a（使用已有 raw_news.db，即路径二） |
| `run_entity_normalize` | bool | True | 是否执行 s02b 实体归一化（通常不跳过） |
| `run_cross_verification` | bool | False | 是否执行 s02c 交叉验证（后续路径，预留） |
| `run_export` | bool | False | 是否执行 s03 导出（后续路径，预留） |
| `start_date` / `end_date` | str | 见代码 | s01a 爬取日期范围 |
| `raw_news_db_path` | str | s01a/data/raw_news.db | s01a 输出 / s01b 输入 |
| `projects_db_path` | str | s01b/data/projects.db | s01b 输出 / s02a 输入 |
| `db_path` | str | s02a/data/db/bri_re_database.db | s02a 输出（主数据库） |

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

## 2026-03-22 稳定性修复

- `flow.py` 调整为按需导入 `s03_data_exporter`，降低 `run_pipeline` / `run_from_s01b` 对后续预留阶段模块的导入耦合风险。
- `stage_01a_crawl` 归档逻辑增加目录存在性保护：`s01a/data/raw` 不存在时跳过归档，不再因 `FileNotFoundError` 中断流水线。
