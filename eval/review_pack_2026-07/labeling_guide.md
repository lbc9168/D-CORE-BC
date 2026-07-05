# 金标准标注指南（review_pack_2026-07）

目的：为 s01b 提取器建立金标准评测集。你标注的是"理想提取结果"（按下述**目标口径**，不是复刻现有流水线行为）。

## 输入 / 输出

- 输入：`manifest/aNN.json`——`content` 为新闻原文全文；`pipeline_rows` 为现有流水线的提取行（仅供对照，**不可当作标准答案**，它可能过度收录/漏收/数值错）。
- 输出：`labels/aNN.json`，UTF-8，schema 见文末。每篇一个文件。

## 一、status（文章级，四选一）

| 值 | 判定 |
|---|---|
| `valid` | 能源范围内 + 有**具名**中方企业参与（含港澳台企业；华人海外企业不算） |
| `chinese_unnamed` | 能源范围内 + 明确有中方参与但**未具名**（只说"中国企业/中方投资者"） |
| `no_chinese` | 能源范围内 + 无中方参与（或仅"考虑/传闻"级别） |
| `irrelevant` | 非能源范围：纯评论/行业综述/市场报告、油气煤矿产开采、化工化肥冶金、房建轻轨基建、充换电站、**蓝氢/蓝氨（无明确绿电来源的制氢）**、纯金融新闻等 |

能源范围 = ①发电项目（光伏/风电/水电/生物质/地热/储能/核电/火电/综合能源）②新能源制造业工厂（组件/电池/硅料/装备，产出以 GW/年、GWh/年、万吨/年计）③输变电工程（线路/变电站/换流站/升压站/电网改造）。

## 二、projects（仅 status=valid 填；其余一律空数组）

**收录硬条件（两条都满足才录）**：
1. **可识别的具体单体项目**——有专名或"国家+地点+容量"可锁定的物理工程；
2. **具名中方参与**——该项目有至少一家具名中企（任何角色，含设备供应）。

**不录**（即使文章 valid）：国家/省级规划、批量招标计划结果（多家分标但无单体项目名/单体容量，如"缅甸1GW计划4联合体分30个地块"）、企业远期目标（"某公司2035年12GW目标"）、拍卖轮次公告、无具名中企的项目（文中其他项目照录）。
→ 被拒收的流水线项目写进 `rejected_ai_projects` 并给理由。

**拆分粒度**：不同物理项目各一条；同一项目明确分期（一期/二期）且文中分别有信息的分条；同一项目多个中标标段不拆。

## 三、字段口径

- `project_name`：中文专名，格式"国家+地名/专名+容量+类型"（如"沙特阿尔舒巴赫2.6GW光伏电站"）；照文即可，不必与流水线一致。
- `category`（12 选 1）：光伏 / 风电 / 水电 / 地热及余热 / 生物质 / 储能 / 综合能源 / 核电 / 火电 / 新能源制造业 / 输变电建设 / Unknown。
  - 垃圾发电→生物质；光热→光伏或综合能源；风+光/发电+储能→综合能源；仅输电/变电→输变电建设；工厂看产出单位（GW/年=工厂→新能源制造业）。
- `capacity_mw`：数字（MW），**只填发电装机**。换算：万千瓦×10；千瓦÷1000；GW×1000。**产能（GW/年、GWh、万吨/年）→ null**；输变电的输送容量→ null（写进 note）；储能项目功率 MW 可填，纯 GWh 能量→null+note。多期项目填该条对应期的容量。
- `project_stage`（9 选 1）：拟开发 / 签约 / 中标 / 开工 / 在建 / 竣工 / 投入运营 / 收购 / Unknown。取文章报道的**该项目当前事件**阶段。
- `location`：项目所在省/州级中文名（如"旁遮普省"）；文中只有国家→null。
- `total_investment_usd`：数字（**百万美元**）。亿美元×100；万美元÷100；欧元≈×1.08、英镑≈×1.25、人民币≈÷7.2 折美元后再换算；其他币种或不确定→null+note。**总投资口径**（融资额/合同额≠总投资，若只有合同额→null+note）。
- `financing_model`（6 选 1）：信贷融资 / 债券融资 / 股权融资 / 混合融资 / 其他 / Unknown。未提及融资→Unknown。
- `entities`：该项目所有**具名中方**实体。`name`=公司名照原文（全称优先）；`involvement_type`（9 选 1）：BOT\BOOT / EPC总承包 / 设计\施工服务方 / 股权投资方 / 设备供应商 / 一体化方案提供商 / 投融资+EPC / 融资方 / Unknown。非中方企业不录。

## 四、evidence（证据引句，关键）

对每个**非空**的 capacity_mw、total_investment_usd、project_stage，以及 entities（整体一条），给一条**逐字复制自 content 的连续子串**（≤120 字）。禁止改写、缩略或拼接——复核时会程序校验子串存在性。status 也给一条 `status_evidence`。

## 五、输出 JSON schema

```json
{
  "source_link": "…",
  "status": "valid",
  "status_reason": "一句话",
  "status_evidence": "原文引句",
  "projects": [
    {
      "project_name": "…",
      "matches_ai": "pipeline_rows 里对应的 project_name，无对应填 null",
      "category": "光伏",
      "capacity_mw": 100,
      "project_stage": "签约",
      "location": "…或 null",
      "total_investment_usd": 60,
      "financing_model": "Unknown",
      "entities": [{"name": "…", "involvement_type": "EPC总承包"}],
      "evidence": {"capacity_mw": "…", "total_investment_usd": "…", "project_stage": "…", "entities": "…"},
      "note": "可空"
    }
  ],
  "rejected_ai_projects": [{"name": "…", "reason": "…"}],
  "article_note": "可空；标注中拿不准的点写这里"
}
```

status ≠ valid 时 `projects: []`，流水线若抽出了项目全部列入 `rejected_ai_projects`。
