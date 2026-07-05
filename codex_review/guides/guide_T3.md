# T3 实体归属补录分拣指南

## 任务

你要分拣一批在实体解析阶段未能匹配到已知集团的原始实体名（来自 `queue_T3.json`），确定每个名字在企业集团层级中的位置。产出写入 `verdicts_T3.json`，用于扩充集团层级表。

**每条归属结论必须有网络来源支撑，不允许只凭记忆下结论。**

判过的 raw_name 会记入台账，同一 raw_name 不会再次入队，无需重复判定。

## 输入格式

读 `queue_T3.json`，结构如下：

```json
{
  "task": "T3",
  "quarter": "2026Q3",
  "generated_at": "...",
  "items": [
    {
      "item_id": "<sha1>",
      "raw_name": "某企业名称",
      "freq": 42,
      "registry_groups": ["已在册集团A", "已在册集团B", "..."]
    }
  ]
}
```

- `raw_name`：待判定的原始实体名（判定单元）。
- `freq`：该名称在数据库中出现的频次，高频优先判定。
- `registry_groups`：当前已在册的所有顶层集团规范名列表（全量），你的首要任务是判断候选名是否归属其中某个——如果是，归到它，**不要新建**。

## 对每个名字，联网核实并分类

用 WebSearch 查证（公司官网、年报、天眼查/企查查、维基百科、权威新闻）。判断它属于哪一类：

| classification | 含义 |
|---|---|
| `alias_of_existing` | 是某已在册集团的**简称/写法变体/海外分支** |
| `subsidiary_of_existing` | 是某已在册集团旗下**独立子公司** |
| `new_group` | 是一个**未在册的独立中国集团**（顶层母公司），应新建 |
| `subsidiary_of_new` | 是本批某个 new_group 的子公司 |
| `not_chinese` | 实际是外国企业/机构，不进中国层级表 |
| `not_company` | 不是企业（政府部门、笼统占位等） |
| `uncertain` | 查证不足，标 low 置信度并说明 |

## 输出格式

写 `verdicts_T3.json`，UTF-8，结构如下：

```json
{
  "task": "T3",
  "quarter": "<与 queue_T3.json 一致>",
  "verdicts": [
    {
      "raw_name": "某企业名称",
      "classification": "alias_of_existing",
      "canonical_group_cn": "中国能源建设集团有限公司",
      "legal_full_name_cn": "中国能建国际工程有限公司",
      "parent_cn": "中国能源建设集团有限公司",
      "is_central_soe": true,
      "source_urls": ["https://example.com/source1", "https://example.com/source2"],
      "confidence": "high",
      "notes": "一句话依据：官网及年报确认为中国能建旗下国际工程子公司"
    }
  ]
}
```

### 完整 verdict 示例

```json
{
  "raw_name": "中国能建国际集团",
  "classification": "alias_of_existing",
  "canonical_group_cn": "中国能源建设集团有限公司",
  "legal_full_name_cn": null,
  "parent_cn": "中国能源建设集团有限公司",
  "is_central_soe": true,
  "source_urls": ["https://www.ceec.net.cn/", "https://www.ndrc.gov.cn/..."],
  "confidence": "high",
  "notes": "中国能建国际集团系中国能源建设集团有限公司国际业务口径简称，官网证实。"
}
```

### 字段约束

- `raw_name`：原样照抄 queue 中的 raw_name，不要修改。
- `classification`：七选一，见上表。
- `canonical_group_cn`：归属的**顶层集团**规范全称。`new_group` 时填该名字自己的规范全称；`not_chinese`/`not_company`/`uncertain` 时可为 `null`。
- `legal_full_name_cn`：工商注册全称（查不到留 `null`）。
- `parent_cn`：直接上级公司名称（`new_group`/`not_chinese`/`not_company`/`uncertain` 时可为 `null`）。
- `is_central_soe`：是否国务院国资委直接监管的中央企业（`true`/`false`/`null`）。地方国企、上市民企均为 `false`。存疑标 `null` + `confidence=low`。
- `source_urls`：**`alias_of_existing`/`subsidiary_of_existing`/`new_group`/`subsidiary_of_new` 分类必须至少 1 条**；其他分类可为空列表。
- `confidence`：`"high"` / `"medium"` / `"low"` 三档。
- `notes`：一句话依据，禁止为空。

## 判定规则与易混点

1. **归属优先于新建**：若候选名可以归到 `registry_groups` 中任一集团，优先归属，不要新建。
2. **`is_central_soe` 判定**：仅国务院国资委直接监管的"央企"为 `true`，地方国企（各省市国资委）、上市民企均为 `false`。存疑时标 `null`。
3. **易混点**：
   - "青山"系（青山实业/控股/集团）是浙江**民营**企业 Tsingshan，是中国公司（`not_chinese=false`）。
   - 港股上市的中资/中资背景企业（如协合新能源、光大环境）算中国公司。
   - 各国"国家电力公司/电网公司/能源部"是外国机构 → `not_chinese`。
   - 合资控股平台（如某某巴西公司、某某老挝公司）通常是已在册集团的海外 SPV → `subsidiary_of_existing` 或 `alias_of_existing`。
4. **来源要求**：每条归属类结论至少 1 个 source_url；高置信度建议 2 个。查不到可靠来源 → `confidence=low` + notes 说明。

## 提交前校验

写完后自行 `json.load` 校验可解析，并对照上表检查每条 verdict 的字段完整性（归属类必有 `canonical_group_cn` 且 `source_urls` 非空）。**不要添加上表之外的任何字段**（如 `as_of`、`freq`）——校验按严格模式执行，多余字段会整条拒收；判定日期由回收方在台账里记录。
