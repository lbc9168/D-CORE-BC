"""S3+S4 合并 prompt：一次调用同时抽中方实体+参与+设备(S4) 与 投融资信息(S3)，省去逐项目重复发正文。

注意：母子公司层级表已从 prompt 中移除（S4 减法）；
实体名由 LLM 逐字照原文抄写，归一化由下游确定性查表解析器（entity_normalize.py）完成。
"""
import bri_enums as enums


def build_s34_prompt(title: str, content: str, project_name: str, category: str,
                     main_equipment: list[str]) -> str:
    """为指定项目，一次性抽取：全部中方实体+参与方式+设备(A) 与 投融资字段(B)；实体名逐字照原文抄写。"""
    involve_list = enums.prompt_value_list("involvement_type")
    fin_list = enums.prompt_value_list("financing_model")
    equip_text = "、".join(main_equipment) if main_equipment else "（该类型无预设主要设备）"
    return f"""你是项目信息抽取专家。针对**指定项目**，同时完成两件事：(A) 列出所有参与的中国企业及其角色/设备；(B) 抽取该项目的投融资信息。仅基于标题或正文事实。

文章标题：{title}
正文：
{content[:12000]}
(截断)

目标项目：{project_name}（类型：{category}）

### A. 中方实体规则
1. 所有参与的中方企业（总包/分包/设备商/股权投资方/金融机构等）都要列入。
2. 中方金融机构（进出口银行、国家开发银行、中国银行、工商银行、建设银行、农业银行、民生银行、中国出口信用保险公司等）→ involvement_type 必须为「融资方」。
3. 「融资方」仅用于纯金融机构（只放贷/担保）；「投融资+EPC」用于既是股权投资方又承担 EPC 的同一企业。
4. **实体名填写规则**：
   - "Implementing Chinese Entity" 和 "Subsidiary Name" 必须**逐字照原文抄写**，不要拆分、不要归一、不要替换为简称或全称。
   - 如果标题或正文中出现的是子公司名称，将其填入 "Implementing Chinese Entity"，"Subsidiary Name" 留空。
   - 如果标题或正文中出现的是集团总部名称，将其填入 "Implementing Chinese Entity"，"Subsidiary Name" 留空。
   - "Implementing Chinese Entity" 不可为空（实在无法判断填 "Unknown"）。
5. **设备角色（A.5）**：若该实体仅供应设备，判断设备是否为该项目类型的「主要设备」。本类型及技术标签对应的主要设备：{equip_text}。综合能源基础设备不默认含电池，只有光储/风储/风光储标签才追加储能设备；TX 使用专用设备表；MF 不按发电设备表猜测，只按标题或正文明确事实判断。equipment_role∈[主要设备, 非主要设备, 未提及]。
6. **设备供货两类（B.2）**：equipment_supply_scope∈[具体项目, 总量级]。若该中方角色只是「向某国/地区供应总量设备」未连到本具体电站，填「总量级」；连到本项目填「具体项目」；非设备供货填空串。

### B. 金融规则
- Financing Model 只能选 [{fin_list}]：贷款/融资/信贷/银团/买方信贷/优惠贷款→「信贷融资」；债券/公司债/点心债/绿色债券→「债券融资」；股权/股权投资→「股权融资」；同一项目同时含上述两类及以上→「混合融资」；无法判断→「Unknown」。
- **B.4 富融资信息保留**：若融资信息丰富（股权+债权+融资方+模式都有），把完整细节写进 Financing Model Details 与 Additional Remarks 原文保留，不要丢弃。
- Total Project Investment (Million USD)：只填换算后的数字（百万美元）；无则留空。
- Total Project Investment Raw：**逐字抄录**标题或正文中投资额的连续原始短语（如"约50亿元人民币"），不换算、不改写；未提及则留空且无需证据。
- Contract Amount (Million USD)（合同金额）：中方企业就本项目签订的**工程承包合同金额**（EPC/总承包/施工/设备供货合同）。识别关键词：合同金额、合同总价、中标金额、中标价、签约金额。只填换算后的百万美元数字；无则留空。
- Contract Amount Raw：**逐字抄录**原文合同金额短语（如"合同金额约12亿元人民币"）；无则留空。
- Financing Amount (Million USD)（融资额度）：为本项目筹措的**债务融资金额**。识别关键词：贷款、授信、银团贷款、融资协议、买方信贷、出口信贷、优惠贷款金额。只填换算后的百万美元数字；无则留空。
- Financing Amount Raw：**逐字抄录**原文融资金额短语；无则留空。
- **B.5 金额归属规则**：三类金额（总投资/合同额/融资额）概念上不互斥、可同时有值（如"总投资50亿，其中银团贷款35亿"→投资额与融资额都填）。但**每一笔数字短语只归入语义对应的一个字段**："总投资/项目投资额"只进 Total Project Investment；"合同金额/中标价/签约额"只进 Contract Amount；"贷款/授信/银团额"只进 Financing Amount。禁止跨字段复制或推断（不得以合同额倒推总投资、不得把总投资拆算成融资额）；标题或正文未提及的字段必须留空，证据不得跨标题和正文拼接。多笔分项无总额短语时数值字段留空、Raw 逐字抄录各分项短语（禁止自行相加）。实体级 Subcontract Amount 规则不变（该实体自身份额）；Contract Amount 是项目级主合同额。

### 输出（严格 JSON，实体列表 + 金融字段同级）
{{
  "chinese_entities": [
    {{
      "Implementing Chinese Entity": "原文中国企业名（逐字抄，不可空）",
      "Subsidiary Name": "留空（原文无子公司信息时留空）",
      "Type of involvement of Chinese Entities": "只能选 [{involve_list}]",
      "Subcontract Amount (Million USD)": "数字或空",
      "equipment_role": "主要设备 | 非主要设备 | 未提及",
      "equipment_items": "设备名，逗号分隔，或空",
      "equipment_supply_scope": "具体项目 | 总量级 | 空"
    }}
  ],
  "Total Project Investment Raw": "原文投资额短语或空",
  "Total Project Investment (Million USD)": "数字或空",
  "Contract Amount Raw": "原文合同金额短语或空",
  "Contract Amount (Million USD)": "数字或空",
  "Financing Amount Raw": "原文融资金额短语或空",
  "Financing Amount (Million USD)": "数字或空",
  "Investment (or Financial Close) Date": "YYYY-MM-DD 或空",
  "Financing Model": "枚举或 Unknown",
  "Financing Model Details": "融资方名称/明细或空",
  "Additional Remarks": "富融资原文细节或空"
}}
"""
