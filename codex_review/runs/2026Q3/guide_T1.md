# T1 分拣指南：漏合并候选对判定

## 任务说明

每个 item 是一对疑似重复的 canonical 项目（a/b 各带全部事件摘要、容量、日期、信源链接、中企名单）。你的任务是判断它们是否为**同一个物理项目**。

**"同一个物理项目"的定义**：同一地点、同一资产的同一实体。注意：
- 同项目**不同期/不同标段**（如 Phase I 与 Phase II）算**不同**项目，除非上下文表明是同一资产的重复报道。
- 扩建期与原始项目也算**不同**项目，除非明确是报道同一物理装置。
- 仅名称相似不足以判定为同一项目，须结合地点、容量、日期、实体等综合判断。

## 判定依据

- 证据**只能来自** item 自带的上下文（project_name、events、entity_names、source_links）以及（如可用的）联网核实原文。
- `evidence_quote` 字段**必须逐字引用** item 内文字或来源原文，不得改写或概括。

## survivor 选择规则

当 `same_project=true` 时：
- `survivor_id` 必填，且**必须是 a 或 b 的 canonical_id 之一**。
- 选**事件更多、信息更全**的一侧作为 survivor。
- 两侧信息量相当时，选 **canonical_id 字典序更小**的一侧。

## confidence 三档

| confidence | 适用场景 |
|---|---|
| `high` | 证据明确，地点/容量/实体高度一致，无歧义 |
| `medium` | 有合理推断，但存在信息不全或小出入 |
| `low` | 证据薄弱，存在明显不确定性 |

**拿不准时**：`same_project` 按更保守方向填 `false`，`confidence` 填 `low`，在 `reason` 中说明不确定来源。

## 输出格式

将判定结果写入 `verdicts_T1.json`，信封格式如下：

```json
{
  "task": "T1",
  "quarter": "2026Q3",
  "verdicts": [
    {
      "pair_id": "a3f2c1d4e5b6789012345678abcdef01abcdef01",
      "same_project": true,
      "confidence": "high",
      "evidence_quote": "a.project_name='越南宁顺50MW光伏项目'; b.project_name='越南宁顺省50MW光伏项目'; a.events[0].publish_date='2023-01-01'; b.events[0].publish_date='2023-01-15'; 容量均为50MW，实体均为'中国电建集团'",
      "survivor_id": "VNM-PV-0001",
      "reason": "两侧项目名称、容量、地点（宁顺省）、实施实体高度一致，发布日期相近，判定为同一物理项目的重复录入。选 VNM-PV-0001 为 survivor（事件更多）。"
    },
    {
      "pair_id": "b4e5f6a7b8c9012345678901bcdef012bcdef012",
      "same_project": false,
      "confidence": "medium",
      "evidence_quote": "a.project_name='巴基斯坦卡拉奇100MW风电一期'; b.project_name='巴基斯坦卡拉奇100MW风电二期'; a.events[0].project_stage='完工'; b.events[0].project_stage='建设中'",
      "survivor_id": null,
      "reason": "名称与容量近似，但一期已完工、二期仍在建，项目阶段明显不同，判定为不同物理项目（不同建设期）。"
    }
  ]
}
```

字段说明（与 `schemas.T1Verdict` 逐字对齐）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `pair_id` | str | 与 queue item 的 pair_id 相同 |
| `same_project` | bool | 是否同一物理项目 |
| `confidence` | "high"\|"medium"\|"low" | 判定置信度 |
| `evidence_quote` | str（非空） | 逐字引用 item 内文字或来源原文 |
| `survivor_id` | str\|null | same_project=true 时必填；false 时必须为 null |
| `reason` | str（非空） | 判定理由 |
