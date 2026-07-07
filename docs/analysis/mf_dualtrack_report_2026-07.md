# MF（新能源制造业）双轨对照报告

生成时间：2026-07-07 07:46 UTC　脚本：`scripts/oneoff/report_mf_dualtrack.py`　真库：`E:\Projects\BRI_RE_Database_v2\s02a_duplicate_merger\data\db\bri_re_database.db`（只读连接，本报告零写库）

## 摘要

- canonical MF 总数 **205**（未排除 **162** / 已排除 **43**）；non_project_records 新能源制造业记录 **57** 条
- 同名重叠（精确+rapidfuzz≥85，同国优先）：**5** 对（精确 2 / 模糊 3），未匹配 52 条
- 若 57 条整体迁移，命中阶段2 T2 硬收录边界关键词（锂矿/铁矿/电解铝/蓝氨/蓝氢）的有 **0** 条，将立刻触发排除

## 1. 双轨基本盘

### 1.1 canonical_projects（category=新能源制造业）

| 口径 | 数量 |
|---|---|
| 总数 | 205 |
| 未排除（excluded=0） | 162 |
| 已排除（excluded=1） | 43 |
| 其中 codex_T2_exclude:2026Q3 | 16 |

已排除的其余 reason 分组（非 T2）：

| excluded_reason | 数量 |
|---|---|
| 绿氢/电解类项目缺明确可再生能源种类或装机容量（用户规则2026-07-02） | 7 |
| 非能源产业项目（名称命中「锂矿」且无发电语义） | 3 |
| 非能源产业项目（名称命中「锂盐」且无发电语义） | 3 |
| 非能源产业项目（名称命中「氯碱」且无发电语义） | 2 |
| 非能源产业项目（名称命中「水泥」且无发电语义） | 2 |
| 非能源产业项目（名称命中「氧化铝」且无发电语义） | 1 |
| 非能源产业项目（名称命中「化工」且无发电语义） | 1 |
| 非能源产业项目（名称命中「镍矿」且无发电语义） | 1 |
| 非能源产业项目（名称命中「矿业」且无发电语义） | 1 |
| 非能源产业项目（名称命中「矿区」且无发电语义） | 1 |
| 非能源产业项目（名称命中「铜矿」且无发电语义） | 1 |
| 非能源产业项目（名称命中「MTBE」且无发电语义） | 1 |
| 非能源产业项目（名称命中「FPU」且无发电语义） | 1 |
| 非能源产业项目（名称命中「镍铁」且无发电语义） | 1 |
| 非能源产业项目（名称命中「化肥」且无发电语义） | 1 |

### 1.2 non_project_records（record_type=新能源制造业）字段完备度

共 57 条。字段非空计数：

| 字段 | 非空行数 | 占比 |
|---|---|---|
| 国家（country） | 57/57 | 100% |
| 国家 ISO（country_iso） | 57/57 | 100% |
| 实施主体（implementing_entity） | 55/57 | 96% |
| 发布日期（publish_date） | 57/57 | 100% |
| 信源（source_link） | 57/57 | 100% |

## 2. 同名重叠

匹配方法：精确名匹配（score=100）+ rapidfuzz `fuzz.ratio` 相似度 ≥ 85（同国 country_iso 优先于分数排序，每条 npr 记录最多取 1 个最佳候选）。

- 重叠对数：**5**（精确 2 / 模糊 3）
- 明细 CSV（不提交 git）：`E:\Projects\BRI_RE_Database_v2\.claude\worktrees\dcore_v2.2\supporting_docs\mf_dualtrack_overlap_20260707_074633.csv`
- 与计划旧数「37」的差异：旧数来自 2026-07-06 之前的口径（当时 canonical MF 未排除数为 178，计划文档 `docs/rules_and_plans/plan_data_quality_2026-07.md` §阶段3 记录"178/37"）；此后阶段2 T2 复核已将 MF 中 16 条判定 exclude（`codex_T2_exclude:2026Q3`），canonical MF 未排除数从 178 降至 162，故本次按当前库重算得到 5 对，与旧数口径不同（统计口径、库状态均已变化，非本脚本误差）。

### 抽样对照（前 5 对，按分数降序）

**样例 1**（exact，score=100.0，同国=是）
- npr[6] `埃及博达新能5GW光伏制造基地`（EGY）
- canonical `EGY-MF-0001` `埃及博达新能5GW光伏制造基地`（EGY，excluded=0）

**样例 2**（exact，score=100.0，同国=是）
- npr[800] `马来西亚双文丹组件工厂`（MYS）
- canonical `MYS-MF-0011` `马来西亚双文丹组件工厂`（MYS，excluded=0）

**样例 3**（fuzzy，score=93.3，同国=是）
- npr[31] `埃及博达新能4GW光伏制造基地`（EGY）
- canonical `EGY-MF-0001` `埃及博达新能5GW光伏制造基地`（EGY，excluded=0）

**样例 4**（fuzzy，score=86.7，同国=是）
- npr[694] `马来西亚吉打州圆柱形锂电池制造项目`（MYS）
- canonical `MYS-MF-0016` `马来西亚圆柱锂电池制造项目`（MYS，excluded=0）

**样例 5**（fuzzy，score=85.7，同国=是）
- npr[126] `英国明阳智能风电机组制造基地项目`（GBR）
- canonical `GBR-MF-0001` `英国风电机组制造基地项目`（GBR，excluded=0）

## 3. 边界案落点（引用阶段2 T2 规则，不重新裁决）

T2 硬收录边界规则原文（`codex_review/runs/2026Q3/guide_T2.md`）：
> **不应收录**：… 非能源资产：矿山开采（锂矿/铁矿等）、电解铝冶炼、蓝氨/蓝氢（天然气制氨，非可再生能源）。

对 57 条 non_project_records 的 project_name + capacity_details 做关键词扫描（锂矿/铁矿/电解铝/蓝氨/蓝氢），命中 **0** 条——若这些记录被迁移为 canonical，按 T2 规则将立刻触发 exclude。

（无命中）

## 4. 迁移口径选项

### A. 维持双轨不迁移
- 影响：0 新增/合并 canonical；0 工作量；MF 展示继续只用 canonical（162 条未排除），non_project_records 仅存档不进前端。
- 去重风险：无（不动结构）。代价：57 条一手信息（含 5 条与既有项目重叠、52 条独有）继续不可见于任何展示面。

### B. 仅同名重叠部分补挂信源
- 影响：0 新增 canonical；把 5 条重叠记录的 source_link/publish_date/实体信息补挂到对应既有 canonical MF 项目（作为该项目的补充信源/事件备注），不新建 id。
- 工作量：中等——需人工/半自动逐对确认 5 对匹配是否语义上确为同一项目（rapidfuzz 只是候选，非确定性判断，尤其 3 条模糊匹配需复核）；其中命中边界关键词的 0 条重叠记录对应的 canonical 项目本身需检查是否也该被 T2 规则排除。
- 去重风险：低（不新建 id，只加信源，不会产生新的重复项目）。
- 52 条未匹配的记录仍不处理，维持存档状态。

### C. 全量迁移（57 条升格 canonical）
- 路径：57 条中 5 条与既有 canonical 重叠，若不去重直接建 id 会产生重复项目——需先套用 s02a 的模糊匹配去重逻辑（或人工去重），仅对 52 条真正独有的记录新建 canonical id（涉及国家 BRA, CHL, EGY, ESP, FRA, HTI, IDN, ITA… 共 52 条）；重叠的 5 条应走方案 B 的补挂路径而非新建。
- 边界案拦截：全量 57 条中命中 T2 关键词 **0** 条，若走全量迁移，这些记录进 canonical 后应立即比照 T2 规则标记 excluded（不应作为可见项目升格）。
- 工作量：较大——id 分配需走 s02a 编号规则（`{ISO3}-MF-{NNNN}`，需查当前各国 MF 序号最大值续号），且需人工复核每条记录是否满足"具名中企参与+可识别单体项目"（T2 keep 门槛），不能全信 non_project_records 原始标注。
- 去重风险：高（若跳过去重步骤直接迁移全部 57 条，会与既有 5 条重叠项目产生重复）。

### 推荐
**推荐方案 B**（仅同名重叠部分补挂信源，未匹配的暂维持归档）：
- 理由：5 条重叠已有 canonical 归宿，补挂信源零去重风险、工作量可控，能立刻提升既有 MF 项目的信源完整度；
- 52 条独有记录不建议在本任务内草率全量迁移——其中命中 T2 边界关键词的 0 条大概率会被排除，其余独有记录是否满足"可识别单体项目"门槛需要逐条核实（不是数据结构问题，是内容判断问题），建议留给阶段2 T3（registry/去重复核）或后续人工 campaign 消化，而非本次批量升格；
- 方案 A（完全不迁移）过于保守，浪费了 5 条已确认重叠的可补充信源；方案 C（全量迁移）在未逐条核实独有记录质量前风险最高，且会把本该走 T2 判定的边界案（0 条）暴露为需要额外一轮排除处理的负担。

## 附录：关键数字复算 SQL

```sql
-- canonical MF 总数 / 未排除 / 已排除
SELECT COUNT(*) FROM canonical_projects WHERE category='新能源制造业';
SELECT COUNT(*) FROM canonical_projects WHERE category='新能源制造业' AND excluded=0;
SELECT COUNT(*) FROM canonical_projects WHERE category='新能源制造业' AND excluded=1;

-- 已排除 reason 分组
SELECT excluded_reason, COUNT(*) FROM canonical_projects WHERE category='新能源制造业' AND excluded=1 GROUP BY excluded_reason ORDER BY COUNT(*) DESC;

-- non_project_records 新能源制造业总数与字段完备度
SELECT COUNT(*) FROM non_project_records WHERE record_type='新能源制造业';
SELECT COUNT(*) FROM non_project_records WHERE record_type='新能源制造业' AND implementing_entity IS NOT NULL AND TRIM(implementing_entity)<>'';

-- 边界关键词扫描（示例：锂矿）
SELECT id, project_name FROM non_project_records WHERE record_type='新能源制造业' AND (project_name LIKE '%锂矿%' OR capacity_details LIKE '%锂矿%');
```

同名重叠对数为脚本计算结果（精确名匹配 SQL 可直接对拍，模糊匹配需跑本脚本`scripts/oneoff/report_mf_dualtrack.py` 复现 rapidfuzz 计算）：
```sql
-- 精确名匹配（不含模糊）
SELECT n.id AS npr_id, n.project_name, c.id AS canonical_id
FROM non_project_records n JOIN canonical_projects c
  ON c.project_name = n.project_name AND c.category='新能源制造业'
WHERE n.record_type='新能源制造业';
```
