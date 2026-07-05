# 母子公司核实指南（子代理执行）

## 任务
你要核实一批中国企业名称，确定每个名字在企业集团层级中的位置。产出用于扩充 `company_hierarchy.json`（一张**只含中国公司**的母子公司层级表）。**每条归属结论必须有网络来源支撑，不允许只凭记忆下结论。**

## 输入
读你被指定的输入文件 `hierarchy_verify_2026-07-05/in/<batch>.json`：
- `registry_groups`：**已在册**的 19 个顶层/次级集团规范名。你的首要任务是判断候选名是否是这些已有集团的别名或子公司——如果是，归到它，**不要新建**。
- `candidates`：`[{name, freq}]` 待核实名单。

## 对每个候选名，联网核实并分类

用 WebSearch 查证（公司官网、年报、天眼查/企查查、维基、权威新闻）。判断它属于哪一类：

| classification | 含义 |
|---|---|
| `alias_of_existing` | 是某已在册集团的**简称/写法变体/海外分支**（如"中国能建国际集团"="中国能源建设集团"的国际业务口径） |
| `subsidiary_of_existing` | 是某已在册集团旗下**独立子公司**（如"中国港湾工程"→中国交建，但中国交建本身不在册则不算这类） |
| `new_group` | 是一个**未在册的独立中国集团**（顶层母公司），应新建（如金风科技、隆基绿能、中国建筑集团） |
| `subsidiary_of_new` | 是本批某个 new_group 的子公司（如"中建三局"→新建的"中国建筑集团"） |
| `not_chinese` | 实际是外国企业/机构（翻译名混入），**不进中国层级表** |
| `not_company` | 不是企业（政府部门、笼统占位等） |
| `uncertain` | 查证不足，标 low 置信度并说明 |

## 输出
写 `hierarchy_verify_2026-07-05/out/<batch>.json`，UTF-8：
```json
{
  "batch": "<batch名>",
  "results": [
    {
      "raw_name": "中国能建国际集团",
      "freq": 1800,
      "classification": "alias_of_existing",
      "canonical_group_cn": "中国能源建设集团有限公司",
      "legal_full_name_cn": "中国能源建设集团国际工程有限公司 或 null（查不到留 null）",
      "parent_cn": "中国能源建设集团有限公司",
      "is_central_soe": true,
      "source_urls": ["https://...","https://..."],
      "as_of": "2026-07-05",
      "confidence": "high",
      "notes": "一句话依据"
    }
  ]
}
```

## 规则
- `is_central_soe`：是否国务院国资委直接监管的中央企业（true/false）。地方国企、上市民企均为 false。存疑标 low。
- `canonical_group_cn`：该名字最终归属的**顶层集团**规范名（全称）。new_group 时填它自己的规范全称。
- 每条至少 1 个 source_url；高置信度建议 2 个。查不到可靠来源 → confidence=low + notes 说明。
- `as_of` 一律填 "2026-07-05"。
- 注意易混点：① "青山"系（青山实业/控股/集团）是浙江**民营**企业 Tsingshan，是中国公司；② 港股上市的中资/中资背景企业（如协合新能源、光大环境）算中国公司；③ 各国"国家电力公司/电网公司/能源部"是外国机构 → not_chinese；④ 合资控股平台（如某某巴西公司、某某老挝公司）通常是已在册集团的海外 SPV → subsidiary/alias_of_existing。
- 写完文件后自己 json.load 校验一遍可解析。
- 最终回复给主代理一行摘要即可：`<batch> 完成 N 条：new_group X / alias Y / sub Z / not_chinese W / uncertain U`。
