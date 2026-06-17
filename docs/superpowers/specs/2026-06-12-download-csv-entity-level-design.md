# 下载 CSV 重做：实体级明细 + 网页口径全量

- 日期：2026-06-12
- 分支：worktree-v2（worktree `.claude/worktrees/v2`）
- 范围：仅改 s03 后端 `events_exporter.py`（+ 测试 + 文档）。前端、s04 下载逻辑、问卷弹窗一律不动。

## 1. 背景与现状

网页「下载数据」按钮（`s04/.../TablePage.tsx` 的 `triggerDownload`）的行为是：直接 `fetch` 后端预先烤好的静态文件 `public/data/events_export.csv` 原样下载，**与前端筛选无关**（筛选只作用于交互式地图/列表）。该文件由 s03 流水线 `events_exporter.export_events_csv()` 生成，main.py 第 [6/6] 步同时写入 s03 output 与 s04 public/data。

现状 CSV 的问题：

1. **粒度过粗 + 实体信息丢失**：一行 = 一个 `project_event`，同一事件下的多家中国公司被压扁进单列 `entities`（仅中文名、`"; "` 拼串），`is_central_soe` 被 `any()` 折叠成整行一个 0/1。公司英文名、子公司中/英文名、参与角色全部丢失。
2. **口径与网页不一致**：现状只排除 `canonical_projects.excluded=1`，未应用网页可视化的 2022 基线政策（排除中港澳台、最晚事件早于 2022 的项目等）。

## 2. 目标

把下载文件改成「实体级明细、与网页展示口径一致的 2022+ 全量数据」：

- 行粒度：一行 = 一个事件 × 一家中国公司。
- 补全每家公司的：中文名、英文名、子公司中/英文名、参与角色中/英文、各自的央企标志。
- 口径对齐网页：项目集 = 网页显示的项目；事件行只含 `year ≥ 2022`。

文件名、路径、UTF-8-sig BOM、双写目标、前端下载逻辑均不变；跑一次 `uv run run_export` 重新生成即生效。

## 3. 输出列规格

替换现状 21 列中的实体部分。新列集（顺序）：

| 分组 | 列名 | 来源 |
|---|---|---|
| 标识 | `canonical_id` | `project_events.canonical_id` |
| | `event_id` | `project_events.id` |
| 项目 | `project_name_en` | `canonical_projects.project_name_en` |
| | `project_name_cn` | `canonical_projects.project_name` |
| | `country_en` | `iso3_to_en[country_iso]` |
| | `country_iso` | `canonical_projects.country_iso` |
| | `province` | `province_map[canonical_id]` |
| | `category_en` | `enums.category_en(category)` |
| | `category_cn` | `canonical_projects.category` |
| 事件 | `stage_cn` | `project_events.project_stage` |
| | `stage_en` | `enums.stage_en(stage)` |
| | `stage_group_cn` | `enums.viz_groups()[viz_group(stage)].zh` |
| | `stage_group_en` | `enums.viz_groups()[viz_group(stage)].en` |
| | `year` | `_year(publish_date or investment_date)` |
| | `month` | `_month(publish_date or investment_date)` |
| | `capacity_mw` | `project_events.capacity_mw` |
| | `total_investment_usd` | `project_events.total_investment_usd` |
| | `financing_model_en` | `enums.financing_en(financing_model)` |
| | `verification_status` | `project_events.verification_status` |
| **公司（新增，每行一家）** | `entity_cn` | `chinese_entities.implementing_entity` |
| | `entity_en` | `chinese_entities.implementing_entity_en` |
| | `subsidiary_cn` | `chinese_entities.subsidiary_name` |
| | `subsidiary_en` | `chinese_entities.subsidiary_name_en` |
| | `involvement_type_cn` | `chinese_entities.involvement_type` |
| | `involvement_type_en` | `enums.involvement_en(involvement_type)` |
| | `is_central_soe` | `chinese_entities.is_central_soe`（每行该公司自己的值，0/1/空）|

**删除**：旧列 `entities`（拼串）、旧整行折叠的 `is_central_soe`。
**不含**：`subcontract_amount` / `subcontract_amount_raw`、`entity_en_quality`。

枚举中英文映射一律走 `bri_enums`（单一可信源 `enums_mapping.json`），禁止硬编码。所需函数均已存在：`category_en` / `stage_en` / `viz_group` / `viz_groups` / `financing_en` / `involvement_en`。

## 4. 行生成规则

遍历 `project_events`，对每个事件：

1. 取其 `canonical_id` 对应的项目；若项目不在「合格项目集」（见 §5）→ 跳过整个事件。
2. 若事件 `year < 2022`（或无法解析出年份）→ 跳过该事件。
3. 取该事件的中国公司列表 `ents`：
   - `ents` 非空：每家公司出一行，填该公司自己的 7 个实体列；
   - `ents` 为空：仍出 1 行，7 个实体列留空（不丢事件）。

事件年份用 `_year(publish_date or investment_date)`，与 flat_builder 的 `stage_events` 年份口径一致。

## 5. 合格项目集（口径对齐网页）

网页项目集 = `projects_flat`（已排除 `excluded=1`）再应用前端 `DataContext.shouldExcludeByPolicy`。该 Python 实现以 `projects_flat` 行（已由 build_flat 算好）为输入，排除规则与前端逐条对应：

| 前端规则（`DataContext.tsx`） | Python 等价 |
|---|---|
| `country_iso ∈ {CHN,HKG,MAC,TWN}` | 同一集合 |
| `first_event_year == null \|\| last_event_year == null` | 同 |
| `last_event_year < 2022` | 同（`BASELINE_YEAR = 2022`）|
| `isExcludedCategory(category) \|\| isHiddenCategory(category)` | `enums.is_excluded_category` / `enums.is_hidden_category` |
| `isUnknownText(primary_entity)` | 复刻 `UNKNOWN_TEXTS` 集合（unknown/未知/未披露/n/a/na/-/—）|
| `hasCapacity(category) && capacity_mw == null` | `not enums.is_no_capacity(category) and capacity_mw is None` |

**实现方式**：events_exporter 读取已构建的 `projects_flat`（DST_DB `bri_re_export.db`）获得合格判定所需字段（`first_event_year` / `last_event_year` / `category` / `primary_entity` / `capacity_mw` / `country_iso`），应用 `passes_baseline()` 得到合格 `canonical_id` 集合；事件与公司明细仍从 SRC_DB（s02a 三表）拉取。main.py 现有顺序已保证 build_flat（[2/6]）先于 events 导出（[6/6]）。

把 `passes_baseline()` 放在单一 Python 位置（events_exporter 内或共享 helper），并用测试钉住规则，降低与前端 TS 漂移的风险（TS/Python 双实现无法共享代码，仅能靠测试对齐）。

## 6. 函数签名变更

`export_events_csv` 需要新增对 `projects_flat`（或其等价的合格集 + 字段）的访问。建议签名：

```python
def export_events_csv(
    src_db,                      # s02a 三表
    out_paths: list,
    flat_db=None,                # DST_DB，用于读 projects_flat 做基线判定（None 时退化为仅排 excluded）
    iso3_to_en: Optional[dict] = None,
    province_map: Optional[dict] = None,
) -> int:
```

main.py 第 [6/6] 步补传 `flat_db=DST_DB`。

## 7. 测试（`test_events_exporter.py`）

- 行粒度：构造一事件挂 3 家公司 → 出 3 行，各行实体列取各自值。
- 实体列：英文名/子公司中英文/角色中英文/`is_central_soe` 按公司各自输出，不再折叠。
- 空实体事件：合格项目下某事件无公司 → 仍出 1 行，实体列空。
- 基线口径：分别构造 中港澳台 / `last_event_year<2022` / 排除类别 / primary_entity 未知 / 应有容量但缺容量 的项目 → 均不出现在输出。
- 事件年份：`year<2022` 的事件行被剔除；`year>=2022` 保留。
- 列集：表头等于 §3 规定列、删除的列不存在。

## 8. 预期效果

下载文件从「5184 行事件级（拼串）」变为实体级明细：行数上限约等于源 `chinese_entities` 的 6316 行，再扣除基线排除项目与 `year<2022` 事件对应的公司行，外加「无公司事件」各补 1 行——最终落在数千行量级（实际值以重跑结果为准）。每行带独立的公司中英文名、子公司、角色、央企标志。

## 9. 收尾

按项目规则同步 `docs/status`（项目状态）与 `docs/changes`（修改日志）。MEMORY.md 旧待办「CSV 下载重设计」可标记完成。

## 10. 非目标（YAGNI）

- 不做「按前端筛选导出」（用户已明确：筛选只作用于交互地图，下载即全量）。
- 不改前端下载逻辑、问卷弹窗、文件名/路径。
- 不导出 `subcontract_amount` / `entity_en_quality`。
- 不引入新的前端数据文件（projects_flat.json 等不变）。
