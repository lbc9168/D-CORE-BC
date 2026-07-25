# pipeline · 流水线编排与批量采集

> 最后更新：2026-07-24（新增阶段 2B-7 储能/光伏子项/子类回填：storage_*、pv_capacity_mw、subcategory，fill-if-null）

---

## 职责

`pipeline/` 用一条命令把各阶段子项目串成完整流水线（纯 Python 顺序编排，无 crewAI）。
阶段之间靠 SQLite 文件接力（不依赖内存状态），可按需跳过任意阶段。s01b 内部实体处理采用"S4 逐字抄原文名 → 写库前 entity_normalize 确定性查表解析（company_hierarchy_v2.json）"两步，与 s02b 归一化独立。

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
阶段二B-2 地点归一化 ← cfg.run_location_normalize=False 时跳过（写 location_norm）
   ↓
阶段二B-3 业主归一化 ← cfg.run_developer_normalize=False 时跳过（登记册查表前置归一 project_developer；LLM 降级为补录建议 CSV）
   ↓
阶段二B-4 英文名回填 ← cfg.run_en_backfill=False 时跳过（registry canonical_en > 缓存 en_abbr > en 三档口径重算 *_en 列，miss 只报告）
   ↓
阶段二B-5 TX/MF 专用列回填 ← cfg.run_txmf_backfill=False 时跳过（txmf_spec_parser 确定性解析 details/summary，fill-if-null 回填 5 稀疏列；流水线模式只打印 misses 计数，CSV 由手跑 CLI 带 --out 产出）
   ↓
阶段二B-6 剩余名称翻译回填 ← cfg.run_residual_translate=False 时跳过（2B-4 miss 的长尾中文名：registry>主缓存>残余缓存>LLM 批翻，写 residual_translation_cache.json，fill-if-null 只填 *_en 空/含中文行）
   ↓
阶段二B-7 储能/光伏子项/子类回填 ← cfg.run_storage_subcat_backfill=False 时跳过（storage_parser+subcategory_rules 确定性解析 capacity_details 等文本，fill-if-null 回填 storage_power_mw/storage_energy_mwh（全类别）、pv_capacity_mw（仅综合能源）、canonical.subcategory（光伏/综合能源，只写有证据值，NULL 由 s03 导出层渲染默认））
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
| `run_location_normalize` | bool | True | 是否执行地点归一化（写 location_norm） |
| `run_developer_normalize` | bool | True | 是否执行业主归一化（project_developer 部件级） |
| `run_en_backfill` | bool | True | 是否执行英文名回填（缓存口径同步 *_en 列） |
| `run_txmf_backfill` | bool | True | 是否执行 TX/MF 专用列回填（s01b txmf_backfill，fill-if-null） |
| `run_residual_translate` | bool | True | 是否执行剩余名称翻译回填（长尾名 LLM 翻译+残余缓存，fill-if-null） |
| `run_storage_subcat_backfill` | bool | True | 是否执行储能/光伏子项/子类回填（s01b storage_subcat_backfill，fill-if-null） |
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
5. **主库原地回填后的部署顺序（硬约束）**：凡对主库 `chinese_entities` 做过实体回填
   （如 `entity_reparse_maindb --apply`，其规则为「CN 变 → 对应 `*_en` 置 NULL」）后，
   部署必须按 **en_backfill → run_export(s03) → 部署（gh-pages）** 顺序执行，
   不得跳过 en_backfill 直接导出——回填后主库英文名大面积为空
   （2026-07-06 实测 `implementing_entity_en` 空 3660/6519 ≈ 56%、
   `subsidiary_name_en` 缺 666），先导出/部署会把空英文名发布到前端。
   en_backfill 单独跑：`cd s02b_entity_normalizer && uv run backfill_entity_en`
   （或走 pipeline `run_en_backfill=True` 段）。
