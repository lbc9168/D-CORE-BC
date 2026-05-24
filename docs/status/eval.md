# eval — s01b 提取器金标准评测框架（v2.1 P0）

> 最后更新：2026-05-24

**模块路径：** `eval/`（仓库根）

---

## 定位

为 `s01b_project_extractor` 的 AI 提取提供一把「尺子」：约 150 篇人工核对过的金标准文章 + 字段级准确率，
使 P1 起每次 prompt / agent 改动可被量化为「变好 / 变差」，而非凭感觉。

设计 / 计划：
- spec：`docs/superpowers/specs/2026-05-24-v2.1-P0-eval-set-design.md`
- plan：`docs/superpowers/plans/2026-05-24-v2.1-P0-eval-set.md`

---

## 组成

| 文件 | 作用 |
| --- | --- |
| `metrics.py` | 比较器：status 闸门 + 枚举归一相等 + 数值 ±5% + 实体 P/R/F1；`score_record(pred, gold)` |
| `predict.py` | `flat_prediction(result)`：提取器原始 result → 评测用扁平记录（P0 只取主项目） |
| `article_store.py` | `get_article(url, db_path)` 取原文；`cache_*` 按 url+版本缓存提取结果（避免重复付费） |
| `build_draft.py` | 分层抽样 → `draft.csv`（AI 预填 + 空列待人工修正） |
| `gold_loader.py` | `load(path)` 读 `gold_set.jsonl`，Pydantic 校验，非法行带行号报错 |
| `run_eval.py` | 跑提取器 → 打分 → 写 `reports/<ts>.json` → 与上次报告 ↑/↓ 对比 |
| `README.md` | 标注 + 评测全流程文档 |

测试（`tests/`）：`test_eval_metrics.py`、`test_predict.py`、`test_article_store.py`、
`test_extract_one_smoke.py`、`test_run_eval_e2e.py`（端到端 mock 提取器）。

---

## 如何运行

均从 worktree 根用 s01b venv 运行：

```bash
# 1) 生成草稿（DB 在主仓库，用绝对路径覆盖）
uv --project s01b_project_extractor run python -m eval.build_draft \
  --n 150 --out eval/draft.csv \
  --projects-db  E:/Projects/BRI_RE_Database_v2/s01b_project_extractor/data/projects.db \
  --raw-news-db  E:/Projects/BRI_RE_Database_v2/s01a_news_crawler/data/raw_news.db

# 2) 人工标注 draft.csv（utf-8-sig，逐篇读原文修正）→ 手工转 eval/gold_set.jsonl

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

- **框架：✅ 已完成**（6 文件 + README + 5 测试文件，全绿；端到端 mock 测试验证报告数值正确）。
- **金标准标注：⬜ 待人工**——需跑 `build_draft.py` → 人工读原文修正 ~150 篇 → 转 `gold_set.jsonl`。
- **CSV→jsonl 转换脚本：⬜ 未内置**（P0 未做，README 已给期望形状，可手工或后续小脚本生成）。
- 真实 `run_eval`（调 Gemini）依赖：`GEMINI_API_KEY`（.env）+ 已标注的 `gold_set.jsonl`。
