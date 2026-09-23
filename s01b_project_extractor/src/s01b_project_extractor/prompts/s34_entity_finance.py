"""S3+S4 合并 prompt：一次调用同时抽中方实体+参与+设备(S4) 与 投融资信息(S3)，省去逐项目重复发正文。

注意：母子公司层级表已从 prompt 中移除（S4 减法）；
实体名由 LLM 逐字照原文抄写，归一化由下游确定性查表解析器（entity_normalize.py）完成。
"""
import json
import bri_enums as enums
from s01b_project_extractor.prompts.policy_fragments import (
    AMOUNT_EVIDENCE_RULE,
    DEVELOPER_REVIEW_RULE,
    ENTITY_POLICY_VERSION,
    CHINESE_IDENTITY_RULE,
    ENTITY_ROLE_RULE,
    FINANCING_MODEL_RULE,
    PROJECT_EVIDENCE_BINDING_RULE,
    PROJECT_ASSESSMENT_RULE,
)


def build_s34_prompt(title: str, content: str, project_name: str, category: str,
                     main_equipment: list[str], project_context: dict | None = None) -> str:
    """为指定项目，一次性抽取：全部中方实体+参与方式+设备(A) 与 投融资字段(B)；实体名逐字照原文抄写。"""
    involve_list = enums.prompt_value_list("involvement_type")
    fin_list = enums.prompt_value_list("financing_model")
    equip_text = "、".join(main_equipment) if main_equipment else "（该类型无预设主要设备）"
    return f"""提示词版本：{ENTITY_POLICY_VERSION}
你是项目信息抽取专家。针对**指定项目**，同时完成两件事：(A) 列出所有参与的中国企业及其角色/设备；(B) 抽取该项目的投融资信息。参与、角色和金额基于标题或正文事实；中企身份另可结合常见企业知识与提供的名录线索。候选初筛不代表每个项目已确认有中企。

文章标题：{title}
正文：
{content[:12000]}
(截断)

目标项目：{project_name}（类型：{category}）
原文项目上下文：{json.dumps(project_context or {}, ensure_ascii=False)}

### 项目边界独立复核
{PROJECT_ASSESSMENT_RULE}

### A. 中方实体规则
1. 所有参与的中方企业（总包/分包/设备商/股权投资方/金融机构等）都要列入。
   - {CHINESE_IDENTITY_RULE}
   - {ENTITY_ROLE_RULE}
2. 中方金融机构（进出口银行、国家开发银行、中国银行、工商银行、建设银行、农业银行、民生银行、中国出口信用保险公司等）→ involvement_type 必须为「融资方」。
3. 「融资方」仅用于纯金融机构（只放贷/担保）；「投融资+EPC」用于既是股权投资方又承担 EPC 的同一企业。
4. **实体名填写规则**：
   - "Implementing Chinese Entity" 和 "Subsidiary Name" 必须**逐字照原文抄写**，不要拆分、不要归一、不要替换为简称或全称。
   - 如果标题或正文中出现的是子公司名称，将其填入 "Implementing Chinese Entity"，"Subsidiary Name" 留空。
   - 如果标题或正文中出现的是集团总部名称，将其填入 "Implementing Chinese Entity"，"Subsidiary Name" 留空。
   - 不得用 "Unknown" 虚构企业。保留与目标项目相关的候选，明确外企/否定参与用相应false，真实歧义用null；没有任何合理候选时才返回空数组。
   - 每个实体必须提供 Participation Assessment：participates与is_chinese分别为true/false/null，不用缺省值代替判断。identity_basis取article、registry、model_knowledge或uncertain，并在rationale解释依据；registry仅用于实际提供的名录线索。
   - rationale说明该企业参与的是当前目标项目而非历史背景、另一项目或否定情形；evidence_quotes保留支持判断的原文片段。{PROJECT_EVIDENCE_BINDING_RULE}
   - Chinese Identity Evidence仅摘录原文明示身份的片段，没有则空串；模型知识写入rationale，不伪造为原文国籍引文。
   - 先判断参与，再判断角色。角色无明确依据时role与Type of involvement of Chinese Entities均为Unknown，role_basis说明已知参与但职责未知；供应商不能推成EPC/业主，合作开发不能推成股权。
   - Participation Evidence保留各角色原文片段及目标项目描述作审计，不要求一条quote同时含企业、项目和动作；多个片段可分条列出。
5. **设备角色（A.5）**：若该实体仅供应设备，判断设备是否为该项目类型的「主要设备」。本类型及技术标签对应的主要设备：{equip_text}。综合能源基础设备不默认含电池，只有光储/风储/风光储标签才追加储能设备；TX 使用专用设备表；MF 不按发电设备表猜测，只按标题或正文明确事实判断。equipment_role∈[主要设备, 非主要设备, 未提及]。
6. **设备供货两类（B.2）**：equipment_supply_scope∈[具体项目, 总量级]。若该中方角色只是「向某国/地区供应总量设备」未连到本具体电站，填「总量级」；连到本项目填「具体项目」；非设备供货填空串。

### B. 金融规则
- {DEVELOPER_REVIEW_RULE}
- {AMOUNT_EVIDENCE_RULE}
- Financing Model 只能选 [{fin_list}]。{FINANCING_MODEL_RULE}
- Financing Evidence 仅在 Financing Model 非 Unknown 时填写。{PROJECT_EVIDENCE_BINDING_RULE}
- tool 只能选 credit/bond/equity/other；银团属于 credit。other 只用于明确归属于目标项目的赠款、资金援助或资助；组合层面援助仍填 Unknown。
- **B.4 富融资信息保留**：若融资信息丰富（股权+债权+融资方+模式都有），把完整细节写进 Financing Model Details 与 Additional Remarks 原文保留，不要丢弃。
- Total Project Investment (Million USD)：只填有依据的百万美元数字；原币非美元且无汇率依据时留空。
- Total Project Investment Raw：**逐字抄录**标题或正文中投资额的连续原始短语（如"约50亿元人民币"），不换算、不改写；未提及则留空且无需证据。
- Contract Amount (Million USD)（合同金额）：中方企业就本项目签订的**工程承包合同金额**（EPC/总承包/施工/设备供货合同）。识别关键词：合同金额、合同总价、中标金额、中标价、签约金额。只填有依据的百万美元数字；无可复现汇率依据时留空。
- Contract Amount Raw：**逐字抄录**原文合同金额短语（如"合同金额约12亿元人民币"）；无则留空。
- Financing Amount (Million USD)（融资额度）：为本项目筹措的**债务融资金额**。识别关键词：贷款、授信、银团贷款、融资协议、买方信贷、出口信贷、优惠贷款金额。只填有依据的百万美元数字；无可复现汇率依据时留空。
- Financing Amount Raw：**逐字抄录**原文融资金额短语；无则留空。
- **B.5 金额归属规则**：三类金额（总投资/合同额/融资额）概念上不互斥、可同时有值（如"总投资50亿，其中银团贷款35亿"→投资额与融资额都填）。但**每一笔数字短语只归入语义对应的一个字段**："总投资/项目投资额"只进 Total Project Investment；"合同金额/中标价/签约额"只进 Contract Amount；"贷款/授信/银团额"只进 Financing Amount。禁止跨字段复制或推断（不得以合同额倒推总投资、不得把总投资拆算成融资额）；标题或正文未提及的字段必须留空，证据不得跨标题和正文拼接。多笔分项无总额短语时数值字段留空、Raw 逐字抄录各分项短语（禁止自行相加）。实体级 Subcontract Amount 规则不变（该实体自身份额）；Contract Amount 是项目级主合同额。

### 输出（严格 JSON，实体列表 + 金融字段同级）
{{
  "Project Assessment": {{
    "is_current_event": true,
    "is_single_physical_project": true,
    "rationale": "独立说明本次新事件和目标工程边界；不照抄S2结论",
    "evidence_quotes": ["支持本次事件与项目边界的原文片段"],
    "separate_projects": []
  }},
  "chinese_entities": [
    {{
      "Implementing Chinese Entity": "原文中国企业名（逐字抄，不可空）",
      "Subsidiary Name": "留空（原文无子公司信息时留空）",
      "Chinese Identity Evidence": "原文明示当前具名企业为中企的片段；无则空串",
      "Participation Assessment": {{
        "participates": true,
        "is_chinese": true,
        "identity_basis": "article|registry|model_knowledge|uncertain",
        "rationale": "参与及中企判断依据，说明与当前目标项目的关系；有歧义如实说明",
        "evidence_quotes": ["原文片段，可多条、跨句、列表"],
        "role": "参与方式枚举或Unknown，与下方角色一致",
        "role_basis": "具体职责依据；仅品牌使用不能推成EPC/业主"
      }},
      "Type of involvement of Chinese Entities": "只能选 [{involve_list}]",
      "Participation Evidence": [
        {{"role": "单项参与方式枚举", "quote": "支持该角色或参与判断的原文片段", "project_anchor": "对应项目的原文名称/描述或别名"}}
      ],
      "Subcontract Amount (Million USD)": "数字或空",
      "equipment_role": "主要设备 | 非主要设备 | 未提及",
      "equipment_items": "设备名，逗号分隔，或空",
      "equipment_supply_scope": "具体项目 | 总量级 | 空"
    }}
  ],
  "Project Developer": "项目公司/开发商/股东原文名，未知为null或空字符串",
  "Project Developer Evidence": "业主判断依据，未知为null或空字符串",
  "Total Project Investment Raw": "原文投资额短语或空",
  "Total Project Investment (Million USD)": "数字或空",
  "Contract Amount Raw": "原文合同金额短语或空",
  "Contract Amount (Million USD)": "数字或空",
  "Financing Amount Raw": "原文融资金额短语或空",
  "Financing Amount (Million USD)": "数字或空",
  "Investment (or Financial Close) Date": "YYYY-MM-DD 或空",
  "Financing Model": "枚举或 Unknown",
  "Financing Evidence": [
    {{"tool": "credit|bond|equity|other", "quote": "支持融资工具的原文片段", "project_anchor": "对应项目原文描述/别名", "rationale": "工具为何属于当前目标项目", "scope": "project|portfolio|unknown"}}
  ],
  "Financing Model Details": "融资方名称/明细或空",
  "Additional Remarks": "富融资原文细节或未分配组合金额说明或空",
  "Amount Evidence": [
    {{"field": "Total Project Investment (Million USD)|Contract Amount (Million USD)|Financing Amount (Million USD)|Subcontract Amount (Million USD)", "amount_scope": "project|portfolio|equipment_unit", "currency": "原币代码如USD/CNY", "original_amount": null, "usd_per_currency_unit": null, "conversion_basis": "可复现依据或空", "evidence_quotes": ["原文金额及归属片段"], "rationale": "金额类别及目标项目归属"}}
  ]
}}
"""
