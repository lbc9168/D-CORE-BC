# eval — s01b 提取器金标准评测框架（v2.1 P0 / P0.1）

> 最后更新：2026-05-24（P0.1 多项目升级已落地）

**模块路径：** `eval/`（仓库根）

---

## 定位

为 `s01b_project_extractor` 的 AI 提取提供一把「尺子」：约 150 篇人工核对过的金标准文章 + 字段级准确率，
使 P1 起每次 prompt / agent 改动可被量化为「变好 / 变差」，而非凭感觉。

设计 / 计划：
- spec：`docs/superpowers/specs/2026-05-24-v2.1-P0-eval-set-design.md`
- plan：`docs/superpowers/plans/2026-05-24-v2.1-P0-eval-set.md`
- **P0.1 多项目升级** spec：`docs/superpowers/specs/2026-05-24-v2.1-P0.1-multiproject-eval-design.md`
- **P0.1 多项目升级** plan：`docs/superpowers/plans/2026-05-24-v2.1-P0.1-multiproject-eval.md`

---

## 组成

| 文件 | 作用 |
| --- | --- |
| `metrics.py` | 比较器（P0.1 多项目）：status 闸门 + `align_projects` 贪心对齐 + 逐项目枚举/数值 + `score_entities`（名字集合 F1 + 角色准确率）；`score_article(pred, gold)` |
| `predict.py` | `flat_prediction(result)`：提取器原始 result → 文章级 `{status, projects:[{字段, entities:[{name,involvement_type}]}]}`（**全部项目**） |
| `article_store.py` | `get_article(url, db_path)` 取原文；`cache_*` 按 url+版本缓存提取结果（避免重复付费） |
| `build_draft.py` | 分层抽样 → `draft.csv`（**一项目一行**，AI 预填 + 空列待人工修正） |
| `gold_loader.py` | `load(path)` 读 `gold_set.jsonl`（**嵌套 schema**：文章级 status + projects[]），Pydantic 校验，非法行带行号报错 |
| `csv_to_gold.py` | 多行 `draft.csv` **按 source_link 聚合** → `gold_set.jsonl`；实体 `名字(角色)` 解析（角色取末括号，名字可含括号） |
| `run_eval.py` | 跑提取器 → `score_article` 打分 → 写 `reports/<ts>.json`（含 project_set / entity role）→ 与上次报告 ↑/↓ 对比 |
| `README.md` | 标注 + 评测全流程文档（P0.1 多项目） |

测试（`tests/`）：`test_eval_metrics.py`、`test_predict.py`、`test_gold_loader.py`、`test_article_store.py`、
`test_csv_to_gold.py`、`test_extract_one_smoke.py`、`test_run_eval_e2e.py`（端到端 mock 多项目）。

---

## 如何运行

均从 worktree 根用 s01b venv 运行：

```bash
# 1) 生成草稿（DB 在主仓库，用绝对路径覆盖）
uv --project s01b_project_extractor run python -m eval.build_draft \
  --n 150 --out eval/draft.csv \
  --projects-db  E:/Projects/BRI_RE_Database_v2/s01b_project_extractor/data/projects.db \
  --raw-news-db  E:/Projects/BRI_RE_Database_v2/s01a_news_crawler/data/raw_news.db

# 2) 人工标注 draft.csv（utf-8-sig，逐篇读原文修正）
uv --project s01b_project_extractor run python -m eval.csv_to_gold --csv eval/draft.csv --out eval/gold_set.jsonl

# 3) 真实评测（需 .env 内 GEMINI_API_KEY）
uv --project s01b_project_extractor run python -m eval.run_eval --gold eval/gold_set.jsonl

# 测试（无 Gemini，注入 mock）
uv --project s01b_project_extractor run python -m pytest tests/test_run_eval_e2e.py -v
```

数据库在**主仓库**（非本 worktree）：
- 原文：`s01a_news_crawler/data/raw_news.db`
- 提取结果：`s01b_project_extractor/data/projects.db`

本地产物（`draft*.csv` / `gold_set.jsonl` / `cache/` / `reports/` / `*.log`）均已 gitignore。

---

## 当前状态

- **框架：✅ 已完成（含 P0.1 多项目升级，2026-05-24）**（7 文件 + README + 6 测试文件，全 35 测试绿）。
  P0.1 落地内容：项目级**贪心对齐**（`align_projects`，rapidfuzz 阈值 80）、**嵌套文章金标准**（status + projects[]）、
  **一项目一行** draft.csv、**per-entity 角色**（involvement_type）、新报告指标（`project_set` P/R/F1 + `entity.role_accuracy`）。
- **CSV→jsonl 转换脚本：✅ 已内置**（`csv_to_gold.py`，按 source_link 聚合 + `名字(角色)` 解析）。
- **金标准标注：⬜ 待人工**——需跑 `build_draft.py` → 人工读原文修正（一项目一行、entities 写 `名字(角色)`）→
  `csv_to_gold.py` 转 `gold_set.jsonl`。**多项目金标准尚未标注**。
- 真实 `run_eval`（调 Gemini）依赖：`GEMINI_API_KEY`（.env）+ 已标注的 `gold_set.jsonl`。
