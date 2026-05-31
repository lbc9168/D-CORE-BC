# review — P2 复核机制（E.1 低置信二次核验 + E.2 chinese_unnamed）

> 最后更新：2026-06-01（T10 实跑：gold 复查 + eval 重跑 + 全量 dry-run + CSV 导出；新增 --concurrency 并行、value_validator 枚值校验、export_queue）

**模块路径：** `review/`（仓库根）

---

## 定位

**P2 复核机制**包含两条并行流：

- **E.1 低置信二次核验**：对已写库的 `extracted_projects` 中存在可疑字段的记录，启动独立 `review/` 模块走批判-分流流水线，自动修正高置信字段值或将低置信候选入复核清单，**永不自动改 status**。
- **E.2 chinese_unnamed 未具名中企流**：S1 判 `chinese_unnamed_participation` → orchestrator 标准化为 `chinese_unnamed`，跳过 S2-S5 直接走写库分支，注入占位实体 `未具名中企`（常量 `bri_enums.UNNAMED_ENTITY_PLACEHOLDER`，列 `is_unnamed_placeholder=1`），同时向 `review_queue` 写入 `reason_type=unnamed_entity` 条目，待人工补录公司名。

设计 / 计划：
- spec：`docs/superpowers/specs/2026-05-31-v2.1-P2-review-design.md`
- plan：`docs/superpowers/plans/2026-05-31-v2.1-P2-review.md`

---

## review/ 模块组成

| 文件 | 作用 |
| --- | --- |
| `signals.py` | 确定性预筛：枚举 `suspicious_fields(proj)`，对 `implementing_entity=Unknown / 该类目应有容量却为空 / involvement_type=Unknown` 三类字段打标；占位实体（`is_unnamed_placeholder=1`）不计入可疑 |
| `critic.py` | 批判 agent：`review_project(article, project, suspicious, llm)` → `list[Verdict]`；Pydantic `Verdict` 校验（verdict/field/confidence/suggested_value/reasoning），默认 LLM = DeepSeek |
| `apply.py` | 分流：高置信（≥threshold）+ 字段在白名单 `_UPDATABLE` + **建议值过 `value_validator` 合法性校验** → 调 `update_field` 改库；status 挑战 + 低置信 + 非法建议值 → 入 `review_queue`；**status 永不自动改** |
| `queue.py` | `review_queue` 模块：`init_queue()`（幂等建表）/ `add_item()`（入口校验 reason_type）/ `pending()`（返回所有 pending 条目） |
| `run_review.py` | CLI 编排：`signals → get_article → critic → apply` 流水线；**`--concurrency N` 并行批判（仅读+LLM 并发，库写串行）**；`--dry-run`（只记录不写库）；`_default_value_validator`（category 转人工 / involvement 须合法枚举 / capacity 须可解析正数 / project_name 非空）；`review/reports/<ts>.json` 落报告；改库前自动 `.bak`；兼容旧库缺 `is_unnamed_placeholder` 列；`main()` 载入 `.env` |
| `export_queue.py` | 把 `review_queue` pending 项导出为 CSV（join `projects.db` 补项目名/类别/原值，utf-8-sig 带 BOM 供 Excel）；只读不改库；`status_challenge` 排前 |
| `__init__.py` | 包骨架（空，统一入口） |

---

## chinese_unnamed 流（E.2）

1. **S1 判断**：`stages/s1_relevance.py` 新增 `_VALID_STATUS` 第 4 值（RAW 值）`"chinese_unnamed_participation"`；S1 prompt 增加新分支——文章提到有中企参与但未具名时输出该 RAW 值。`orchestrator._S1_STATUS_MAP` 将其标准化为 `"chinese_unnamed"`；eval（gold_loader/predict）与写库分支比较的是标准化后的 `"chinese_unnamed"`。
2. **orchestrator 标准化**：`_S1_STATUS_MAP`（`stages/orchestrator.py`）将 `"chinese_unnamed_participation"` 映射为 `"chinese_unnamed"`，进入写库分支（跳过 S2-S5）。
3. **写库**：`project_extractor_tool.py` 新增 `elif status == "chinese_unnamed":` 分支——写入占位实体 `enums.UNNAMED_ENTITY_PLACEHOLDER`（= `"未具名中企"`），`is_unnamed_placeholder=1`，`involvement_type=Unknown`；向 `review_queue` 写入 `reason_type="unnamed_entity"` 条目（`evidence="S1 判 chinese_unnamed，待补公司名"`）。
4. **下游保护**：`s02a_duplicate_merger` `_entity_overlap` 排除占位实体，防止未具名占位行触发误合并。

---

## review_queue 表结构

建在 `review.db`（与 `projects.db` 同目录，CLI 通过 `--review-db` 指定）。

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | INTEGER PK | 自增 |
| `canonical_id` | TEXT | 可选；关联 `extracted_projects.id`（或 `canonical_projects.canonical_id`） |
| `source_link` | TEXT | 原文 URL |
| `reason_type` | TEXT | `unnamed_entity` \| `low_confidence_field` \| `status_challenge` |
| `field` | TEXT | 可疑字段名（unnamed_entity 时为 NULL） |
| `current_value` | TEXT | 库中当前值 |
| `suggested_value` | TEXT | critic 建议值 |
| `critic_confidence` | REAL | critic 置信度（unnamed_entity 时为 NULL） |
| `evidence` | TEXT | 批判理由摘要 |
| `state` | TEXT | `pending`（默认）\| `resolved` |
| `created_at` | TEXT | ISO 时间戳 |

---

## 运行命令

```bash
# 全量 dry-run（并发 100；批判用 deepseek-v4-pro）——只读不改库
uv --project s01b_project_extractor run python -m review.run_review \
  --projects-db <path/to/projects.db> \
  --raw-news-db <path/to/raw_news.db> \
  --review-db   <path/to/review.db> \
  --dry-run --llm-model deepseek-v4-pro --concurrency 100

# 把 pending 复核清单导成 CSV（join 项目名/原值，Excel 友好）
uv --project s01b_project_extractor run python -m review.export_queue \
  --review-db <path/to/review.db> \
  --projects-db <path/to/projects.db> \
  --out review/review_queue.csv
```

- 默认批判 LLM = DeepSeek（需 `DEEPSEEK_API_KEY` / `DS_API_KEY`，`main()` 自动载 `.env`）；`--llm-model deepseek-v4-pro` 用 Pro 做批判。
- **并发**：`--concurrency` 仅并行"取原文+批判"（库写仍在主线程串行，线程安全已审查）。实测真实库约 75–80 有效并发、~5–6 调用/秒；**并发设到 300 会在沙箱里因线程过多被静默杀掉，用 ≤100**。
- report 落 `review/reports/<ts>.json`（已 gitignore）。改库前自动 `.bak`（dot/underscore 两式均 gitignore）。CSV / `*_review.db` 等本地产物 gitignore。

---

## status 第 4 值同步说明（重要事实记录）

`guardrails/format_checks.py` 与 `semantic_checks.py` 只校验记录级枚举字段（category/stage/involvement/financing），**不校验文章级 status**。因此 status 第 4 值的"全链同步"实际落在四处：

1. `stages/s1_relevance.py` — `_VALID_STATUS`（S1 输出合法值集合，持有 RAW 值 `"chinese_unnamed_participation"`）
2. `stages/orchestrator.py` — `_VALID_S1_STATUS`（orchestrator 门控合法值，持有 RAW 值 `"chinese_unnamed_participation"`）+ `_S1_STATUS_MAP`（标准化 `"chinese_unnamed_participation"` → `"chinese_unnamed"`）
3. `eval/gold_loader.py` — `Literal[...]` 类型注解（金标准 schema 校验）
4. `eval/predict.py` — `_STATUS_MAP`（提取器结果 → 评测用状态映射）

原 plan/spec 中提到的 `format_checks`/`semantic_checks` 两处**无需改动**（其不涉及 status 字段）。

---

## 当前状态

**代码 + 测试：✅ 全部完成（2026-05-31）**

- `review/` 模块 6 组件全部落地（signals / critic / apply / queue / run_review / `__init__`）。
- E.2 写库链路（`project_extractor_tool.py` `chinese_unnamed` 分支 + `is_unnamed_placeholder` 列 + `review_queue.add_item`）已验证绿。
- 全部单测 + e2e 绿（含 `test_review_signals.py` / `test_review_critic.py` / `test_review_apply.py` / `test_review_queue.py` / `test_run_review_e2e.py` / `test_bri_enums_placeholder.py` / `test_s02a_exclude_placeholder.py` / `test_write_unnamed.py`）。
- `s02a_duplicate_merger` `_entity_overlap` 已排除占位实体（防误并保护）。
- 白名单字段硬编码（`_UPDATABLE`）+ 二次防御（`update_field` 内再次校验）已实施。

**T10 实跑记录（2026-06-01）**

1. **gold 复查**：用户复查 97 篇，确认本批无 chinese_unnamed 类型样本；改标 1 篇（article_id 16256）valid→irrelevant，重生成 `gold_set.jsonl`（valid 60 / irrelevant 28 / no_chinese 9）。
2. **重跑 eval**（P1+P2 代码，deepseek 混合）：`weighted_total 0.7379→0.7613`，project_set/fields/entity 全面↑；`status_accuracy 0.7917→0.75`——经混淆矩阵核查，**与 chinese_unnamed 无关**（96 篇仅 1 篇被预测为 chinese_unnamed），下降来自既有 S1 闸门过度收录（irrelevant→valid 13/→no_chinese 6）+ 16256 改标 + LLM 非确定性。**P2 无回归、净提升**。
3. **全量 dry-run**（3351 候选，deepseek-v4-pro，并发 100，~20 分钟，未改库）：自动改 156（capacity_mw 40 / project_name 99 / involvement_type 17；category 0 已转人工）、`status_challenge 1586` + `low_confidence_field 4112`、critic_failed 189（~5.6% Pro 输出不合 schema）。status_challenge 关键词分布：非可再生(化石/油气) 405 / 无或未证实中企 568 / 建议 irrelevant 126 / 其它 487——抽样几乎条条命中真实过度收录，价值高。
4. **导出 CSV**：`review/export_queue` 导出 5698 条 pending 供人工三角（已交付）。
5. **决策**：用户选"导出 CSV、暂不自动改库"。**自动改写未真跑**（见下方待硬化）。

---

## 已知后续（v2.1 follow-up，非本期）

- **自动改写真跑前必须硬化（dry-run 抓出，未修）**：
  - **capacity_mw 写的是 critic 原始建议串而非解析后的数值**——`value_validator` 只确认"能解析出数字"就放行，但 `update_field` 写的是 `suggested_value` 原文，会把 `'1000-1200'`/`'近90'`/`'10000 MWh (年产能)'` 等字符串塞进 REAL 容量列（违反"数值字段一律 REAL"）。修法：capacity 写 `bri_parse.parse_number(suggested)` 的结果。
  - **project_name 偶发"张冠李戴"**（如 id4106 巴西 1.31GW 风电被 critic 串成"墨西哥 319MW Cimarron"）——99 条多为无害清理，但跨项目改名有害，建议 project_name 也转人工。
  - 在以上两点修好 + 重跑 dry-run 验证前，**只用人工清单（CSV），不开自动改库**。
- `s02a_duplicate_merger` `_fmt_entities` / `_merge_records` 实体去重未排除占位实体——因占位已排除出 `_entity_overlap` 触发信号、影响有限，留待后续小迭代。
