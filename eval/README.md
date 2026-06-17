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

**一文一行 → 一项目一行（P0.1）**：一篇文章如含多个项目，会拆成**多行**，
同一 `source_link` 在这些行里**重复**出现（`status` 列也逐行重复）。新增列 `ai_project_name` / `project_name`
（项目名仅用于对齐，不参与打分）。`no_chinese` / `irrelevant` 文章及负样本桶为**单行**，项目相关列留空。

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

用 Excel 打开（CSV 为 **utf-8-sig**，中文不乱码）。

**「只改错的」约定（推荐，省力）**——你不是给 AI 打分，而是记录每个字段的**正确值**；框架自己比对算分：

1. **打开 `source_link` 指向的原文，从头读一遍。** 这是关键——`ai_<field>` 只是参考，AI 经常出错，
   不读原文就直接放过 AI 值会让这把尺子失去意义。
2. 逐字段判断 `ai_<field>` 对不对：
   - **AI 对** → 对应的 `<field>` 修正列**留空**（转换时自动沿用 `ai_<field>`）。
   - **AI 错** → 在 `<field>` 修正列写**正确值**（覆盖 `ai_<field>`）。
   - **真实值确为空/无**（AI 多填了） → 在 `<field>` 写空标记 `-`（转换为 null / 空实体集）。
3. 字段格式：
   - `status` — `valid` / `no_chinese` / `irrelevant` 三选一，**文章级**（同一文章每行写同一个 status）。**负样本桶（`_bucket=negative:no_extraction`）的行 `ai_status` 为空，必须人工填 status**，否则转换时整篇跳过。
   - `project_name` — 项目名，仅用于**对齐**预测/金标准的同一项目（rapidfuzz 模糊匹配），**不计入字段分**。一文多项目时每行一个。
   - `category` / `project_stage` / `financing_model` — 枚举值（规范中文，见 `enums_mapping.json`）。
   - `capacity_mw` / `total_investment_usd` — 数值（单位分别为 MW、百万美元；带原文单位也可，打分时按数值解析）。
   - `location` — 省/州（**记录但不打分**）。
   - `entities` — 该**项目**的中方实体，格式 `名字(角色); 名字(角色)`——同一项目多家中企用**分号**分隔，每家在括号里写角色（involvement_type）。如 `中国电建(EPC总承包); 三峡(投资方)`。
     - **括号解析规则（重要）**：角色取**最后一个**括号组，所以**公司名本身可以含括号**。
       例：`中国大唐环境(中国)有限公司(EPC总承包)` 解析为 name=`中国大唐环境(中国)有限公司`、role=`EPC总承包`。
     - **边界**：若某实体本身名字以 `)` 结尾**又没有写角色**，会被误读成「名字+角色」。极少见，但标注时请确保
       名字以右括号结尾的实体**务必带上角色**（写成 `...公司)(EPC总承包)` 之类），避免歧义。

### 3. 转成金标准 `gold_set.jsonl`（`csv_to_gold.py`）

标注完直接跑转换脚本（按上面「只改错的」约定：空=沿用 ai 值，填了=覆盖，空标记 `-`=置空）：

```bash
uv --project s01b_project_extractor run python -m eval.csv_to_gold \
  --csv eval/draft.csv --out eval/gold_set.jsonl
```

脚本会**按 `source_link` 把多行聚合成一篇文章**（每行→一个 project），把 `entities` 的 `名字(角色)` 串解析成
`[{name, involvement_type}]`，跳过未标注 status 的文章（带告警），并在写完后立即用 `gold_loader.py` 的 Pydantic
校验产物，任一行非法（如 status 不合法、实体缺名字）会**带行号立即报错**。产物形状（一行一**篇文章**，内嵌 projects[]）：

```json
{"source_link": "http://...", "status": "valid", "projects": [
  {"project_name": "阿尔法光伏电站", "category": "光伏", "capacity_mw": "50", "project_stage": "中标", "financing_model": "Unknown", "total_investment_usd": null, "location": "...", "entities": [{"name": "中国电建", "involvement_type": "EPC总承包"}]},
  {"project_name": "贝塔光伏电站", "category": "光伏", "capacity_mw": "50", "project_stage": "中标", "financing_model": "Unknown", "total_investment_usd": null, "location": null, "entities": [{"name": "中国电建", "involvement_type": "EPC总承包"}]}]}
{"source_link": "http://...", "status": "irrelevant", "projects": []}
```

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

## 打分规则（`metrics.py`，P0.1 多项目）

打分单位是**一篇文章**（`score_article(pred, gold)`），逐层进行：

- **status 闸门（文章级）**：status 预测错的文章，其项目/字段/实体**全部不计分**（只在 `status_accuracy` 体现）；
  且仅当 gold `status == valid` 时才进入后续项目级打分（`irrelevant` / `no_chinese` 只比 status）。
- **项目集合 P/R/F1（`project_set`）**：用项目名做**贪心对齐**（rapidfuzz `token_set_ratio` ≥ 80，平局看 category/容量），
  得到匹配对数 → precision=matched/pred_total、recall=matched/gold_total、f1。这是衡量「枚举/拆分是否正确」（漏项目、
  过度拆分、合并）的**单独指标**，**不进 weighted_total**。
- **每个对上的项目**逐字段打分：
  - **枚举字段**（category / project_stage / financing_model）：归一化后相等。
  - **数值字段**（capacity_mw / total_investment_usd）：相对误差 ±5% 内算对。（储能功率/容量字段 P0 暂不评测。）
  - **location**：**记录但不打分**。
- **实体（每个对上的项目内）**：
  - 中企**名字集合** P/R/F1，报告取所有匹配项目的 **name_f1 均值**（`entity.name_f1_mean`）。
  - **角色准确率**（`entity.role_accuracy`）：仅对两边**同名**的中企比 involvement_type，correct/matched。
- **weighted_total**：四分量**等权**（各 0.25）——`status` / `fields`（各字段准确率均值）/ `entity_name`（name_f1_mean）/
  `entity_role`（role_accuracy）。**注意 project_set 不在其中**（它衡量枚举/拆分，单列即可）。
  无数据的分量丢弃并对剩余权重重新归一化。权重可在 `run_eval.py` 的 `WEIGHTS` 调整。

报告 json 关键字段：`timestamp, extractor_version, n_gold, n_failed, n_skipped, status_accuracy,
n_scored_articles, project_set{matched,pred_total,gold_total,precision,recall,f1},
fields{field:{correct,total,accuracy}}, entity{name_f1_mean,role_accuracy,role_matched_total}, weighted_total`。

---

## 数据位置与 git

- 数据库在**主仓库**（非本 worktree）：
  - 原文：`s01a_news_crawler/data/raw_news.db`
  - 提取结果：`s01b_project_extractor/data/projects.db`
- 以下均为**本地产物，已 gitignore**，不入库：
  `eval/draft*.csv`、`eval/gold_set.jsonl`、`eval/cache/`、`eval/reports/`、`eval/*.log`。
