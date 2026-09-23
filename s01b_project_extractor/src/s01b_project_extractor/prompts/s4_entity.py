"""S4 中方实体+参与方式+设备 prompt：枚举经 bri_enums 注入，含 A.5 设备主/次与 B.2 设备供货两类。

注意：母子公司层级表已从 prompt 中移除（S4 减法）。
实体名由 LLM 逐字照原文抄写，归一化由下游确定性查表解析器（entity_normalize.py）完成。
"""
import json
import bri_enums as enums
from s01b_project_extractor.prompts.policy_fragments import (
    AMOUNT_EVIDENCE_RULE,
    ENTITY_POLICY_VERSION,
    CHINESE_IDENTITY_RULE,
    ENTITY_ROLE_RULE,
    PROJECT_EVIDENCE_BINDING_RULE,
    PROJECT_ASSESSMENT_RULE,
)


def build_s4_prompt(title: str, content: str, project_name: str, category: str, main_equipment: list[str], project_context: dict | None = None) -> str:
    """为某个已识别项目，提取其全部中方实体、参与方式、设备角色；实体名逐字照原文抄写。"""
    involve_list = enums.prompt_value_list("involvement_type")
    equip_text = "、".join(main_equipment) if main_equipment else "（该类型无预设主要设备）"
    return f"""提示词版本：{ENTITY_POLICY_VERSION}
你是中方参与实体抽取专家。针对**指定项目**，列出所有参与的中国企业及其角色。参与、角色和金额基于标题或正文事实；中企身份另可结合常见企业知识与提供的名录线索。候选初筛不代表每个项目已确认有中企。

文章标题：{title}
正文：
{content[:12000]}
(截断)

目标项目：{project_name}（类型：{category}）
原文项目上下文：{json.dumps(project_context or {}, ensure_ascii=False)}

### 项目边界独立复核
{PROJECT_ASSESSMENT_RULE}

### 规则
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
5. **设备角色（A.5）**：若该实体仅供应设备，判断设备是否为该项目类型的「主要设备」。本类型及技术标签对应的主要设备：{equip_text}。综合能源基础设备不默认含电池，只有光储/风储/风光储标签才追加储能设备；TX 使用其专用设备表；MF 不按发电设备表猜测，只按标题或正文明确事实判断。equipment_role∈[主要设备, 非主要设备, 未提及]。
6. **设备供货两类（B.2）**：equipment_supply_scope∈[具体项目, 总量级]。若该中方角色只是「向某国/地区供应总量设备」未连到本具体电站，填「总量级」；连到本项目填「具体项目」；非设备供货填空串。

### 金额规则
{AMOUNT_EVIDENCE_RULE}
实体分包额只填该企业在本项目中明确披露的份额，不分摊主合同额。

### 输出（严格 JSON）
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
  "Amount Evidence": [
    {{"field": "Subcontract Amount (Million USD)", "amount_scope": "project|portfolio|equipment_unit", "currency": "原币代码如USD/CNY", "original_amount": null, "usd_per_currency_unit": null, "conversion_basis": "可复现依据或空", "evidence_quotes": ["原文金额及归属片段"], "rationale": "该企业份额及目标项目归属"}}
  ]
}}
"""
