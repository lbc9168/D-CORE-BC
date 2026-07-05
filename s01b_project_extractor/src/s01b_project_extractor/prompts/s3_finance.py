"""S3 金融信息 prompt：投融资额/融资模式/融资方/分包额/投资日期；B.4 富融资信息保留。"""
import bri_enums as enums


def build_s3_prompt(title: str, content: str, project_name: str) -> str:
    """为指定项目抽取金融信息。"""
    fin_list = enums.prompt_value_list("financing_model")
    return f"""你是项目投融资信息抽取专家。针对指定项目，提取金融相关字段。仅基于正文。

标题：{title}
正文：
{content[:12000]}
(截断)
目标项目：{project_name}

### 规则
- Financing Model 只能选 [{fin_list}]：贷款/融资/信贷/银团/买方信贷/优惠贷款→「信贷融资」；债券/公司债/点心债/绿色债券→「债券融资」；股权/股权投资→「股权融资」；同一项目同时含上述两类及以上→「混合融资」；无法判断→「Unknown」。
- **B.4 富融资信息保留**：若融资信息丰富（股权+债权+融资方+模式都有），把完整细节写进 Financing Model Details 与 Additional Remarks 原文保留，不要丢弃。
- Total Project Investment (Million USD)：只填换算后的数字（百万美元）；无则留空。
- Total Project Investment Raw：**逐字抄录**正文中投资额的原始短语（如"约50亿元人民币"），不换算、不改写；正文无投资额信息则留空。

### 输出（严格 JSON）
{{
  "Total Project Investment Raw": "原文投资额短语或空",
  "Total Project Investment (Million USD)": "数字或空",
  "Investment (or Financial Close) Date": "YYYY-MM-DD 或空",
  "Financing Model": "枚举或 Unknown",
  "Financing Model Details": "融资方名称/明细或空",
  "Additional Remarks": "富融资原文细节或空"
}}
"""
