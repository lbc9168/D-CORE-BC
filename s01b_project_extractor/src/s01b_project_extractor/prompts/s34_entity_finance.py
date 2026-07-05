"""S3+S4 合并 prompt：一次调用同时抽中方实体+参与+设备(S4) 与 投融资信息(S3)，省去逐项目重复发正文。"""
import bri_enums as enums


def build_s34_prompt(title: str, content: str, project_name: str, category: str,
                     entity_lookup: str, main_equipment: list[str]) -> str:
    """为指定项目，一次性抽取：全部中方实体+参与方式+设备(A) 与 投融资字段(B)。"""
    involve_list = enums.prompt_value_list("involvement_type")
    fin_list = enums.prompt_value_list("financing_model")
    equip_text = "、".join(main_equipment) if main_equipment else "（该类型无预设主要设备）"
    return f"""你是项目信息抽取专家。针对**指定项目**，同时完成两件事：(A) 列出所有参与的中国企业及其角色/设备；(B) 抽取该项目的投融资信息。仅基于正文事实。

文章标题：{title}
正文：
{content[:12000]}
(截断)

目标项目：{project_name}（类型：{category}）

### A. 中方实体规则
1. 所有参与的中方企业（总包/分包/设备商/股权投资方/金融机构等）都要列入。
2. 中方金融机构（进出口银行、国家开发银行、中国银行、工商银行、建设银行、农业银行、民生银行、中国出口信用保险公司等）→ involvement_type 必须为「融资方」。
3. 「融资方」仅用于纯金融机构（只放贷/担保）；「投融资+EPC」用于既是股权投资方又承担 EPC 的同一企业。
4. **实体层级（母子公司对照表，强制参考）**：识别到表中企业，Implementing Chinese Entity=顶级集团，Subsidiary Name=对应子公司规范名；直接出现顶级集团则 Subsidiary 留空；不在表中按上下文判断，无法判断填入文中原名、Subsidiary 留空；Implementing Chinese Entity 不可为空（实在无法判断填 "Unknown"）。
{entity_lookup}
5. **设备角色（A.5）**：若该实体仅供应设备，判断设备是否为该项目类型的「主要设备」。本类型主要设备：{equip_text}。equipment_role∈[主要设备, 非主要设备, 未提及]，equipment_items 填具体设备名（如「组件、逆变器」）。
6. **设备供货两类（B.2）**：equipment_supply_scope∈[具体项目, 总量级]。若该中方角色只是「向某国/地区供应总量设备」未连到本具体电站，填「总量级」；连到本项目填「具体项目」；非设备供货填空串。

### B. 金融规则
- Financing Model 只能选 [{fin_list}]：贷款/融资/信贷/银团/买方信贷/优惠贷款→「信贷融资」；债券/公司债/点心债/绿色债券→「债券融资」；股权/股权投资→「股权融资」；同一项目同时含上述两类及以上→「混合融资」；无法判断→「Unknown」。
- **B.4 富融资信息保留**：若融资信息丰富（股权+债权+融资方+模式都有），把完整细节写进 Financing Model Details 与 Additional Remarks 原文保留，不要丢弃。
- Total Project Investment (Million USD)：只填换算后的数字（百万美元）；无则留空。
- Total Project Investment Raw：**逐字抄录**正文中投资额的原始短语（如"约50亿元人民币"），不换算、不改写；正文无投资额信息则留空。

### 输出（严格 JSON，实体列表 + 金融字段同级）
{{
  "chinese_entities": [
    {{
      "Implementing Chinese Entity": "顶级集团名（不可空）",
      "Subsidiary Name": "子公司规范名或空",
      "Type of involvement of Chinese Entities": "只能选 [{involve_list}]",
      "Subcontract Amount (Million USD)": "数字或空",
      "equipment_role": "主要设备 | 非主要设备 | 未提及",
      "equipment_items": "设备名，逗号分隔，或空",
      "equipment_supply_scope": "具体项目 | 总量级 | 空"
    }}
  ],
  "Total Project Investment Raw": "原文投资额短语或空",
  "Total Project Investment (Million USD)": "数字或空",
  "Investment (or Financial Close) Date": "YYYY-MM-DD 或空",
  "Financing Model": "枚举或 Unknown",
  "Financing Model Details": "融资方名称/明细或空",
  "Additional Remarks": "富融资原文细节或空"
}}
"""
