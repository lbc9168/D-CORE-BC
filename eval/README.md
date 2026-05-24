# eval — s01b 提取器金标准评测框架

为 `s01b_project_extractor` 的 AI 提取提供一把「尺子」：约 150 篇人工核对过的金标准文章 + 字段级准确率，
让每一次 prompt / agent 改动都能被量化为「变好」还是「变差」，而不是凭感觉。

---

## 工作流总览

```
build_draft.py  →  draft.csv  →  (人工标注)  →  gold_set.jsonl  →  run_eval.py  →  reports/<ts>.json
   分层抽样        AI 预填+空列     读原文逐条修正      金标准              跑当前提取器打分      ↑/↓ 对比上次
```

### 1. 生成草稿 `build_draft.py → eval/draft.csv`

按 category + 难度桶（容量缺失 / 数据中心 / 设备供应商 / 无关 / 负样本）分层抽样 `source_link`，
每条预填 `ai_<field>` 只读列，并留空 `<field>` 列供人工修正。

数据库在**主仓库**（不在本 worktree），运行时用绝对路径覆盖默认值：

```bash
uv --project s01b_project_extractor run python -m eval.build_draft \
  --n 150 \
  --out eval/draft.csv \
  --projects-db  E:/Projects/BRI_RE_Database_v2/s01b_project_extractor/data/projects.db \
  --raw-news-db  E:/Projects/BRI_RE_Database_v2/s01a_news_crawler/data/raw_news.db
```

（`--raw-news-db` 可选，仅用于关联 `article_id` 与抽取负样本桶；缺失则跳过。）

### 2. 人工标注 `draft.csv`

用 Excel 打开（CSV 为 **utf-8-sig**，中文不乱码）。对每一行：

1. **打开 `source_link` 指向的原文，从头读一遍。** 这是关键——`ai_<field>` 只是参考，AI 经常出错，
   不读原文就直接抄 AI 值会让这把尺子失去意义。
2. 在每个 `<field>` 列填入**正确值**：
   - `status` — `valid` / `no_chinese` / `irrelevant` 三选一。
   - `category` / `project_stage` / `financing_model` / `involvement_type` — 枚举值（规范中文，见 `enums_mapping.json`）。
   - `capacity_mw` / `total_investment_usd` — 数值（带原文单位也可，打分时按数值解析）。
   - `location` — 省/州。
   - `entities` — 中方实体名，多个用**分号**分隔（如 `中国电建; 三峡`）。

### 3. 转成金标准 `gold_set.jsonl`

把修正后的 CSV 转为每行一条记录的 jsonl（P0 暂未内置 CSV→jsonl 转换脚本，可手工或后续小脚本生成）。
期望形状（一行一条）：

```json
{"source_link": "http://...", "labels": {"status": "valid", "category": "光伏", "capacity_mw": "50", "project_stage": "中标", "location": "...", "total_investment_usd": "...", "financing_model": "...", "involvement_type": "EPC总承包", "entities": ["中国电建", "三峡"]}}
{"source_link": "http://...", "labels": {"status": "irrelevant", "entities": []}}
```

规则：`status ∈ {valid, no_chinese, irrelevant}`；`entities` 为字符串数组；其余字段可空（`null` 或省略）。
每行经 `gold_loader.py` 的 Pydantic 校验，任一行非法（如 status 不合法）会**带行号立即报错**。

### 4. 评测 `run_eval.py → eval/reports/<ts>.json`

对金标准里每篇文章跑当前提取器（结果按 `url + prompt 哈希` 缓存，避免重复付费调用 Gemini），
逐字段打分，写报告 json，并打印各指标相对**上一次报告**的 ↑/↓/= 对比。

```bash
uv --project s01b_project_extractor run python -m eval.run_eval \
  --gold eval/gold_set.jsonl
```

- **真实运行**需设置 `GEMINI_API_KEY`（放在 `.env`）。
- 框架也可用**注入的 mock 提取器**测试，完全不碰 Gemini —— 见 `tests/test_run_eval_e2e.py`：
  对 `run_eval()` 同时传 `extract_fn=` 与 `extractor_version=` 即走纯本地路径（genai 为延迟导入）。
- `--no-cache` 强制重新提取；`--reports-dir` / `--cache-dir` 可覆盖默认目录。

---

## 打分规则（`metrics.py`）

- **status 是闸门**：status 预测错的记录，其字段**不计入字段分**（单独在 `status_accuracy` 体现）；
  且仅当 gold `status == valid` 时才有字段分（`irrelevant` / `no_chinese` 只比 status）。
- **枚举字段**（category / project_stage / financing_model / involvement_type）：归一化后相等判定。
- **数值字段**（capacity_mw / total_investment_usd / storage_power_mw / storage_energy_mwh）：相对误差 ±5% 内算对。
- **实体**：预测集合 vs 金标准集合的 precision / recall / F1（报告取 F1 均值）。
- **weighted_total**：status / enum / numeric / entity 四分量加权，默认等权（各 0.25）；
  无数据的分量丢弃并对剩余权重重新归一化。权重可在 `run_eval.py` 的 `WEIGHTS` 调整。

报告 json 关键字段：`timestamp, extractor_version, n_gold, n_scored, n_failed, n_skipped,
status_accuracy, fields{field:{correct,total,accuracy}}, entity_f1_mean, weighted_total`。

---

## 数据位置与 git

- 数据库在**主仓库**（非本 worktree）：
  - 原文：`s01a_news_crawler/data/raw_news.db`
  - 提取结果：`s01b_project_extractor/data/projects.db`
- 以下均为**本地产物，已 gitignore**，不入库：
  `eval/draft*.csv`、`eval/gold_set.jsonl`、`eval/cache/`、`eval/reports/`、`eval/*.log`。
