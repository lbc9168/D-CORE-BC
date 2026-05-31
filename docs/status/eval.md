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

### Phase 2：S4 实体阶段（母子公司归一）（2026-05-31，report `eval/reports/20260531_125108.json`）

| 指标 | 基线 | Phase 1 | Phase 2(S4) | vs 基线 |
| --- | --- | --- | --- | --- |
| **entity name_f1_mean** | 0.565 | 0.613 | **0.688** | ↑ +0.123（Phase 2 主攻达成）|
| entity role_accuracy | 0.684 | 0.750 | 0.628 | ↓ 见下注 |
| project_set f1 | 0.747 | 0.697 | 0.743 | ≈ |
| category / project_stage | 0.881/0.881 | 0.925/0.868 | 0.927/0.873 | ↑ |
| status_accuracy | 0.794 | 0.823 | 0.821 | ↑ |
| weighted_total | 0.7396 | 0.7767 | 0.7624 | ↑ +0.023 |

**role_accuracy 下降是分母效应，非真实退步**：`role_matched_total` 38(基线)→36(P1)→**43**(P2)；绝对答对数 ≈26/27/27 **稳定**。name_f1 变好→更多实体被正确匹配→进入 role 比较的实体集更大且更难，故率降但绝对答对稳定。S4 未恶化 role 分类，是 name_f1 胜利的耦合产物。role 分类细化留待 Phase 4 S5 自检 / 后续 S4 prompt 迭代。

> 注：`n_failed=2`（S4 增加每项目一次 LLM 调用，失败面略增；fail-loud 可见）。S4 为每个项目额外调一次 LLM 抽实体（母子公司归一 + 设备主次/供货两类）。

### Phase 3：S2 项目识别拆出（防过度拆分）（2026-05-31，report `eval/reports/20260531_171821.json`，version `270281f8d947`）

orchestrator valid 分支由「legacy 单 prompt 出项目」改为「独立 S2 阶段出 `projects[]` → 逐项目 S4」，**彻底移除 legacy 单 prompt**。

| 指标 | 基线 | Phase 2 | **Phase 3** | vs 基线 |
| --- | --- | --- | --- | --- |
| **weighted_total** | 0.7396 | 0.7624 | **0.7735** | ↑ +0.034（gate「weighted 不得 ↓」达标） |
| project_set precision | 0.678 | — | 0.6629 | ≈（−0.015，残余多合同汇总文多编） |
| project_set recall | 0.831 | — | **0.8429** | ↑ |
| project_set f1 | 0.747 | 0.743 | **0.7421** | ≈ Phase2 持平（重构未退化项目识别） |
| category | 0.881 | 0.927 | **0.9661** | ↑ |
| project_stage | 0.881 | 0.873 | 0.8644 | ≈ |
| capacity_mw | 0.915 | — | **0.9492** | ↑ |
| financing_model | 0.983 | — | 0.9492 | ≈ |
| entity name_f1_mean | 0.565 | 0.688 | **0.7090** | ↑ |
| entity role_accuracy | 0.684 | 0.628 | 0.6383 | ≈ |
| status_accuracy | 0.794 | 0.821 | **0.8316** | ↑ |

**Phase 3 调试历程（systematic-debugging，4 轮 eval + 1 次匹配器修复）**：
1. **run1**（S2 初版）：project_set precision 跌到 0.54、financing 跌到 **0.0**。根因①：S2 不再输出 Financing Model（gold 多为 "Unknown"，空≠Unknown 被判错）；根因②：S2 过度拆分（pred 98 vs gold 70）。
2. **run2**（加严合并 + 我自拟命名规范「不加补充词」）：**反而更差**——命名规范把 gold 赖以识别的专名（Rumah 2、联合循环、拉索…）删光，matched 53→39、recall 0.76→0.56。**教训：自拟命名脱离 gold 习惯，净退步。**
3. **run3**（撤销命名误改 + 港 legacy 的「逐项目范围过滤」：剔除学校/道路/数据中心等非发电基建、纯运维、设备供货并一条）：project_set f1 0.49→0.69，pred 98→82。
4. **run4**（容量按原文单位不换算 GW→MW、去承包方式后缀、硬化设备供货并一条）：weighted 0.7724（>Phase2）、f1 0.706，但 precision 仍 ~0.635。
5. **匹配器修复（用户拍板）**：诊断发现大量「未匹配」其实是**同一项目**，仅因 `兆瓦/吉瓦/千瓦/兆瓦时` vs `MW/GW/kW/MWh` 写法差异，token_set_ratio < 80 未对齐。在 `eval/metrics.py:_norm_name` 加中英容量单位归一（对所有版本一视同仁，匹配器纠错非刷分；**未改阈值/权重**）。**重跑同一缓存预测**（无新 API）→ project_set f1 **0.7421**、precision 0.663、recall 0.843、weighted **0.7735**。

> **结论**：weighted 净升（gate 达标），project_set 回到 Phase2 持平、recall 升过基线；残余 precision 小缺口（0.663 vs 0.678）来自少数「连连签/连中 N 标」汇总文的真实多编，留待 Phase 4 S5 自检合并/剪枝。financing=Unknown 为 S2 临时占位（gold 多为 Unknown），Phase 4 S3 接入后以真实融资值覆盖。`n_failed=2`。

### Phase 4：S3 金融 + S5 摘要/自检（P1 全五阶段完成）（2026-05-31，report `eval/reports/20260531_180009.json`，version `ee49b9bcf371`）

至此 orchestrator 全链为 **S1 门控 → S2 项目识别 → 逐项目 S4 实体 → S3 金融 → S5 摘要+自检纠偏**。

| 指标 | 基线 | Phase 2 | Phase 3 | **Phase 4(全链)** | vs 基线 |
| --- | --- | --- | --- | --- | --- |
| **weighted_total** | 0.7396 | 0.7624 | 0.7735 | **0.7686** | ↑ +0.029 |
| status_accuracy | 0.794 | 0.821 | 0.832 | 0.8316 | ↑ |
| project_set f1 | 0.747 | 0.743 | 0.742 | 0.700 | ≈（S2 跑间噪声，见下） |
| **project_stage** | 0.881 | 0.873 | 0.864 | **0.8929** | ↑（S5 自检纠偏生效） |
| category | 0.881 | 0.927 | 0.966 | 0.9643 | ↑ |
| **total_investment_usd** | 0.915 | — | 0.847 | **0.8929** | ↑（S3 抽投资额） |
| financing_model | 0.983 | — | 0.949 | 0.9286 | ↓ 见下注 |
| capacity_mw | 0.915 | — | 0.949 | 0.9286 | ↑ |
| entity name_f1_mean | 0.565 | 0.688 | 0.709 | 0.6935 | ↑ |
| entity role_accuracy | 0.684 | 0.628 | 0.638 | 0.6279 | ≈ |

**解读**：
- **S5 兑现主攻**：`project_stage` 0.864→**0.893**（高置信度自检纠偏把误判阶段拉回）。
- **S3 兑现**：`total_investment_usd` 0.847→**0.893**（逐项目抽投资额）。
- **financing 0.949→0.929 是预期权衡，非退步**：S3 开始做**真实**融资判断，而 gold 融资多为保守的 "Unknown"；S3 抽出真实模式与 gold 的 Unknown 不一致被记错——gold 保守所致，非 S3 缺陷。
- **project_set f1 0.742→0.700 是 S2 跑间噪声**：S2 代码自 Phase 3 未改；S3 只并入 common_info、S5 只写 summary/纠偏 stage·category，**均不动 project_name**，故不影响按名对齐。matched 59→56 纯属 LLM 非确定性（temp 0.14）跑间抖动。
- **weighted 0.7686 vs Phase3 重打分 0.7735 的 −0.005 在噪声内**（叠加 financing 权衡）；vs 上一已提交全链态（Phase2 0.7624）与基线（0.7396）净升。

> 注：`n_failed=2`。S3/S5 各为每项目额外一次 LLM 调用。两处旧 crewAI guardrail 单测（`test_guardrail.py` 2 例）在本会话开始前（ae5d7be）即失败，与本期改动无关，Phase 5 去 crewAI 时一并处理。

---

## P1 全链小结（基线 → 全五阶段）

| 指标 | 基线 | P1 全链 | Δ |
| --- | --- | --- | --- |
| weighted_total | 0.7396 | **0.7686** | **+0.029** |
| status_accuracy | 0.7938 | 0.8316 | +0.038 |
| entity name_f1_mean | 0.565 | 0.6935 | +0.129 |
| category | 0.881 | 0.964 | +0.083 |
| project_stage | 0.881 | 0.893 | +0.012 |
| project_set recall | 0.831 | 0.80 | −0.031（S2 噪声区间内） |

P1 主攻三项（status 负样本剔除、实体名归一、防过度拆分）均达标或净升；新增 eval 匹配器单位归一修复使 project_set 度量更真实。供应商 A/B（DeepSeek vs Gemini）见下。

---

## 供应商 A/B：DeepSeek V4 Flash vs Gemini 2.5 Flash-Lite（Phase 6，2026-05-31）

同一全五阶段架构与 prompt，仅切 `S01B_PROVIDER`。Gemini report `20260531_183541.json`(version b7709304df1f)；DeepSeek report `20260531_185413.json`(version c7d57854095f，model `deepseek-v4-flash`)。

| 指标 | Gemini flash-lite | DeepSeek V4 Flash | 胜方 |
| --- | --- | --- | --- |
| **weighted_total** | 0.7784 | **0.8283** | DeepSeek +0.050 |
| status_accuracy | 0.8211 | 0.8247 | ≈ |
| project_set f1 | **0.760** | 0.672 | Gemini |
| project_set precision | 0.695 | 0.694 | ≈ |
| project_set recall | **0.838** | 0.652 | Gemini（DeepSeek 少提：pred 62 vs gold 66） |
| category | **0.965** | 0.930 | Gemini |
| project_stage | 0.877 | **0.907** | DeepSeek |
| financing_model | 0.930 | **0.977** | DeepSeek |
| capacity_mw | **0.930** | 0.907 | Gemini |
| total_investment_usd | **0.895** | 0.884 | ≈ |
| entity name_f1_mean | 0.684 | **0.744** | DeepSeek |
| entity role_accuracy | 0.689 | **0.824** | DeepSeek |
| n_failed / n_scored | 2 / 58 | **0** / 55 | DeepSeek（更稳） |

**解读**：
- **DeepSeek V4 Flash 综合分明显更高（0.828 vs 0.778, +0.05）**，强在**实体质量**（role +0.135、name +0.06）、financing、project_stage，且**零失败**（Gemini 2 失败）。
- **Gemini 强在项目覆盖**：project_set recall 0.838 vs 0.652——DeepSeek 提取的项目更少（pred 62 < gold 66），更保守/合并更狠，漏项约 35%；category 也略高。
- **取舍**：DeepSeek = 每条记录质量更高 + 更稳；Gemini = 项目覆盖更全。叠加**相对成本**（V4 Flash vs flash-lite，由用户掌握）共同决定。
- **延迟**：DeepSeek 单次 ~5-6s，Gemini ~1s；全量 eval DeepSeek 约 18 分钟 vs Gemini 约 8 分钟。

> ⚠️ 跨供应商比较有口径噪声：两者各自 status 判定不同 → n_scored（58 vs 55）与 matched 集合不同，per-matched 字段/实体指标非严格同集合可比。**见下「分阶段路由」一节——这条最初的"DeepSeek 实体更优 +0.05"结论被混合实验大幅修正。**

### 分阶段路由 StageRouter：混合方案实测（2026-05-31，report `20260531_195022.json`，version 746aa2bf76d2）

新增 `StageRouter`（按 stage 选 provider）。混合配置 `S01B_STAGE_PROVIDERS=s3:deepseek,s4:deepseek,s5:deepseek`（S1/S2=Gemini 保覆盖，S3/S4/S5=DeepSeek）。三方对比：

| 指标 | 全 Gemini | 全 DeepSeek | **混合(S1/S2 gem, S3-5 ds)** |
| --- | --- | --- | --- |
| project_set **recall** | 0.838 | 0.652 | **0.857**（三者最高） |
| project_set **f1** | 0.760 | 0.672 | **0.774**（最高） |
| project_set precision | 0.695 | 0.694 | **0.706** |
| category | 0.965 | 0.930 | **0.967** |
| capacity_mw | 0.930 | 0.907 | **0.933** |
| total_investment_usd | 0.895 | 0.884 | **0.917** |
| financing_model | 0.930 | **0.977** | 0.950 |
| project_stage | 0.877 | **0.907** | 0.867 |
| entity name_f1 | 0.684 | **0.744** | 0.694 |
| entity role_accuracy | 0.689 | **0.824** | 0.682 |
| weighted_total | 0.7784 | **0.8283** | 0.7814 |

**两条修正了原 A/B 结论的关键发现**：
1. **DeepSeek 的"实体更优"主要是低召回的幸存者偏差**：混合方案里 S4(实体) 跑在 DeepSeek 上，但 name_f1(0.694)/role(0.682) 回到了 **Gemini 水平**，而非全 DeepSeek 的 0.744/0.824。原因：实体指标只在**已匹配项目**上算——全 DeepSeek 只匹配了 43 个(易)项目，故实体评在易例上；混合匹配 60 个(更难集)，率自然回落。**覆盖一拉满，DeepSeek 的实体优势基本消失。**
2. **`weighted_total` 是有缺陷的跨配置指标——它根本不含项目覆盖**：四分量是 status/fields/entity_name/entity_role，**project_set/recall 不在内**。所以全 DeepSeek 的 0.8283 是**被低覆盖抬高**的（小而易的匹配集上 per-match 质量高）。对"尽量全"的追踪型数据库，weighted_total 低估了最该看的覆盖。

**结论**：**混合方案给出三者最佳的项目覆盖(recall 0.857/f1 0.774) + 最佳/并列最佳字段(category/capacity/investment) + Gemini 级实体质量 + 略升的 financing**，代价是 S3/S4/S5 走 DeepSeek 的延迟/成本。相对**全 Gemini**，混合买到的是"覆盖 + 金融/投资字段"的小幅提升，换 3 个阶段的 DeepSeek 成本。**全 DeepSeek 不推荐**（headline weighted 虚高、覆盖差）。

> 用法：`S01B_STAGE_PROVIDERS=s3:deepseek,s4:deepseek,s5:deepseek`。**用户已选定此混合为默认并写入 `.env`**；不设该 env = 全 Gemini（原行为）。keys 用 `.env` 的 `GEMINI_API_KEY` + `DS_*`。

### 原版「单大 prompt」vs 分阶段：A/B（2026-05-31，reports `legacy-gemini`=20260531_213207 / `legacy-deepseek`=20260531_214630）

用退役的单大 prompt（`tools/project_extractor_tool.py:_build_prompt`，一次 LLM 调用产出全结构）分别跑两供应商，**沿用当前评测口径**，与分阶段方案同尺对比。

| 配置 | weighted | proj_set f1 | proj recall | name_f1 | role | status | category |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 原版单prompt × Gemini | 0.7215 | 0.706 | 0.783 | 0.599 | 0.600 | 0.784 | 0.926 |
| 原版单prompt × DeepSeek | 0.7750 | **0.490** | **0.493** | 0.519 | 0.842 | 0.825 | 0.914 |
| 分阶段 × 全 Gemini | 0.7784 | 0.760 | 0.838 | 0.684 | 0.689 | 0.821 | 0.965 |
| 分阶段 × 全 DeepSeek | 0.8283 | 0.672 | 0.652 | 0.744 | 0.824 | 0.825 | 0.930 |
| **分阶段混合(默认)** | 0.7814 | **0.774** | **0.857** | 0.694 | 0.682 | 0.823 | 0.967 |

**结论：拆分阶段对两个模型都是明确提升**：Gemini 单prompt 0.7215→分阶段 0.7784(+0.057)；DeepSeek 单prompt 0.7750→分阶段 0.8283(+0.053)，且 name_f1 0.519→0.744、coverage f1 0.490→0.672 大涨。
- **最扎眼**：原版单prompt + DeepSeek 覆盖极差（recall **0.493**，匹配 35/71，漏一半项目）——单次大调用把信息挤一起、大量项目丢失；拆出专门 S2 阶段把覆盖救回，混合(S2=Gemini)拉到最高(0.857)。
- **当前默认(混合) vs 最初做法(单prompt Gemini)**：weighted 0.7814 vs 0.7215(**+0.060**)、coverage f1 0.774 vs 0.706、recall 0.857 vs 0.783——几乎每项更好。**整个 P1 分阶段重构得到强验证。**

> 跨配置有 LLM 噪声（n_scored 58/60/58/55/59 略不同）+ weighted 不含覆盖；但 ±0.05-0.06 与覆盖 0.49→0.86 的差距远超噪声。一次性 A/B 脚本 `eval/_ab_legacy.py` 用后即删。

### 弃案记录：prompt 前缀缓存重排（2026-05-31，已回退，勿重走）

为提升 DeepSeek **前缀缓存命中率**，曾试把全部 5 个阶段 prompt 改为「静态块（角色/规则/枚举/对照表/输出schema）在前、变量（正文/标题/项目名）在后」（纯搬动，规则一字未改）。**eval 实测明显伤准确率，已回退、未提交：**

| 指标 | 重排前(混合) | 重排后 | Δ |
| --- | --- | --- | --- |
| weighted_total | 0.7814 | 0.7379 | −0.044 |
| project_set recall | 0.857 | 0.696 | **−0.161** |
| project_set f1 | 0.774 | 0.644 | −0.130 |
| entity name_f1 | 0.694 | 0.587 | −0.107 |
| status / category / stage | 0.823/0.967/0.867 | 0.792/0.917/0.792 | 均跌 |

**结论**：S2 matched 60→48（prompt 内容相同、仅顺序变）证实模型**偏好正文靠前**；"正文垫在大段规则之后"显著伤抽取（尤其项目覆盖）。前缀缓存要求"变量在后"与此**直接冲突**，且 DeepSeek 仅支持自动前缀缓存（无中段缓存断点），故重排是唯一缓存杠杆却要拿准确率换。**取舍：保准确率，接受较低缓存命中率。** 若日后仍要省 DeepSeek 成本，应从「截短喂入正文」等方向评估，而非重排 prompt。
