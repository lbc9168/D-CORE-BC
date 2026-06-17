# v2.1 待办清单（P2 / P2.1 未完事项 + 真实数据落地 + P3/P4/P5 提醒）

> 创建：2026-06-02；**更新：2026-06-10**（C1 重录入✅、C2 dry-run✅、E 部分决策已拍板）。
> 上游：`docs/rules_and_plans/v2.1_plan.md`（roadmap）、`docs/status/review.md`（P2/P2.1 状态）、`docs/changes/2026-W23a.md`（C1/C2 实跑记录）。
>
> **当前主线（2026-06-10 用户拍板；2026-06-13 P3 代码完成更新）**：~~P3（去重分组+地点归一化，待设计）~~ **P3 代码✅（2026-06-13，实跑 GATED 并入重建 runbook）** → C2 真跑落库（用户自做，先复看 dry-run CSV）→ s02a canonical 重建（届时自动走 P3 新去重逻辑）→ s03 导出 → s04。**P4 不做**（改动过大，搁置）。
>
> **P3 代码完成（2026-06-13，6 commit `62b08cf..4b8264f`，subagent-driven）**：(a) 分组去 stage + 跨阶段分摊合并 + dedup_group_id；(b) Signal 6 地点/容量锚点 + canonical 地点辅助通道 + `location_norm` 列；(c) 新建根 `bri_location.py` + s02b `normalize_locations` CLI + s02a 批内 in-memory 归一；顺手 `LLM_CONFIRM_MODEL` env 化。计划 `docs/superpowers/plans/2026-06-13-v2.1-P3-dedup-location.md`，日志 + **Phase B 实跑 runbook** `docs/changes/2026-W24b.md`。测试 s02a 30 / 根 101 / s03 13 全绿。**实跑落库 GATED（用户在主工作区配合 C2/重建一次性做，见 W24b runbook）**。

---

## A. P2 未完（复核机制）

| # | 事项 | 当初定位 | 状态 |
|---|---|---|---|
| A1 | **review_queue 人工裁决后回写库 + 标 resolved** | spec 当初说本期只导出 CSV | ❌ 未做（现仅 `export_queue` 导出，无回写脚本） |
| A2 | s03 对 chinese_unnamed 的"待补名"计数 | 标"可选小步、不阻塞" | ❌ 未做（s03 无引用） |
| A3 | s04 前端对 unnamed 的显示/筛选 | 标 YAGNI | ❌ 未做 |

## B. P2.1 未完（exclude + 范围对齐 + critic 约束）

| # | 事项 | 状态 |
|---|---|---|
| B1 | **真跑改库**（把 critic 自动 exclude / 自动改写真正落主库） | ❌ 未做，GATED（dry-run 已全量跑完，报告/CSV 待人工复看；**推荐按 `reports/20260603_005742.json` 的 `would_change` 确定性重放**，勿直接去 `--dry-run` 重跑——queue 无去重会重复入队 4,413 条 + 重花 critic LLM 钱；181 条 critic_failed 单独补跑） |
| B2 | eval 提取并行化（`eval/predict.py`/`run_eval.py` 现完全串行，97 篇 ~24 分钟） | ❌ 未做（v2.1 follow-up；改加 ThreadPool 即可） |
| B3 | s02a `_fmt_entities`/`_merge_records` 实体去重排除占位实体 | ❌ 未做（影响有限，小迭代） |
| B4 | `exclude_source='manual'` 写入路径（人工 UI 反标 exclude / 一键翻回） | ❌ 未做（机制留了 manual 取值，但无写入入口） |

## C. ⚠️ 最关键：真实数据落地（"机制 vs 生效"的鸿沟）

**更新（2026-06-10）**：C1 已完成、C2 的 dry-run 部分已完成（详见 `docs/changes/2026-W23a.md`）。

1. **C1 · 用新 prompt 重新录入主库** ✅（2026-06-02）
   - 101 月度批全量重跑完成：`extracted_projects` 11,909 行（6,678 篇 distinct），提取时 `excluded=1` 176 行，unnamed 占位流入队 61 条；仅 1 篇网络异常隔离（重跑自动补）。
   - 容量清洗 56→0；备份保留 `projects.db.bak_20260602_235452`（清洗前 C1 产出）。

2. **C2 · 真跑 review 改库**（= B1）+ **重建 s02a canonical** —— **dry-run ✅ / 真跑 ❌**
   - **已做**：全量 dry-run（2026-06-03）：candidates 4,012 / 拟纠正 397 / 拟 exclude 1,717 / 入队 4,413 / critic 失败 181；CSV `review/review_queue_c2_dryrun.csv` 待人工复看。
   - **未做（GATED，用户之后自做）**：真跑落库。推荐按报告 `would_change` 确定性重放（见 B1 注）。
   - **未做**：s02a 重跑 `extracted_projects` → `canonical_projects`（exclude OR 传播生效）。
   - ⚠️ **s02a/canonical 这层会被 P3（去重分组+地点归一化）改变** → 已决策：**s02a 重建放到 P3 落地之后一次做**（不必再重录入 s01b，s02a 重跑比重录入便宜很多）。

## D. P3 / P4 / P5 是什么（roadmap 提醒）

| 子项目 | 做什么 | 是否动"提取层" | 是否动"去重/canonical 层" |
|---|---|---|---|
| **P3 · 去重分组 + 地点归一化**（从 v2.0 S5 推迟） | s02a 侧：分组去掉 stage 维度、地点/容量硬锚点防误并、地点音译归一化、实体归一化副作用（S5 草稿 Task2-5，见 `sessions/S5_dedup_location.md`） | ❌ 不动 | ✅ **代码已完成（2026-06-13，6 commit `62b08cf..4b8264f`）；实跑 GATED**（计划 `docs/superpowers/plans/2026-06-13-v2.1-P3-dedup-location.md`，日志+runbook `docs/changes/2026-W24b.md`） |
| **P4 · 外部信源换源**（可选/最后） | s02b/s02c 侧：E.3 把 GDELT 换成证券/官方公告源做交叉验证 | ❌ 不动 | ❌ 不动（下游验证层） |
| **P5 · 去 crewAI 薄壳**（机械/独立） | 清理 s01a/s02a/**s02c**（roadmap 原写"s02b"为笔误）的 crewAI 冗余薄壳（删 crew.py + yaml、工具去 BaseTool、main 改纯 Python、pyproject 移除 crewai）+ pipeline/flow.py Flow→纯 Python。**纯机械重构，不改任何数据逻辑/输出。** | ❌ 不动 | ❌ 不动 | ✅ **已完成（2026-06-02）** |

**关键事实**：**只有 P3 动 s02a/canonical 层；P4、P5 都不动数据产出。提取层（s01b）在 P2.1 后已冻结，P3/P4/P5 都不再改它。**

> ~~**P5 实施计划已就绪**~~ → **✅ P5 已完成（2026-06-02）**：见 `docs/changes/2026-W22h.md`。s01a/s02a/s02c 三薄壳 + pipeline/flow.py 全去 crewAI（工具改普通类、删 crew.py+yaml、main 直调 `_run`、flow 改 `run_pipeline(cfg)` 并修 P1 后已断的 s01b crew import）；全仓 `git grep crewai` 仅剩老版 `s01_data_collector_filter`（归档候选）。行为不变、零 LLM 花费；s02a 单测 12 passed、smoke import 全通过。
> **遗留**：① ~~老版 `s01_data_collector_filter` 仍依赖 crewAI（归档/删除候选，单独决策）~~ → ✅ 已归档至 `archive/`（2026-06-10，repo 整理 Commit `875edf2`）；② ~~s01b orchestrator 3 个 s34 测试预存失败~~ → ✅ 已修（2026-06-02，commit 2e439d6）。

## E. 真实数据该现在跑还是等 P3/P4/P5 之后？（✅ 已决策并执行）

> **决策结果（2026-06-02 执行 / 2026-06-10 拍板补全）**：采纳"现在跑 C1 + dry-run 全量复看"方案——C1 重录入与 review 全量 dry-run 已完成；**C2 真跑由用户复看 CSV 后自做**；**s02a canonical 最终成型推迟到 P3 之后**；**P4 不做**。以下为当时的权衡分析（留档）。

**核心权衡**（基于"提取层已冻结、只有 P3 动 canonical"）：

- **现在跑 C1 重录入** = 提取层已含 P1+P2+P2.1，**不会被 P3/P4/P5 作废**；能尽早在真实数据上验证 P2.1 的"recall-first 宽收 + exclude 兜底"是否真把 eval 暴露的 precision 下降（pred 91 vs gold 69）追回来；早发现提取问题。代价：canonical 层（C2 的 s02a）在 P3 后要再重跑一次（便宜）。
- **等 P3 之后再一次性跑** = 重录入 + 去重 + review 只做一遍、最干净、零返工；但在 P3（状态"待设计"，时间未知）之前你一直**看不到 P2.1 在全量数据上的真实效果**，等于把 P3 改动叠加在一个未经真实验证的提取基座上。

**建议（Claude）**：倾向**现在就做 C1 重录入 + review dry-run 全量复看**（提取层已冻结，这步不会白做），借此验证 P2.1 全量实效；**但把 C2 的 canonical 最终成型推迟到 P3 之后**（避免 s02a 白跑两遍中"被 P3 改写"的那遍）。即：
- 现在：重录入 s01b（一次性）→ review 全量 dry-run → 看 exclude/范围 实效 → （同意后）review 真跑落 `excluded`。
- P3 完成后：重跑 s02a 一次 → 最终 canonical → s03 导出 → s04。

若优先**省钱/省返工**且信任现有 eval+小样本信号，则**等 P3 之后一次性跑全部**也合理——代价是延后全量验证。**这是成本/风险偏好题，待用户定。**

---

## F. 最后一遍带原文 AI 复核 —— ✅ 代码 + 真跑落库已完成（2026-06-17，详见 `docs/changes/2026-W24e.md`）

> **状态更新（2026-06-17）**：spec/plan 落地（`docs/superpowers/{specs,plans}/2026-06-17-v2.1-recheck-second-pass*`）。新建 `review/recheck.py`（薄编排器，复用 critic/apply/run_review 闭包）+ critic `recheck_project`；TDD 9 测试、根回归 127 全绿。
> - **dry-run → 用户复看 → `--apply` 真跑**（v4-pro / concurrency=40，主仓库 raw_news.db 只读）：**净新增 exclude 46 条（exclude_source='recheck'）+ involvement_type 纠正 23 条**；category/capacity 未自动改（→残留 894 行 CSV）。改前 .bak。
> - 实跑暴露并 TDD 修复两问题：①首版串行 ~5–6h → 并发化 ~11min；②concurrency=300 连接风暴炸整批 → 降 40 + worker 广捕隔离；③apply 复用旧闭包误写 source='review' → 修为 'recheck' + relabel 46 条。
> - **落地（用户拍板：不做全量 B3→B7，改增量）**：69 改动直接外科手术打进现有 `bri_re_database.db`（exclude 走 OR 语义置 canonical.excluded，靠 source_link→event→canonical 消歧）——**canonical excluded 1196→1229（净+33）+ 22 involvement 更新**，保留 B4/B5 归一 + 6 组合并；只重跑 B7（`projects_flat 3835→3802`，events_export 2657→2642）。脚本 `scripts/oneoff/output/_recheck_incremental_{match,apply}.py`，备份 `bri_re_database.db.bak_pre_incremental_20260617_083533`。
> - **残留**：s04 浏览器实测（数据已导出，未做）；recheck_residual.csv 894 行未自动改项；2/1 条 critic_failed 可补跑。
> - 下方为原设计草案（留档）。

### 原设计草案（2026-06-13）

> 起因：C2 dry-run 把 4,357 条标为 `low_confidence_field` 入队。详拆后发现——**约 90% 没有具体建议值**（critic 只标"可疑"给不出修正），且部分"建议"套用反而更糟（involvement →Unknown 降级、project_name 张冠李戴）。真正可操作的高价值子集很小。决策：**本轮不手工复核**，改为打 severity 标记 + 留到一个独立的"带原文 AI 复核"步骤统一处理。

### confidence 的真实语义（看 review/critic.py 确认）
- **不是计算值，是 critic LLM 自报的数**（Verdict.confidence，pydantic 仅校验 ∈[0,1]）。
- 整个 prompt **只有 exclude 一处校准**（critic.py:57「确定该排除 1.0；存疑 <1.0」）；字段纠正类 confidence **无任何指引** → 未校准的模型"感觉"。
- 推论：`conf=1` ≠ 改动可信；如 1,846 条 capacity 是 conf=1 但无建议值（模型自信"可疑"却给不出数）。**当软信号，别当概率。**

### severity 标记（与 confidence 正交，确定性算，见 scripts/oneoff/output/tag_severity.py）
- **severity = 若 critic 说得对、后果多大**（看字段+方向）；**confidence = 说对的可能性**（LLM 自报）。两轴**不可合并**——低 conf + 高 severity（如"可能该排除的非 RE"）正是最该让第二遍带原文重看的，合并会把它埋掉。confidence 只在**低 severity 端**强化"忽略"。
- 规则：**高**=category 改成非 RE/exclude；**中**=category 任何类别挑战（含无建议）/involvement 真·角色换角色（非→Unknown）/capacity 有建议且≥30%变/project_name 非张冠李戴真改；**低**=其余（capacity 无建议或小变、involvement →Unknown 或无建议、project_name 疑似张冠李戴）。
- C2 实际分布：**高 1 / 中 854 / 低 3,502** → 最后一遍只扫**高+中 = 855 条**，低的 3,502 维持原值。交叉表证实正交：中 severity 里有 140 条 conf<0.9（最该二次复核的"重要但不确定"）。

### 最后一遍复核步骤设计（未建）
- **本质 = 带原文的第二轮 critic**（不是对队列文本做判断——90% 无建议值，等于 critic 已看过一次原文没判出来；必须重新喂原文）。
- **输入**：severity∈{高,中} 的 855 条 + 各自 source_link → 从 `raw_news.db` 取原文 + 第一遍的 verdict/confidence/evidence 作上下文。
- **建议增强**：换更强模型/更细提示；产出仍须过二次闸（防 →Unknown 降级、张冠李戴被采纳）。
- **落点（待定）**：在 s02a 重建**之前**（让纠正/排除先进 extracted_projects 再去重）或 s03 **之前**——倾向前者；需用户拍板。
- **与日常增量的关系**：日常新数据的"应用复核结论"由 `run_review`（非 --dry-run）内联完成；本步是针对"第一遍拿不准"那批的**二次兜底**，可考虑将来并入 run_review 作为"低置信项二次 pass"。
- **状态**：设计草案，**待 C2 真跑 + s02a 重建走完再建**（届时 canonical 已成型，范围/落点更清晰）。打标脚本 `scripts/oneoff/output/tag_severity.py` 已就绪可复跑。
