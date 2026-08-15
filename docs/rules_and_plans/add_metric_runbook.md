# 新增指标 Runbook（可重复执行）

> 目的：以后要给数据库加一个新 variable/指标时，照本清单执行，不必每次重新设计。
> 两条路径的样板代码都已在仓库里，逐步引用真实文件。
> 建立于 2026-W30 指标扩展（子类/储能/pv_capacity/金额四列），该轮 diff 本身就是最新范例。

## 0. 决策树：先选路径

问一个问题：**这个指标的证据，现有库里的文本字段（capacity_details / project_summary / additional_remarks）已经有了吗？**

- **有 → 轻路径（回填型）**：不动 prompt，不动 s01b/s02a 抽取链。加稀疏列 + 确定性解析器 + 流水线常设回填阶段（2B-x）。零 LLM 成本、可审计、可重跑、历史存量一并覆盖。
  - 范例：TX/MF 五列（2B-5）、储能/光伏子项/子类（2B-7）。
- **没有（证据只在新闻原文里，现有字段没固化）→ 重路径（抽取型）**：必须改 s01b prompt 让 LLM 抽，并把字段穿过全链 5 个硬编码点。只对新数据生效；历史存量按需另跑 LLM campaign 回填。
  - 范例：合同金额/融资额度四列（2026-W30）。

两条路径共用同一个第 1 步（主库迁移）。

## 1. 主库迁移（两条路径共用）

1. **一次性迁移脚本**：复制 [scripts/migrate_add_subcat_amount_columns.py](../../scripts/migrate_add_subcat_amount_columns.py)（多表版；单表版见 [scripts/migrate_add_txmf_columns.py](../../scripts/migrate_add_txmf_columns.py)），改 `_NEW_COLUMNS` 字典即可。安全范式勿删：
   - dry-run 默认（mode=ro 只读连接）；`--apply` 才落库
   - apply 时 sqlite3.backup WAL 备份 + 三重断言（存在/非0字节/行数一致）
   - busy_timeout=30s → 单事务 ALTER → 提交后自检（列齐全+行数不变）→ 幂等（duplicate column 静默）
2. **运行时幂等 ALTER 兜底**：在 s02a `_init_db` 的 ALTER 列表加同样的列（[duplicate_merger_tool.py](../../s02a_duplicate_merger/src/s02a_duplicate_merger/tools/duplicate_merger_tool.py) 搜 `_init_db`），保证全新建库也有列。
3. **测试**：照 [tests/test_migrate_subcat_amount.py](../../tests/test_migrate_subcat_amount.py)：dry-run 报 to_add → apply → 重跑幂等 → 行数不变。
4. **枚举值**（如新指标带受控枚举）：先加 [enums_mapping.json](../../enums_mapping.json)（单一可信源，禁止 .py/.ts 硬编码），再在 [bri_enums.py](../../bri_enums.py) 加一句话访问器，扩 [tests/test_enums_consistency.py](../../tests/test_enums_consistency.py)。

## 2A. 轻路径（回填型）

1. **纯函数解析器**（无 IO）：放 `s01b_project_extractor/src/s01b_project_extractor/`，样板 [txmf_spec_parser.py](../../s01b_project_extractor/src/s01b_project_extractor/txmf_spec_parser.py)、[storage_parser.py](../../s01b_project_extractor/src/s01b_project_extractor/storage_parser.py)、[subcategory_rules.py](../../s01b_project_extractor/src/s01b_project_extractor/subcategory_rules.py)。要点：
   - 数据源优先级固定（capacity_details → project_summary →（仅含单位文本的）capacity_mw_raw）；**裸数字禁止单独作来源**（历史误解析教训）
   - 关键词需在数值前邻接（负向排除易混单位，如 MW vs MWh、千伏 vs 千伏安）
   - 歧义保守置空，宁缺勿错
2. **回填 CLI**：放 `s01b_project_extractor/src/s01b_project_extractor/tools/`，照抄 [storage_subcat_backfill.py](../../s01b_project_extractor/src/s01b_project_extractor/tools/storage_subcat_backfill.py)（或 [txmf_backfill.py](../../s01b_project_extractor/src/s01b_project_extractor/tools/txmf_backfill.py)）骨架：
   - Pydantic 入口校验 + `_validate_db`（缺列时提示先跑迁移）
   - **fill-if-null**：只填 NULL，非空值（含人工 campaign 写入）永不覆盖
   - dry-run 默认；apply 时备份 + 单事务 executemany + **apply 后重扫 would_change 必须=0**（幂等自检）
   - 解析不出的进 misses CSV（supporting_docs/），喂下一轮人工/campaign
3. **流水线常设阶段**：在 [pipeline/src/bri_pipeline/flow.py](../../pipeline/src/bri_pipeline/flow.py) 的 `PipelineConfig` 加 `run_xxx: bool = True` 开关，在 2B 段末尾按序插 `2B-x` 阶段块（一职一号；现有 2B-2…2B-7）。增量新数据入库后由流水线自动补值。
4. **测试三件套**：解析器单测（正例/负例/边界）、回填单测（dry-run 零写入/apply 幂等/manual 不覆盖/excluded 跳过）、misses CSV。

## 2B. 重路径（抽取型）——5 个硬编码点逐一过

字段名在链上各处硬编码，没有集中 schema，**一个都不能漏**。以金额四列（2026-W30 diff）为对照范例：

| # | 位置 | 文件 | 改什么 |
|---|------|------|--------|
| 1 | s01b prompt | [prompts/s34_entity_finance.py](../../s01b_project_extractor/src/s01b_project_extractor/prompts/s34_entity_finance.py) + [prompts/s3_finance.py](../../s01b_project_extractor/src/s01b_project_extractor/prompts/s3_finance.py)（双路径同构） | 规则段（含与既有字段的**消歧/归属规则**，防互相污染）+ 输出 JSON 键。数值字段配一个 `... Raw` 原文短语键（审计不变量：非空 ⇔ 逐字原文） |
| 2 | s01b stage | [stages/s3_finance.py](../../s01b_project_extractor/src/s01b_project_extractor/stages/s3_finance.py)（AMOUNT_FIELDS 三元组模式）+ stages/s34_entity_finance.py | pop Raw 键 → `*_raw_phrase`；数值过 `bri_parse.parse_number` |
| 3 | s01b 库 | [tools/project_extractor_tool.py](../../s01b_project_extractor/src/s01b_project_extractor/tools/project_extractor_tool.py) | `_init_projects_db` CREATE + 幂等 ALTER 列表；两处 `base = {...}`（named/unnamed 路径都要）；INSERT 列/占位符/值三方对齐 |
| 4 | s02a 全链 | [tools/duplicate_merger_tool.py](../../s02a_duplicate_merger/src/s02a_duplicate_merger/tools/duplicate_merger_tool.py) | SCHEMA_SQL；`_init_db` ALTER；`_load_from_db` rename dict（snake_case↔Title Case）；`_group_into_logical_projects` 组装；`conflict_fields`（数值/枚举字段进 LLM 仲裁，raw 短语不仲裁随 first）；跨批 UPDATE + 新建 INSERT；`_fetch_event_as_dict`（跨批合并不丢值） |
| 5 | s03 + s04 契约 | [flat_builder.py](../../s03_data_exporter/src/s03_data_exporter/flat_builder.py)（建表+选值+行组装）、[events_exporter.py](../../s03_data_exporter/src/s03_data_exporter/events_exporter.py)（`_COLUMNS`+行组装）、[types/index.ts](../../s04_web_visualization/src/types/index.ts)（Project 接口加可选字段） | 金额类选值用 `_pick_amount`（终态事件优先，缺则最新有值）；CSV 数值列 None→空串 |

另：**历史存量策略**默认"只对新数据生效、历史留空"；需要历史时另起 LLM campaign（从已存文本回填），不要整体重跑抽取。

## 3. 导出与交付物（两条路径共用）

- `projects_flat`/CSV 加列后，**[data_dictionary.xlsx](../../s04_web_visualization/public/data/data_dictionary.xlsx) 必须同步加行**（表格页下载按钮旁的数据字典，用户对外口径）。
- 若前端暂不展示：只动 types/index.ts 可选字段（数据契约先行），展示 UI 另行排期——TX/MF 与本轮均如此。
- 全新独立域（如 TX/MF）考虑导出层分流出专用表/JSON（`tx_flat` 模式，flat_builder.py:408 起），而不是塞进 projects_flat。

## 4. 验证与上线（gate 惯例）

1. 单测全绿：根 `uv run --no-sync pytest tests/`、s01b、s02a、s03 各套。
2. **副本演练**：scratchpad 复制真库 → 迁移 dry-run/apply → 回填 dry-run/apply → 直调 build_flat/export_events_csv → 与现产物比对（**除新列外逐列零 diff**）。
3. **用户 gate（真库操作一律用户把关）**：真库迁移 `--apply` → 真库回填 apply（或跑一次流水线）→ `uv run run_export_only` → 部署（gh-pages + 公司服务器双发）。部署硬顺序：en_backfill → run_export → 部署。
4. 文档收尾：docs/changes 追加一条；仅当改变模块对外行为时覆盖更新对应 docs/status。
