# v2.1 待办清单（P2 / P2.1 未完事项 + 真实数据落地 + P3/P4/P5 提醒）

> 创建：2026-06-02。记录"机制已建好、但还没在真实数据上跑出效果 / 当初标推迟"的事项，避免遗忘。
> 上游：`docs/rules_and_plans/v2.1_plan.md`（roadmap）、`docs/status/review.md`（P2/P2.1 状态）。

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
| B1 | **真跑改库**（去 `--dry-run`，把 critic 自动 exclude / 自动改写真正落主库） | ❌ 未做，GATED（须人看 dry-run report 同意；动 schema 前 `.bak`，写库代码已含主库自动备份） |
| B2 | eval 提取并行化（`eval/predict.py`/`run_eval.py` 现完全串行，97 篇 ~24 分钟） | ❌ 未做（v2.1 follow-up；改加 ThreadPool 即可） |
| B3 | s02a `_fmt_entities`/`_merge_records` 实体去重排除占位实体 | ❌ 未做（影响有限，小迭代） |
| B4 | `exclude_source='manual'` 写入路径（人工 UI 反标 exclude / 一键翻回） | ❌ 未做（机制留了 manual 取值，但无写入入口） |

## C. ⚠️ 最关键：真实数据落地（"机制 vs 生效"的鸿沟）

**现状**：P1 + P2 + P2.1 的提取/复核改动**都只对新提取生效**。**主库 `s01b_project_extractor/data/projects.db` 仍是旧 prompt 的老数据**——里面没有任何 `excluded` 标记、没纳入新能源制造业/输变电、没经过新 critic。机制建好了，真实数据上的效果还没产生。

要"真正生效"需要两步（按依赖排序）：

1. **C1 · 用新 prompt 重新录入主库**（大动作、大 LLM 花费）
   - 对 `s01a` raw_news（~10815 篇通过关键词的）重跑 s01b 提取 → 新 `extracted_projects`（带 `in_scope`→`excluded`、新范围、火电保留）。
   - **只依赖提取层（s01b S1-S5 prompt）。P3/P4/P5 都不动提取层 → 现在重录入不会被它们作废。**
   - 动主库前 `.bak`（首次写会触发 ALTER 加三列）。

2. **C2 · 真跑 review 改库**（= B1）+ **重建 s02a canonical**
   - review 真跑：对新 `extracted_projects` 跑 `run_review`（去 `--dry-run`），落 `excluded`/字段纠正；先看 dry-run report 同意。
   - s02a 重跑：`extracted_projects` → `canonical_projects`（exclude OR 传播生效）。
   - ⚠️ **s02a/canonical 这层会被 P3（去重分组+地点归一化）改变** → 若现在重建 canonical，P3 落地后需**再重跑一次 s02a**（但**不必再重录入 s01b**，s02a 重跑比重录入便宜很多）。

## D. P3 / P4 / P5 是什么（roadmap 提醒）

| 子项目 | 做什么 | 是否动"提取层" | 是否动"去重/canonical 层" |
|---|---|---|---|
| **P3 · 去重分组 + 地点归一化**（从 v2.0 S5 推迟） | s02a 侧：分组去掉 stage 维度、地点/容量硬锚点防误并、地点音译归一化、实体归一化副作用（S5 草稿 Task2-5，见 `sessions/S5_dedup_location.md`） | ❌ 不动 | ✅ 动（s02a/canonical） |
| **P4 · 外部信源换源**（可选/最后） | s02b/s02c 侧：E.3 把 GDELT 换成证券/官方公告源做交叉验证 | ❌ 不动 | ❌ 不动（下游验证层） |
| **P5 · 去 crewAI 薄壳**（机械/独立） | 清理 s01a/s02a/**s02c**（roadmap 原写"s02b"为笔误）的 crewAI 冗余薄壳（删 crew.py + yaml、工具去 BaseTool、main 改纯 Python、pyproject 移除 crewai）+ pipeline/flow.py Flow→纯 Python。**纯机械重构，不改任何数据逻辑/输出。** | ❌ 不动 | ❌ 不动 | ✅ **已完成（2026-06-02）** |

**关键事实**：**只有 P3 动 s02a/canonical 层；P4、P5 都不动数据产出。提取层（s01b）在 P2.1 后已冻结，P3/P4/P5 都不再改它。**

> ~~**P5 实施计划已就绪**~~ → **✅ P5 已完成（2026-06-02）**：见 `docs/changes/2026-W22h.md`。s01a/s02a/s02c 三薄壳 + pipeline/flow.py 全去 crewAI（工具改普通类、删 crew.py+yaml、main 直调 `_run`、flow 改 `run_pipeline(cfg)` 并修 P1 后已断的 s01b crew import）；全仓 `git grep crewai` 仅剩老版 `s01_data_collector_filter`（归档候选）。行为不变、零 LLM 花费；s02a 单测 12 passed、smoke import 全通过。
> **遗留**：① 老版 `s01_data_collector_filter` 仍依赖 crewAI（归档/删除候选，单独决策）；② s01b orchestrator 3 个 s34 测试预存失败（源自 e0cc102，与去 crewAI 无关，s01b 线单独修）。

## E. 真实数据该现在跑还是等 P3/P4/P5 之后？（决策待用户拍板）

**核心权衡**（基于"提取层已冻结、只有 P3 动 canonical"）：

- **现在跑 C1 重录入** = 提取层已含 P1+P2+P2.1，**不会被 P3/P4/P5 作废**；能尽早在真实数据上验证 P2.1 的"recall-first 宽收 + exclude 兜底"是否真把 eval 暴露的 precision 下降（pred 91 vs gold 69）追回来；早发现提取问题。代价：canonical 层（C2 的 s02a）在 P3 后要再重跑一次（便宜）。
- **等 P3 之后再一次性跑** = 重录入 + 去重 + review 只做一遍、最干净、零返工；但在 P3（状态"待设计"，时间未知）之前你一直**看不到 P2.1 在全量数据上的真实效果**，等于把 P3 改动叠加在一个未经真实验证的提取基座上。

**建议（Claude）**：倾向**现在就做 C1 重录入 + review dry-run 全量复看**（提取层已冻结，这步不会白做），借此验证 P2.1 全量实效；**但把 C2 的 canonical 最终成型推迟到 P3 之后**（避免 s02a 白跑两遍中"被 P3 改写"的那遍）。即：
- 现在：重录入 s01b（一次性）→ review 全量 dry-run → 看 exclude/范围 实效 → （同意后）review 真跑落 `excluded`。
- P3 完成后：重跑 s02a 一次 → 最终 canonical → s03 导出 → s04。

若优先**省钱/省返工**且信任现有 eval+小样本信号，则**等 P3 之后一次性跑全部**也合理——代价是延后全量验证。**这是成本/风险偏好题，待用户定。**
