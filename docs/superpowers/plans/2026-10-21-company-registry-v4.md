# Company Registry V4：V3 主体 ID + 四级树层级落地计划

日期：2026-10-21
状态：P0/P1 已完成（2026-10-21），P2 待数据库回填
工作区：`E:\Projects\BRI_RE_Database_v2\.agents\worktrees\company-registry-v4`
分支：`company-registry-v4`
基线：`7db5875`（V3 运行时切换已合入 main）

## 0. 一句话目标

在不改变任何现有项目/事件/实体 ID 的前提下，把 `company_hierarchy_tree.json` 的真实四级母子层级接入运行时，替换 V3 中“二级子集团仍独立成组”的扁平口径；同时保留 V3 的 563 个主体 ID 作为唯一 ID 权威。

## 1. 当前事实（开始 P0 前）

- V2 → V3 的 563 个 `entity_id` 完全一致，名称→ID 映射完全一致；V3 只补父指针、纠正农行央企标记，没有 ID churn。
- V3 已接入运行时：`company_hierarchy_v3.json` 是 S01B/S02B/eval/T3 的默认登记册。
- V3 仍是“顶层 group + 扁平 subsidiaries”：中电工程、CMEC、阿特斯等仍是独立顶层 group，只是多了 `parent_entity_id`；`implementing_entity` 不会上卷。
- tree：439 个节点、438 个唯一 ID；与 V3 仅 160 个 ID 重合；151 个节点与 V3 同名但 ID 不同；127 个节点在 V3 中找不到精确同名；403 个 V3 ID 不在树中。
- tree 的 ID 不是 `stable_entity_id(name)`，不能作为权威 ID，只能作为节点标签。
- 当前数据库、导出和前端仍是 V3 口径；本轮 P0 不写数据库。

## 2. 设计原则

1. **ID 权威唯一**：V3 的 563 个 `entity_id` 是唯一合法 ID；tree 的 ID 只写审计报告，不写库。
2. **层级权威分离**：tree 决定 `parent_entity_id` / `level` / `ownership_type` / 树内 `is_central_soe`；V3 决定主体身份和全部历史 ID。
3. **不自动合并不同 ID**：同名但多 ID、别名冲突、跨集团歧义一律进人工清单，不按分数或字典序选一个。
4. **V3 覆盖不丢失**：tree 未收录的 V3 主体继续保留；如果其 V3 祖先被 tree 重新挂接，沿新祖先重算 root，但不改自身 ID。
5. **新节点先隔离**：tree 新增的 127 个名称先标 `provisional_new`，P0 不进入运行时视图；人工确认后才分配正式 ID。
6. **历史快照可追溯**：`chinese_entities.raw_entity_name`、`entity_evidence_json`、`entity_id` 不被覆盖；只回填派生字段并留审计。
7. **默认 dry-run**：所有数据库写操作先出 diff，备份、单事务、幂等、ID/外键/完整性校验通过后才 apply。
8. **统计口径显式**：`implementing_entity` 继续表示顶层集团；`subsidiary_name`/`entity_name` 表示实际法人；新增 `path`/`level` 用于详情展示。

## 3. 目标产物

### 3.1 `supporting_docs/company_registry_v4.json`（主数据）

一行一个唯一主体，包含：

```json
{
  "schema_version": "4.0",
  "generated_on": "2026-10-21",
  "sources": {"v3_sha256": "...", "tree_sha256": "..."},
  "entities": [
    {
      "entity_id": "ent_3d8eb7a5fea850b4bb28",
      "name_cn": "中国电力工程顾问集团有限公司",
      "name_en": "China Power Engineering Consulting Group Co., Ltd.",
      "name_en_abbr": "CPECC",
      "aliases": ["中电工程", "中国电力工程顾问集团"],
      "level": 2,
      "parent_entity_id": "ent_405cdb1433d45008b11d",
      "root_entity_id": "ent_405cdb1433d45008b11d",
      "is_central_soe": true,
      "ownership_type": "中央企业全资二级子集团",
      "sources": [{"authority": "...", "url": "..."}],
      "origin": "v3+tree",
      "status": "matched",
      "tree_entity_id": "ent_26859315147bc0532065",
      "notes": []
    }
  ]
}
```

`status` 取值：

- `matched`：tree 节点按名称/别名唯一映射到 V3 ID；
- `v3_only`：V3 有、tree 无精确节点，沿用 V3 关系；
- `provisional_new`：tree 有、V3 无，暂不进入运行时；
- `ambiguous`：同名/别名命中多个 V3 ID，待裁决；
- `parent_unresolved`：自身已匹配，但父节点仍是 provisional/ambiguous，暂按 V3 祖先挂接。

### 3.2 `supporting_docs/company_hierarchy_v4.json`（兼容视图）

保持 `schema_version=2` 的 `groups → subsidiaries` 结构，供现有 `entity_normalize` / `company_lookup` 直接读取：

- `groups` = 所有 root 节点（L1）；
- 每个 group 的 `subsidiaries` 是整棵子树的扁平列表，条目带 `parent_entity_id`、`level`；
- 只包含 `matched` / `v3_only` / `parent_unresolved` 的已确认主体；
- `provisional_new` 与 `ambiguous` 不进入该文件，避免运行时意外生成新主体；
- 每个条目保留 `name_cn`、`legal_full_name_cn`、`aliases`、`context_required_names`、`is_central_soe`。

### 3.3 `supporting_docs/company_registry_v4_changes.json`（审计）

至少包含：

- 文件 SHA256、生成时间、统计；
- `id_crosswalk`：tree_id → V3 id（151 条同名不同 ID）；
- `ambiguous`：多候选、候选 ID、名称、层级、父节点；
- `new_nodes`：127 个 tree-only 名称、建议 provisional ID、父节点；
- `v3_only`：403 个 V3 ID 的保留与重挂接结果；
- `alias_conflicts`：同一别名指向多个主体；
- `reparented`：V3 → V4 的 parent/root 变化（重点看中电工程、CMEC、阿特斯、葛洲坝等）；
- `metrics`：匹配率、根节点数、各 level 数量、未决数。

### 3.4 P0 评审清单

`output/company-registry-v4/p0/review.csv`（gitignored），一行一个需人工看的 tree 节点：

`tree_id, tree_name, level, tree_parent, match_type, candidate_ids, candidate_names, decision_needed, note`

P0 完成后把关键数字写进 `docs/changes/2026-W43.md` 或后续 W44 记录。

## 4. P0 对账算法

### 4.1 归一化

- `NFKC` + 空白折叠 + `casefold`；
- 不做模糊音译、不做包含匹配、不做“去尾缀”自动合并；
- 去尾缀只作为**候选提示**，必须人工确认，不自动写入。

### 4.2 tree 节点 → V3 ID

按顺序：

1. `name_cn` 精确 key 命中 V3 的 `legal_full_name_cn` / `canonical_name_cn` / `name_cn` / `aliases`；
2. 命中唯一 ID → `matched`；
3. 命中多个 ID → `ambiguous`，记录全部候选；
4. 未命中 → 用 tree `aliases` 再查一轮；
5. 仍未命中 → `provisional_new`，不自动分配正式 ID；
6. tree 自身 ID 只写 `tree_entity_id`，不参与匹配。

### 4.3 V3 主体 → tree 层级

1. 以 V3 的 `entity_id` 建主记录；
2. 如果该 ID 被 tree 节点匹配：采用 tree 的 `level`、`parent`、`ownership_type`、`is_central_soe`、`sources`；
3. 如果未被 tree 匹配：保留 V3 的 parent/group；若其 V3 group 被 tree 重新挂接，则 root 沿新祖先计算；
4. 计算 root：沿 `parent_entity_id` 上溯到无父节点；遇到 provisional parent 时，标记 `parent_unresolved` 并暂挂到最近一个已确认祖先（或 V3 group）；
5. 校验无环、父节点存在、每个 V3 ID 恰好一条主记录。

### 4.4 别名与上下文

- 合并 V3 与 tree 的 aliases；同一 alias 指向不同 ID → 记 `alias_conflicts`，运行时该 alias 不进入全局索引；
- tree 没有 `context_required_names` 时，沿用 V3 的；新增易混简称（如“中设集团”“中电工程”）要人工判断；
- `group_only_aliases` / `disputed_aliases` 保持不参与全局法律主体映射。

### 4.5 根与层级

- L1 = `parent_entity_id is None`；
- 中电工程、CMEC、阿特斯在 V4 中应分别落到能建、国机、Canadian Solar 的子节点；
- 其 V3 下属未入树的子公司随祖先重挂；
- 输出 `level` 分布与每个 root 的直接子节点数，人工抽查 10 个关键集团。

### 4.6 层级口径：集团总部 vs 上市股份公司

V4 的 `level` 按**最终控股集团**定义，不按名称相似度定义：

| 节点 | 层级 | 说明 |
|---|---|---|
| 招商局集团有限公司 | L1 | 集团总部；V3 尚无此组，P0 标 provisional |
| 中国能源建设集团有限公司 | L1 | 集团总部；V3 已有 |
| 中国能源建设股份有限公司 | L2 | 集团控制的 A/H 上市核心平台；V3 缺失 |
| 中国电力建设集团有限公司 | L1 | 集团总部；V3 已有 |
| 中国电力建设股份有限公司 | L2 | 集团控制的 A 股上市核心平台；V3 缺失 |
| 中国交通建设集团有限公司 | L1 | 集团总部；V3 已有 |
| 中国交通建设股份有限公司 | L2 | 集团控制的 A/H 上市核心平台；V3 缺失 |

常见混淆是“中国能建/中国电建”既可指集团、也可指上市股份公司。V4 采用法律层级：集团总部 L1，上市股份公司 L2，工程局/设计院 L3，海外属地机构 L4。若研究口径希望把上市股份公司作为汇报主体，那是 `implementing_entity` 的产品选择，不应改写法律层级。

P0 的 37 条 `parent_unresolved` 不是关系不明，而是 **V3 缺了三个股份公司节点**：

| provisional 父节点 | 影响子节点数 |
|---|---:|
| 中国电力建设股份有限公司 | 24 |
| 中国能源建设股份有限公司 | 10 |
| 中国交通建设股份有限公司 | 3 |

对比：中国建筑股份有限公司在 V3 中已有 ID，所以中建一局/三局/四局/五局/西部建设能直接挂上。P1 只要把这三个股份公司补为正式主体（或明确映射），这 37 条即可原样挂接，不需要重匹配子节点。

## 5. 阶段计划与验收

### P0 编译器与对账（本轮，只读）

交付：`build_registry_v4.py`、三份 JSON、review.csv、P0 报告。

验收：

- V3 563 个 ID 一个不少，且没有重复主记录；
- 每个 tree 节点都有分类（matched/ambiguous/provisional_new）；
- `parent` 全部可解析、无环；
- `company_hierarchy_v4.json` 可被现有 `_GroupRecord` 校验通过；
- 没有任何数据库写入；
- 151 个同名不同 ID 和 127 个未匹配节点全部进 review.csv；
- 关键案例：中电工程→能建、CMEC→国机、阿特斯→Canadian Solar、葛洲坝→能建。

**P0 结果（2026-10-21）：**

- 产物：`supporting_docs/company_registry_v4.json`（688 主体）、`company_hierarchy_v4.json`（150 组 / 419 子 / 569 行）、`company_registry_v4_changes.json`、`company_registry_v4_additions.json`、`output/company-registry-v4/p0/review.csv`（272 行）。
- V3 563 个 ID 全部保留；`company_hierarchy_v4.json` 经 S01B `_GroupRecord` 与 `_build_identity_index` 校验：569 个主体、0 个 exact 歧义。
- 438 个 tree 节点：319 个匹配（含 6 个审批新增）、119 个 provisional、0 个 ambiguous。153 个同名但 tree ID 不同，已进 crosswalk。
- 6 个审批新增主体：中国能源建设股份有限公司、中国电力建设股份有限公司、中国交通建设股份有限公司、招商局集团有限公司、招商局港口控股有限公司、招商局轮船有限公司；均使用 `stable_entity_id(name)`，parent 已校验。
- 53 个主体发生 parent 变化：中电工程→能建、CMEC→国机、阿特斯→Canadian Solar、葛洲坝→能建，以及 37 个能建/电建/中交工程局与设计院→对应上市股份公司。
- 原先 37 个 `parent_unresolved` 全部归零；没有写数据库；`provisional_new` 未进入运行时视图。
- 剩余 119 个 provisional：38 个 L2、71 个 L3、10 个 L4；下一步按“真实法人 / 品牌泛称 / 只做展示”逐类裁决。

### P1 运行时切换（已完成）

- `entity_normalize` / `company_lookup` / S1 hash / eval / T3 默认路径切到 `company_hierarchy_v4.json`；
- 保留 `COMPANY_HIERARCHY=company_hierarchy_v3.json` 回退；实测 v4 下 CMEC→国机、v3 下 CMEC→自身；
- `EntityResolution` 的 `parent_entity_id` 已可用；`parent_name` / `level` / `path` 的展示字段留到 P3；
- 验证：S01B 全量 1012 passed、1 个环境相关旧失败；S02B 55 passed；实体/S1 定向 91 passed；V4 结构契约 5 passed；
- 6 个审批新增、4 条 override 生效；119 个 provisional 不进入运行时；不写库。

### P2 数据库回填（P1 验收后）

- 新脚本 `scripts/migrate_entity_hierarchy_v4.py`：默认 dry-run，按 `entity_id` 重算 `group_entity_id` / `implementing_entity` / `entity_name` / `is_central_soe`；
- 无 `entity_id` 的行按名称唯一匹配；歧义留报告；
- apply：`.bak` + SHA256 + 行数断言 + 单事务 + 审计 CSV + 幂等自检；
- 验证：实体 ID 集合、事件/项目 ID 集合、外键、完整性不变；
- 之后跑 `en_backfill` → S03 → S04。

### P3 导出与网页（P2 验收后）

- `all_entities` 增加 `entity_id`、`entity_name`、`entity_name_en`、`group_id`、`parent_name`、`level`、`path`、`ownership_type`；
- `primary_entity` 仍是顶层集团，新增 `primary_entity_id` / `primary_entity_path`；
- `EntityList` 显示“集团 → 父级 → 实际法人”路径 + SOE 徽章；
- `stats_entity` 暂不改口径；后续可选“集团/法人”切换；
- 数据字典补列；
- 浏览器验收一个多级项目详情、一个 TX/MF 项目、一个纯集团项目。

### P4 部署（单独批准）

- 构建 `dist`，先本地 HTTP 验证；
- 公司主站与 GitHub Pages 按既有 `DEPLOY.md` 流程；
- 线上 metadata、CSV、JSON 与本地一致。

## 6. 数据库回填细节

### 6.1 只改派生字段

`chinese_entities` 现有列足够：

- `entity_name`：规范法人名；
- `group_entity_id`：root（L1）ID；
- `implementing_entity`：root 规范名；
- `implementing_entity_en`：名称变化时置 NULL，交给 en_backfill；
- `is_central_soe`：沿 root 继承；
- `subsidiary_name`：保留原文/别名，不强行改成规范名；展示层优先用 `entity_name`。

不动：`event_id`、`entity_id`、`raw_entity_name`、`involvement_type`、金额字段、证据 JSON。

### 6.2 差异报告

列：`row_id, event_id, entity_id, old/new implementing_entity, old/new group_entity_id, old/new entity_name, old/new is_central_soe, reason, hierarchy_path`。

先按 `entity_id` 聚合出“每个主体影响多少行”，让用户先看集团合并的规模，再看逐行。

### 6.3 验收

- `SELECT COUNT(*)` 不变；
- `SELECT DISTINCT entity_id` 不变；
- `SELECT id FROM project_events` / `canonical_projects` 集合不变；
- `PRAGMA foreign_key_check` 为空；`integrity_check=ok`；
- 二次运行 0 变更；
- 备份文件存在且可读。

## 7. 网页展示细节

### 7.1 导出字段

```json
{
  "name": "中国能源建设集团有限公司",
  "name_en": "China Energy Engineering Group",
  "entity_id": "ent_3d8eb7a5fea850b4bb28",
  "entity_name": "中国电力工程顾问集团有限公司",
  "entity_name_en": "China Power Engineering Consulting Group",
  "group_id": "ent_405cdb1433d45008b11d",
  "parent_name": "中国能源建设集团有限公司",
  "level": 2,
  "path": ["中国能源建设集团有限公司", "中国电力工程顾问集团有限公司"],
  "ownership_type": "中央企业全资二级子集团",
  "subsidiary": "中电工程",
  "involvement_type": "EPC总承包",
  "is_central_soe": 1
}
```

### 7.2 展示规则

- 集团名保持 `name`，实际法人优先 `entity_name`，`subsidiary` 只作兼容/原文别名；
- 路径用 `›` 或 `→` 分隔，最多显示 3 级，超出折叠；
- L1 主体不重复显示路径；
- SOE 徽章以 root 继承值为准；
- 表格主实体列不变；详情 Drawer 增加路径；
- 统计图仍按集团聚合，避免同一项目被多个法人重复计数。

## 8. 测试矩阵

| 层级 | 用例 |
|---|---|
| 编译 | V3 ID 全保留、tree 节点分类守恒、无环、父指针存在 |
| 解析 | 中电工程、CMEC、阿特斯、葛洲坝、农行、上海电力、三峡发展（马） |
| 回归 | `test_entity_normalize*`、`test_company_lookup`、`test_hierarchy_v3` |
| 数据库 | dry-run 差异行数、备份、事务、幂等、FK/完整性 |
| 导出 | projects/TX/MF/events 行数、字段、CSV 回读 |
| 前端 | typecheck、build、项目详情路径、筛选、搜索、CSV 下载 |
| 端到端 | 一份真实项目从 S01B 解析到 S03 导出的 entity path |

## 9. 风险与回滚

| 风险 | 处理 |
|---|---|
| tree ID 被误当权威 | P0 强制丢弃 tree ID，只写 `tree_entity_id` |
| 同名不同 ID 被自动合并 | 多候选一律 `ambiguous`，人工裁决 |
| V3-only 主体丢失 | 563 ID 全量断言，缺失即失败 |
| 集团合并导致统计跳变 | P2 先出按主体聚合的 diff，用户确认后再 apply |
| 历史行被错误改写 | 只改派生字段，保留 raw/evidence；备份 + 审计 |
| 运行时意外新主体 | provisional 节点不进入 `company_hierarchy_v4.json` |
| 回滚 | 代码回退到 `7db5875`；DB 用 `.bak`；导出可重跑 |

## 10. 决策记录

- 2026-10-21：采用“V3 ID 权威 + tree 层级权威”，不直接使用 tree ID。
- 2026-10-21：`implementing_entity` 继续表示 L1 集团；实际法人用 `entity_name`。
- 2026-10-21：历史数据统一按当前层级回填；不建历史有效期模型。
- 2026-10-21：P0 不写数据库；P2 apply 前必须人工看 diff。

## 11. P0 执行清单

1. 写 `supporting_docs/build_registry_v4.py`；
2. 加载 V3/tree，按第 4 节匹配；
3. 生成三份 JSON + review.csv；
4. 断言 563 ID、无环、父指针、schema v2 可加载；
5. 抽查 10 个关键集团；
6. 把统计写入 `docs/changes/2026-W43.md` 或新 W44；
7. 提交 P0 产物，不合并到 main，等用户看 review.csv 后再决定 P1。
