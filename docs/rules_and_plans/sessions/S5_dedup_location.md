# S5 实施计划：去重分组改造 + 地点归一化

> ✅ **Task 2-5 已由 v2.1 P3 实施（2026-06-13）**：实施计划 `docs/superpowers/plans/2026-06-13-v2.1-P3-dedup-location.md`（含设计更新：跨阶段分摊合并 + dedup_group_id、地点归一化拆两个消费端、存量回溯被"P3 后全量重建"化解），实跑 runbook 见 `docs/changes/2026-W24b.md`。本文档 Task 2-5 为当时的起草稿，留档。
>
> ⚠️ **范围调整（2026-05-24）**：v2.0 **只执行 Task 1（S5d：修 `_update_db` bug）**。Task 2-5（(a) 分组去 stage、(b) 锚点、(c) 地点归一化、存量重去重）**全部推迟到 v2.1**，与 agent 重构一起做（本文档 Task 2-5 即 v2.1 起草稿）。原因：与 v2.1 的 agent/复核重构同属一摊、最易被覆盖，且数据操作高风险+费 LLM。
>
> 上级计划：[v2_plan.md](../v2_plan.md) §三 S5
> 性质：**最大、最高风险的一站**。改去重正确性逻辑 + 新建 LLM 地点归一化模块 + 改 s02a 主库数据。
> ⚠️ **不适合无人值守自动跑到底**：含 Gemini 调用（花钱、非确定性）+ 主库数据变更 + 需人工判断合并对错。Task 1-3、Task 4 的"写代码"部分可自动；**Task 4/5 的"实跑 DB"必须人工检查点**。
> 执行：TDD（可单测的逻辑先测）。完成后更新 `docs/status` 与 `docs/changes`。

**目标**：
- (a) 候选预分组 `(country, category, project_stage)` → `(country, category)`，修跨阶段同项目漏合并。
- (b) 名称模糊分低时，加"同国 + 容量接近 + 同规范地名"硬锚点。
- (c) 新建地点（地名）归一化（复用 entity_normalizer 框架），达瓦德密 = 达旺德米。
- (d) 修 `entity_normalizer._update_db`：母公司别名映射误改 subsidiary_name。

**关键事实（调研确认）**：
- `duplicate_merger_tool.py` **1508 行**；`entity_normalizer.py` **438 行**。
- 预分组 bug 点：`duplicate_merger_tool.py:539-542`（grouping key 含 project_stage）。
- `_llm_confirm` prompt（L657-691）写死"同一工程、同一阶段"，含规则 2.1（已让 LLM 把不同名同地当匹配——正是 (c) 要替代的人肉兜底）。
- `_find_or_create_canonical`（L979-1098）跨批次匹配**只比 4 个名称对、完全不看 location**。
- 候选信号级联在 L565-613（Signal 4 容量、Signal 5 宽松），**当前无任何 location 锚点**。
- 匹配时容量用内存 raw string + `_parse_capacity`（=bri_parse）；location 用 `_infer_location_from_name`（L460-512）回填后的内存 string。
- `entity_normalizer`：`_find_candidates`（三信号，L70-136）、`_llm_confirm_batch`（L141-200）、`_build_mapping`（union-find，L205-249，**纯字符串可原样复用**）、`_update_db`（L254-283，bug 处）。Step1 只从 `implementing_entity` 建映射（L358-364），却在 `_update_db` 同时改 `subsidiary_name` → bug 根源。

---

## 设计决策

1. **(d) 先做**（最独立、零 LLM、低风险，可纯自动）：`_update_db` 不再用 implementing 映射改 subsidiary；如需归一 subsidiary 另从 subsidiary 列建独立映射。
2. **(a) 分组去 stage**：改 L542；同时把 `_llm_confirm` / `_llm_confirm_canonical` prompt 从"同一阶段"改为"同一物理工程（阶段可不同）"。合并落地语义：跨阶段同项目 → 一条 canonical + 多条 event（现有 `(canonical_id, project_stage)` event 键已支持）。
3. **(b) 地点锚点**：在 `_find_duplicate_candidates`（L565-613）加 `location_match` boost（规范地名相等 + 容量接近时送 LLM）；`_find_or_create_canonical`（L1018-1028）打分纳入规范地名。依赖 (c) 的规范地名。
4. **(c) 地点归一化**：新建 `s02b_entity_normalizer/.../location_normalizer.py`，复用 union-find + 编排壳；**三信号与 prompt 需为音译变体重调**（达瓦德密 vs 达旺德米：短串、逐字音译差异，子序列信号不会命中，需放宽 + 靠 LLM）；读写 `canonical_projects.location`（单列，无母子问题）。在 s02a 去重后、作为 s02b 的一部分运行；规范地名回写后供 (b) 锚点用。
5. **模型固定**：`LLM_CONFIRM_MODEL = "gemini-3-flash-preview"`（duplicate_merger L59）是 preview，check-record 🔴 标注。S5 顺手改为稳定版本（与项目其它处一致的 gemini-2.5-flash-lite 或确认的稳定版），集中到一处常量。

---

## Phase A —— 可自动（纯代码 + 单测，不实跑 DB、不调 LLM）

### Task 1：(d) 修 entity_normalizer._update_db（TDD）

**Files:** Modify `s02b_entity_normalizer/.../entity_normalizer.py:254-283`; Test `tests/test_entity_update_db.py`

- [ ] **Step 1：失败测试**——构造一行 implementing='中国电建' 且另一行 subsidiary='中国电建'，映射 {中国电建→中国电力建设集团} 只应改 implementing 列，不应动 subsidiary。

```python
import sqlite3
from s02b_entity_normalizer.entity_normalizer import _update_db

def test_update_db_does_not_touch_subsidiary_with_impl_map(tmp_path):
    db = tmp_path/"t.db"; c=sqlite3.connect(db)
    c.execute("CREATE TABLE chinese_entities(id INTEGER PRIMARY KEY, implementing_entity TEXT, subsidiary_name TEXT)")
    c.execute("INSERT INTO chinese_entities(implementing_entity, subsidiary_name) VALUES('中国电建','某子公司')")
    c.execute("INSERT INTO chinese_entities(implementing_entity, subsidiary_name) VALUES('某母公司','中国电建')")
    c.commit(); c.close()
    _update_db(str(db), {"中国电建":"中国电力建设集团"})
    c=sqlite3.connect(db)
    assert c.execute("SELECT implementing_entity FROM chinese_entities WHERE id=1").fetchone()[0]=="中国电力建设集团"
    # 第二行 subsidiary 不应被 implementing 映射改写
    assert c.execute("SELECT subsidiary_name FROM chinese_entities WHERE id=2").fetchone()[0]=="中国电建"
```

- [ ] **Step 2**：跑确认失败（当前会把 subsidiary 也改成规范名）。
- [ ] **Step 3**：改 `_update_db` 签名为 `_update_db(db_path, impl_mapping, sub_mapping=None)`：只用 impl_mapping 改 implementing_entity；仅当传入 sub_mapping 才改 subsidiary（用 `executemany`，符合 CLAUDE.md 规则3）。`run` Step5 调用只传 impl_mapping（subsidiary 归一暂不做，留空）。
- [ ] **Step 4**：跑转绿。提交。

### Task 2：(a) 分组去 stage + prompt 放宽（含单测）

**Files:** Modify `duplicate_merger_tool.py:542, 657-691, 710-766`; Test `tests/test_dedup_grouping.py`

- [ ] **Step 5：失败测试**——两条同国同类同名但不同 stage 的记录，应进同一候选组（改前不会）。针对 `_find_duplicate_candidates` 写最小用例。
- [ ] **Step 6**：L542 grouping key 去掉 `p["project_stage"]` → `(p["country"], p["category"])`。
- [ ] **Step 7**：`_llm_confirm`（L658「同一工程、同一阶段」、L665 展示 project_stage、L681 规则）与 `_llm_confirm_canonical` 同步：措辞改"同一物理工程，**阶段可不同**（签约/开工/竣工属同一项目的不同阶段）"。
- [ ] **Step 8**：跑测试转绿。提交。

### Task 3：(b) 地点/容量硬锚点（含单测）

**Files:** Modify `duplicate_merger_tool.py:565-613, 1018-1028`; Test 扩充 `tests/test_dedup_grouping.py`

- [ ] **Step 9：失败测试**——名称模糊分低（<40）、但同国 + 容量 1% 内 + 规范地名相同 → 应成候选。
- [ ] **Step 10**：在信号级联加 `location_match` boost：当 `not name_ok and not entity_boost` 且两记录规范地名非空且相等且容量接近 → 候选。规范地名取 `project.get("location_norm")`（Task 4 回写；无则回退原 location）。
- [ ] **Step 11**：`_find_or_create_canonical` 打分（L1018-1028）：canonical 查询带出 location，名称分基础上若规范地名相等给加权（或作为 LLM 上下文）。
- [ ] **Step 12**：模型常量收口：`LLM_CONFIRM_MODEL` 改稳定版本，集中定义。跑测试。提交。

> Phase A 全部为代码 + 单测，**可无人值守自动完成**（不实跑 DB、不调真实 LLM——测试用 mock 或纯函数）。

---

## Phase B —— 需人工检查点（新建 LLM 模块 + 实跑主库）

### Task 4：(c) 地点归一化模块 location_normalizer.py

**Files:** Create `s02b_entity_normalizer/src/s02b_entity_normalizer/location_normalizer.py`

- [ ] **Step 13**：克隆 `entity_normalizer.py` 结构：`_build_mapping`（union-find）原样复用；`_find_candidates` 重调（地名短、音译差异：放宽 token 阈值、去掉/弱化子序列信号、降低 MIN_BOTH_LEN）；`_llm_confirm_batch` prompt 重写为"判断是否同一地点的不同音译/简繁/拆分写法（如 达瓦德密=达旺德米）"。
- [ ] **Step 14**：读 `canonical_projects.location`（按 country_iso 分组，组内归一），把规范地名写回新列 `canonical_projects.location_norm`（加列，idempotent；保留原 location）。
- [ ] **Step 15（写代码可自动；下面"实跑"需人工）**：先 `--preview`（dry-run，打印候选对与 LLM 判定，**不写库**）在主库**副本**上跑，人工抽查音译归一是否正确、有无误并。

### Task 5：实跑、验证、落库（**全程人工检查点 + 副本先行 + .bak**）

- [ ] **Step 16**：在主库**副本**上：先跑 location_normalizer（写 location_norm）→ 再跑改造后的 s02a 去重（注意：去重读 projects.db 增量；若要对已入库数据重去重需另行设计，**默认 S5 只影响"今后新增"的去重逻辑**，对存量是否回溯合并需人工决定，见下"重大决策"）。
- [ ] **Step 17**：对比合并前后 canonical 条数；专门验证 **雪兰莪**、**达瓦德密/达旺德米** 两个已知 case 是否正确合并；抽查 20 对高相似但实为不同项目，确认**无误并**（误并比漏并更危险，不可逆地丢项目）。
- [ ] **Step 18**：人工确认无误后，`.bak` 备份主库 → 应用到主库 → 重生成 s03/前端 → 浏览器实测。
- [ ] **Step 19**：更新 docs/status + docs/changes 记录 S5。提交脚本/代码（DB 不入 git）。

---

## ⚠️ 重大决策（执行前必须人工拍板）

1. **存量回溯 vs 仅前向**：s02a 去重是对 projects.db 增量记录跑的。改了分组/锚点后，是否要**对已入库的 4373 个项目重新跑一遍去重以合并历史漏合并**？
   - 重跑存量 = 可能合并掉历史漏配（修雪兰莪类），但**风险高**（误并不可逆、改动手动修正过的记录、需大量 LLM 调用）。
   - 仅前向 = 只让今后新增更准，存量不动（安全，但雪兰莪类历史问题不修）。
   - **建议**：默认仅前向 + 地点归一化（location_norm 是安全的加列）；存量重去重作为**单独的、人工逐条复核**的任务，不在自动流程内。
2. **地点归一化是否回写覆盖 location**：默认**只加 `location_norm` 不覆盖 location**（可逆、安全）。

---

## 验证清单（DoD）
- [ ] (d) `_update_db` 不再误改 subsidiary；单测绿。
- [ ] (a) 分组去 stage + prompt 放宽；跨阶段同项目可成候选；单测绿。
- [ ] (b) 地点/容量硬锚点生效；单测绿。
- [ ] (c) location_normalizer 在副本上 preview 抽查音译归一正确；location_norm 回写。
- [ ] 已知 case（雪兰莪、达瓦德密）按决策处理；无误并。
- [ ] 主库改动前 .bak；副本先行；浏览器实测。
- [ ] docs/status 与 docs/changes 已更新。

## 风险
- **最高**：误并不可逆地丢项目；LLM 非确定性 + 花钱；改主库 + 改手动修正记录。
- 因此 Phase B 不可无人值守；Phase A 可自动。
