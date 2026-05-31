# review — P2 复核机制（E.1 低置信二次核验 + E.2 chinese_unnamed）

> 最后更新：2026-06-01（review/ 模块代码 + 测试已落，全部绿）

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
| `apply.py` | 分流：高置信（≥threshold）且字段在白名单 `_UPDATABLE` 内 → 调 `update_field` 改库；status 挑战 + 低置信 → 入 `review_queue`；**status 永不自动改** |
| `queue.py` | `review_queue` 模块：`init_queue()`（幂等建表）/ `add_item()`（入口校验 reason_type）/ `pending()`（返回所有 pending 条目） |
| `run_review.py` | CLI 编排：`signals → get_article → critic → apply` 流水线；支持 `--dry-run`（只记录不写库）；`review/reports/<ts>.json` 落报告；改库前自动备份 `projects_db.bak_YYYYMMDD_HHMMSS`；白名单列事务改库 |
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
# 先 dry-run，查看 report 再决定是否真跑
uv --project s01b_project_extractor run python -m review.run_review \
  --projects-db <path/to/projects.db> \
  --raw-news-db <path/to/raw_news.db> \
  --review-db   <path/to/review.db> \
  --dry-run

# 确认 report 无误后，去掉 --dry-run 真跑
uv --project s01b_project_extractor run python -m review.run_review \
  --projects-db <path/to/projects.db> \
  --raw-news-db <path/to/raw_news.db> \
  --review-db   <path/to/review.db>
```

- 默认批判 LLM = DeepSeek（需 `DEEPSEEK_API_KEY` / `DS_API_KEY`）；可通过 `--llm-provider gemini` 切换。
- report 落 `review/reports/<ts>.json`（已 gitignore）。
- 改库前自动备份为 `projects.db.bak_YYYYMMDD_HHMMSS`（dot + underscore 格式，均已 gitignore）。

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

**待人工步骤（T10，v2.1 内）**

1. 复查 97 篇 gold_set.jsonl，将"有中企但未具名"的条目改标 `chinese_unnamed`，重跑 `run_eval` 出新基线。
2. 对存量 `projects.db` 先 `--dry-run` 查看 report，确认无误后真跑复核。

---

## 已知后续（v2.1 follow-up，非本期）

- `s02a_duplicate_merger` `_fmt_entities`（送 LLM 确认合并时的实体格式化）与 `_merge_records` 实体去重未排除占位实体——目前因占位已排除出 `_entity_overlap` 触发信号、影响有限，留作后续小迭代再评估。
