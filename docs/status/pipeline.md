# pipeline · 流水线编排与批量采集

> 最后更新：2026-06-24

---

## 职责

`pipeline/` 用一条命令把各阶段子项目串成完整流水线（纯 Python 顺序编排，无 crewAI）。
阶段之间靠 SQLite 文件接力（不依赖内存状态），可按需跳过任意阶段。

---

## 当前功能

### 顺序编排（`src/bri_pipeline/flow.py`）

核心是 `run_pipeline(cfg: PipelineConfig)`，按下列顺序执行：

```
阶段一A 新闻采集    ← cfg.skip_s01a=True 时跳过（→ s01a.run_single）
   ↓
阶段一B AI 提取     ← 始终执行（→ s01b.run_bulk_extract，仅传 raw_news_db_path）
   ↓
阶段二A 去重合并入库 ← 始终执行（→ 直调 DuplicateMergerTool()._run(input_db, db_path)，绝对路径）
   ↓
阶段二B 实体归一化   ← cfg.run_entity_normalize=False 时跳过（通常不跳）
   ↓
预留（默认关，置标志位启用，函数内延迟 import）：
   阶段二C 交叉验证  ← cfg.run_cross_verification=True
   阶段三   导出     ← cfg.run_export=True
```

> 「串某几段」= 构造对应的 `PipelineConfig` 再调 `run_pipeline(cfg)`；「单独跑某段」进对应子项目 `uv run sNN_xxx`。

### 支持路径

```
路径一（完整）：    s01a → s01b → s02a → s02b
路径二（跳过爬虫）： s01b → s02a → s02b（使用已有 raw_news.db）
```

### PipelineConfig 参数（`@dataclass`）

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `skip_s01a` | bool | False | 跳过爬虫（用已有 raw_news.db，即路径二） |
| `run_entity_normalize` | bool | True | 是否执行 s02b 实体归一化 |
| `run_cross_verification` | bool | False | 是否执行 s02c 交叉验证（预留） |
| `run_export` | bool | False | 是否执行 s03 导出（预留） |
| `start_date` / `end_date` | str | 见代码 | s01a 爬取日期范围 |
| `raw_news_db_path` | str | s01a/data/raw_news.db | s01a 输出 / s01b 输入 |
| `input_db` | str | s01b/data/projects.db | s02a 输入 |
| `db_path` | str | s02a/data/db/bri_re_database.db | s02a 输出（主库），同时作 s02b 入参 |

---

## 如何运行

```bash
cd pipeline
uv sync                   # 首次安装（自动以 editable 安装所有本地子项目）

uv run run_pipeline       # 路径一：完整流水线（s01a→s01b→s02a→s02b）
uv run run_from_s01b      # 路径二：跳过爬虫，用已有 raw_news.db
uv run run_export_only    # 仅 s03 导出（数据库已有时）
```

根目录快捷命令（等效）：

```bash
make pipeline        # = uv run run_pipeline
make from-s01b       # = uv run run_from_s01b
make export-only     # = uv run run_export_only
./bri.sh pipeline    # bash 脚本（Windows Git Bash / Linux）
```

### 批量采集 / 提取入口（历史补录用，可单独跑）

```bash
cd s01a_news_crawler   && uv run run_bulk_crawl     # 按月顺序爬取 + checkpoint 接力
cd s01b_project_extractor && uv run run_bulk_extract # 并发调用 Gemini 批量提取（默认 4 并发）
```

---

## 输入 / 输出

- **输入**：goalfore.cn 新闻页（s01a 联网抓取）或已有的 `s01a_news_crawler/data/raw_news.db`。
- **输出**：逐阶段 SQLite 文件接力，最终主库 `s02a_duplicate_merger/data/db/bri_re_database.db`；若启用 `run_export`，则导出到 `s03_data_exporter/data/output/` 并同步前端。

---

## 关键依赖与约束

1. 首次 `uv sync` 需要各子项目目录已存在（以 editable 模式安装）。
2. 根目录 `.env` 需包含 `GEMINI_API_KEY`。
3. 路径二（`run_from_s01b`）需要 `s01a/data/raw_news.db` 已存在。
4. s02a 段直调 tool 用**绝对路径**（不走 `s02a.run()`，因其写死相对路径会按 pipeline 的 CWD 写错文件）。
