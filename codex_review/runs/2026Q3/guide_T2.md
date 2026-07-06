# T2 分拣指南：范围复核

## 任务说明

每个 item 是一个疑似**不该收录在库**（或需降级处理）的项目，带有触发原因、项目名称、事件摘要、中企名单等上下文。你的任务是判断该项目是否应：

- **keep**：保留在库（继续作为 canonical 项目）
- **exclude**：排除出库（设 excluded=1）
- **downgrade_non_project**：降级为"非单体项目"标记

## 硬收录边界（共识规则）

**应收录**：具名中企参与 + 可识别的单体项目（含招标/中标/前期阶段——拟开发或前期阶段不是排除理由，库策略是"保留入库不展示"）。

**不应收录**：
- 批量招标计划（如"第X轮拍卖"中的一批）、国家/省级规划群、企业远期装机目标
- 非能源资产：矿山开采（锂矿/铁矿等）、电解铝冶炼、蓝氨/蓝氢（天然气制氨，非可再生能源）

**注意区分**：
- "绿电制绿氢/绿氨"属可再生能源范畴 → 可 keep
- "蓝氨"= 天然气制氨 → exclude
- 外国业主出现在 developer 字段不是排除理由（项目可能有中企承建/融资）

## 三档判定

| 判定 | 条件 |
|---|---|
| `keep` | 确属可识别单体项目且有中企实质参与；不确定时也选 keep（保守策略：不轻易出库） |
| `exclude` | 无中企参与，或属非能源资产（矿山/冶炼/化工），或经查实为境外非可再生能源项目 |
| `downgrade_non_project` | 内容真实但不是单体项目：省级规划群、批量拍卖轮次、企业装机目标声明 |

**拿不准 → keep + reason 说明**（保守原则：不轻易出库）。

## 判定依据

- 证据**只能来自** item 自带上下文（project_name、audit_note、events、entity_names）以及联网核实原文。
- `evidence_quote` 字段**必须逐字引用** item 内文字或来源原文，不得改写或概括。

## 输出格式

将判定结果写入 `verdicts_T2.json`，信封格式如下（与 `schemas.T2Verdict` 逐字对齐）：

```json
{
  "task": "T2",
  "quarter": "2026Q3",
  "verdicts": [
    {
      "project_id": "IND-PV-0001",
      "action": "keep",
      "reason": "经查实为印度单体太阳能电站，有具名中企（中国电建集团）参与建设，属库收录范围。",
      "evidence_quote": "project_name='印度某地100MW光伏项目'; entity_names=['中国电建集团']; events[0].project_stage='建设中'; events[0].project_summary='中国电建承建，预计2024年投产'"
    },
    {
      "project_id": "ISR-HB-0001",
      "action": "exclude",
      "reason": "项目实质为蓝氨（天然气制氨）工厂，非可再生能源资产，应排除。",
      "evidence_quote": "project_name='以色列蓝氨项目'; events[0].project_summary='天然气制氨，年产量20万吨'"
    }
  ]
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `project_id` | str | 与 queue item 的 project_id 相同 |
| `action` | "keep"\|"exclude"\|"downgrade_non_project" | 判定结果 |
| `reason` | str（非空） | 判定理由 |
| `evidence_quote` | str（非空） | 逐字引用 item 内文字或来源原文 |
