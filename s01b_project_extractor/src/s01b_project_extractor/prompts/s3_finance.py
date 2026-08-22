"""S3 金融信息 prompt：投融资额/融资模式/融资方/分包额/投资日期；B.4 富融资信息保留。"""
import bri_enums as enums
from s01b_project_extractor.prompts.policy_fragments import (
    FINANCING_MODEL_RULE,
    PROJECT_EVIDENCE_BINDING_RULE,
)


def build_s3_prompt(title: str, content: str, project_name: str) -> str:
    """为指定项目抽取金融信息。"""
    fin_list = enums.prompt_value_list("financing_model")
    return f"""你是项目投融资信息抽取专家。针对指定项目，提取金融相关字段。仅基于标题或正文事实。

标题：{title}
正文：
{content[:12000]}
(截断)
目标项目：{project_name}

### 规则
- Financing Model 只能选 [{fin_list}]。{FINANCING_MODEL_RULE}
- Financing Evidence 仅在 Financing Model 非 Unknown 时填写。{PROJECT_EVIDENCE_BINDING_RULE}
- tool 只能选 credit/bond/equity/other；银团属于 credit。other 只用于明确归属于目标项目的赠款、资金援助或资助；组合层面援助仍填 Unknown。
- **B.4 富融资信息保留**：若融资信息丰富（股权+债权+融资方+模式都有），把完整细节写进 Financing Model Details 与 Additional Remarks 原文保留，不要丢弃。
- Total Project Investment (Million USD)：只填换算后的数字（百万美元）；无则留空。
- Total Project Investment Raw：**逐字抄录**标题或正文中投资额的连续原始短语（如"约50亿元人民币"），不换算、不改写；未提及则留空且无需证据。
- Contract Amount (Million USD)（合同金额）：中方企业就本项目签订的**工程承包合同金额**（EPC/总承包/施工/设备供货合同）。识别关键词：合同金额、合同总价、中标金额、中标价、签约金额。只填换算后的百万美元数字；无则留空。
- Contract Amount Raw：**逐字抄录**原文合同金额短语（如"合同金额约12亿元人民币"）；无则留空。
- Financing Amount (Million USD)（融资额度）：为本项目筹措的**债务融资金额**。识别关键词：贷款、授信、银团贷款、融资协议、买方信贷、出口信贷、优惠贷款金额。只填换算后的百万美元数字；无则留空。
- Financing Amount Raw：**逐字抄录**原文融资金额短语；无则留空。
- **B.5 金额归属规则**：三类金额（总投资/合同额/融资额）概念上不互斥、可同时有值（如"总投资50亿，其中银团贷款35亿"→投资额与融资额都填）。但**每一笔数字短语只归入语义对应的一个字段**："总投资/项目投资额"只进 Total Project Investment；"合同金额/中标价/签约额"只进 Contract Amount；"贷款/授信/银团额"只进 Financing Amount。禁止跨字段复制或推断（不得以合同额倒推总投资、不得把总投资拆算成融资额）；标题或正文未提及的字段必须留空。证据只能取单一 surface 的连续短语，不得跨标题和正文拼接。多笔分项无总额短语时数值字段留空、Raw 逐字抄录各分项短语（禁止自行相加）。

### 输出（严格 JSON）
{{
  "Total Project Investment Raw": "原文投资额短语或空",
  "Total Project Investment (Million USD)": "数字或空",
  "Contract Amount Raw": "原文合同金额短语或空",
  "Contract Amount (Million USD)": "数字或空",
  "Financing Amount Raw": "原文融资金额短语或空",
  "Financing Amount (Million USD)": "数字或空",
  "Investment (or Financial Close) Date": "YYYY-MM-DD 或空",
  "Financing Model": "枚举或 Unknown",
  "Financing Evidence": [
    {{"tool": "credit|bond|equity|other", "quote": "连续原文引文", "project_anchor": "引文中的非泛化项目锚点"}}
  ],
  "Financing Model Details": "融资方名称/明细或空",
  "Additional Remarks": "富融资原文细节或空"
}}
"""
