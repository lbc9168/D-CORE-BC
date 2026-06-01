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

## P2 收口状态（哪些做了 / 哪些没做 / 转到哪）

**核心机制：✅ 全部完成**（E.2 chinese_unnamed 全链 + E.1 review/ 模块 + status 第4值同步 + dry-run 护栏 + concurrency/value_validator/export_queue），代码+测试全绿。

**验收（T10）：部分完成**
- ✅ gold 复查 + eval 重跑：证明**无退化、净提升**（weighted 0.7379→0.7613）。
- ❌ "E.1 **真改库** → 字段/实体准确率↑"：**未做**。全量 dry-run 当场发现自动改写不安全（乱填非法枚举 / capacity 写原始串 / project_name 串项目），故停在 dry-run + 导出 CSV。**此条未丢，已转 P2.1 接管**（spec `docs/superpowers/specs/2026-06-01-v2.1-P2.1-exclude-and-scope-design.md`，根因=缺 exclude 出口 + 三层范围不一致 + critic 不受枚举约束）。

**P2 当初就标"以后做/最小处理"、目前确实未做（已核实）**
1. **review_queue 回写脚本**：现仅有"导出 CSV"（`export_queue`），**没有**"人工裁决后回写库 + 标 resolved"那一半（spec 当初明说本期先只导出）。
2. **s03 对 chinese_unnamed 的"待补名"计数**：`s03_data_exporter` 完全未处理（grep 空）；Task 4 当初标"可选小步、不阻塞"。
3. **s04 前端对 unnamed 的显示/筛选**：`s04` 无任何 unnamed 引用（grep 空）；当初标 YAGNI。

**状态注记（非任务）**：chinese_unnamed 至今**未在真实数据上跑出来过**——E.2 已建好，但现有库是旧提取产物、无 chinese_unnamed 记录；要等**用新 S1 重新录入**才会真正产生占位实体 + review_queue 的 unnamed 条目。

---

## 已知后续（v2.1 follow-up，非本期）

- **自动改写真跑前必须硬化（dry-run 抓出，未修）→ 已纳入 P2.1**：
  - **capacity_mw 写的是 critic 原始建议串而非解析后的数值**——`value_validator` 只确认"能解析出数字"就放行，但 `update_field` 写的是 `suggested_value` 原文，会把 `'1000-1200'`/`'近90'`/`'10000 MWh (年产能)'` 等字符串塞进 REAL 容量列（违反"数值字段一律 REAL"）。修法：capacity 写 `bri_parse.parse_number(suggested)` 的结果。
  - **project_name 偶发"张冠李戴"**（如 id4106 巴西 1.31GW 风电被 critic 串成"墨西哥 319MW Cimarron"）——99 条多为无害清理，但跨项目改名有害，建议 project_name 也转人工。
  - 在以上两点修好 + 重跑 dry-run 验证前，**只用人工清单（CSV），不开自动改库**。
- `s02a_duplicate_merger` `_fmt_entities` / `_merge_records` 实体去重未排除占位实体——因占位已排除出 `_entity_overlap` 触发信号、影响有限，留待后续小迭代。

---

## P2.1 状态（exclude 机制 + 录入范围对齐 + critic 枚举约束）

> 实施：2026-06-01–02，worktree `worktree-v2`，subagent-driven 逐 Task。
> 计划 `docs/superpowers/plans/2026-06-01-v2.1-P2.1-exclude-and-scope.md`，设计 `docs/superpowers/specs/2026-06-01-v2.1-P2.1-exclude-and-scope-design.md`，变更日志 `docs/changes/2026-W22f.md`。

**代码 + 单测：✅ 全部完成（10 个实现 Task，mock LLM 零成本）**

1. **枚举**：火电 `excluded:true→false`（火电属发电项目须录入）。`enums_mapping.json` 单一可信源；现无任何 category 处于 excluded。
2. **录入范围统一为三类**：①发电（任何燃料含火电）②新能源制造业（以产能计的组件厂/电池厂/多晶硅厂）③输变电/电网。S1 prompt、S2 prompt、critic prompt 三层全程删除"可再生能源"窄词。
3. **exclude 是可逆标记，不是删除**：
   - s01b `extracted_projects` 新增列 `excluded INTEGER DEFAULT 0` / `exclude_reason TEXT` / `exclude_source TEXT`（`extraction|review|manual`，迁移幂等）。
   - **提取侧**：S2 每项目输出 `in_scope`/`out_of_scope_reason`；`in_scope=false` → 写 `excluded=1, exclude_source='extraction'`。
   - **review 侧**：`Verdict.verdict` 新增 `exclude`；critic 判非范围/无中企走此通道（不再借 category/involvement 表达）。`apply.route` exclude 分流：`conf>=1.0`（`exclude_auto_threshold`）+ 注入 `exclude_fn` → 自动置 `excluded=1, exclude_source='review'`（事务/记日志/可逆/dry-run 记 would_change）；`conf<1.0` → 入 `review_queue` 新 reason_type `exclude_suggested` 抽检。
   - **传播**：s02a 去重时按 **OR 语义**（任一成员排除即排除）把 `excluded` 传到 `canonical_projects.excluded`（分组/合并/写库三处；前端最终认 canonical.excluded）。
4. **critic 受枚举约束**：合法 category/involvement 清单从 `bri_enums` 动态注入 prompt（照 s5_summary 范本），字段纠正只许从清单选，选不出走 exclude/unsupported。
5. **顺带硬化（P2 dry-run 暴露，已修）**：capacity 自动改写写 `bri_parse.parse_number()` 解析值进 REAL 列、原串存 `capacity_mw_raw`；`project_name` 一律转人工不自动改（`_default_value_validator` 返回 False）。

**验证（单测）**：s01b 159 / 根 tests 144（+ `test_id_generation.py` 4，属 s02a 跨项目测试在 s02a 环境跑）/ s02a 12，全绿。

**测试基建修**：根 `pyproject.toml` 加 `[tool.pytest.ini_options] pythonpath=["."]`、s01b `pyproject.toml` 改 `pythonpath=["src","tests",".."]`——修复 base 起 `tests/` 与 s01b 套件因找不到根模块整体无法 collect 的问题。

**待做（GATED，须经用户同意）**：
- critic 侧小样本 real-LLM dry-run 抽验（~几十条 deepseek-v4-pro，复用既有临时库）：验 exclude 分流正确、队列/would_change 不再有枚举外建议、火电不再被报 status_challenge。**不跑全量**（全量 ~¥40 且旧库未重录、测不出 S1/S2 提取侧效果）。
- 提取侧 S1/S2 改动 → eval 重跑 97 篇金标准（与 T10 基线 `weighted_total≈0.7613` 对比）。
- **真跑改库（去 --dry-run）前**：dry-run report 交用户、经同意；改主库 schema 前 `.bak`（写库代码已含主库自动备份）。
- 开放项决议：低置信 exclude 用新 `exclude_suggested`；s02a 用 OR 传播；`in_scope` 本期**不**透传进 review。
