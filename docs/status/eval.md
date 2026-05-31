# eval — s01b 提取器金标准评测框架（v2.1 P0 / P0.1）

> 最后更新：2026-05-30（金标准已标注 97 篇 + P1 改造前基线已跑出）

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

# 3) 真实评测（需 .env 内 GEMINI_API_KEY；DB 在主仓库，用绝对路径覆盖）
uv --project s01b_project_extractor run python -m eval.run_eval --gold eval/gold_set.jsonl \
  --raw-news-db E:/Projects/BRI_RE_Database_v2/s01a_news_crawler/data/raw_news.db

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
- **金标准标注：✅ 已完成（2026-05-30）**——人工标注 `eval/draft_reviewed.csv` → `csv_to_gold` 转出
  `eval/gold_set.jsonl`，**97 篇**（valid 61 / irrelevant 27 / no_chinese 9，72 个项目，全类别覆盖）。
  （`draft.csv` 全量 165 篇里另有 48 篇中段未审，已从 reviewed 剔除、留作以后扩充；备份 `eval/draft_reviewed.bak.csv`。）

## P1 改造前基线（2026-05-30，report `eval/reports/20260530_231934.json`，extractor gemini-2.5-flash-lite）

| 指标 | 值 | 解读 |
| --- | --- | --- |
| **weighted_total** | **0.7396** | 四等权综合分（status/字段均值/实体名/实体角色） |
| status_accuracy | 0.7938 | ⚠️ 主要弱项：**负样本剔除率仅 ~47%**（36 负样本只剔对 ~17）——过度收录油气/矿业/民用/纯设备 |
| project_set P/R/F1 | 0.678 / 0.831 / 0.747 | pred 87 vs gold 71，**过度拆分/多编**（precision 低） |
| category / project_stage | 0.881 / 0.881 | 真实信号，尚可 |
| capacity_mw | 0.915 | 好 |
| total_investment_usd | 0.915 | ⚠️ 偏高有水分（多为空==空） |
| financing_model | 0.983 | ⚠️ 严重虚高（几乎全 Unknown==Unknown） |
| entity name_f1_mean | **0.565** | ⚠️ 最低：含真实抽取错误 + **母子公司未归一**（gold 已归一、提取器照搬文章名，被双重扣分） |
| entity role_accuracy | 0.684 | involvement_type，38 个同名实体上 |

**P1 优先级（由基线驱动）：① S1 相关性闸门（剔除率 47%，收益最大）→ ② S4 实体抽取 + 母子公司归一（name F1 0.57）→ ③ S2 项目枚举防过度拆分（precision 0.68）→ ④ 字段（已 88%+，最后）。**

- 真实 `run_eval`（调 Gemini）依赖：`GEMINI_API_KEY`（.env）+ 已标注的 `gold_set.jsonl`。结果按 url+prompt 哈希缓存。

## P1 进展

### Phase 1：S1 相关性门拆出（2026-05-31，report `eval/reports/20260531_123246.json`，extractor gemini-2.5-flash-lite + 独立 S1 门控）

| 指标 | 基线 | Phase 1 | 方向 |
| --- | --- | --- | --- |
| **weighted_total** | 0.7396 | **0.7767** | ↑ +0.037 |
| **status_accuracy** | 0.7938 | **0.8229** | ↑ +0.029（Phase 1 主攻达标） |
| project_set P/R/F1 | 0.678/0.831/0.747 | 0.66/0.74/0.697 | ↓（legacy S2-5 未改，Phase 3 攻） |
| category / project_stage | 0.881 / 0.881 | 0.925 / 0.868 | category↑ / stage 微降 |
| entity name_f1_mean | 0.565 | 0.613 | ↑ +0.048 |
| entity role_accuracy | 0.684 | 0.750 | ↑ +0.066 |

**核心改善（混淆矩阵证据）**：负样本误收（gold=irrelevant→pred=valid）**16→7**（基线最大弱项「剔除率 47%」显著改善）；gold=irrelevant 正确剔除 9→18。

**Phase 1 调试历程（systematic-debugging，2 轮 S1 prompt 修复）**：
1. 首版 S1 过度激进，按燃料把火电/核电/燃气**电站**误排 + B.2/会议规则过严 → 误杀 10 篇 gold=valid（正样本召回 60→51）。诊断（零 API，读缓存混淆矩阵）后**按 gold 扩范**：所有发电项目不限燃料均 valid，油气/矿业排除仅限非发电环节，软化 B.2/会议/数据中心。
2. 修复后正样本召回回升但 `no_chinese` 被 `irrelevant` 吞并（8→2）——根因：irrelevant 旧定义字面含"无中企的发电项目"。改为**两步决策**（先判电力主题→irrelevant；再判中企→valid/no_chinese，明确"电力项目但无中企必判 no_chinese"）→ status 升至 0.8229。

> 注：`n_failed=1` 为 fail-loud 生效（S1 重试后仍非法 status → RuntimeError，可见非静默）。诊断脚本 `eval/_diag_s1.py` 为一次性工具，已删。
