"""S2 项目基本信息 prompt：项目识别（防过度拆分）+ 名/国/地点/分类/阶段/容量；A.1 分类、A.3 容量反推、B.5 阶段细化。"""
import bri_enums as enums
from s01b_project_extractor.prompts.policy_fragments import MANUFACTURING_SCOPE_RULE, RECORD_TYPE_RULE


S2_PROMPT_VERSION = "2026-09-23.10-energy-equipment-boundary"

def build_s2_prompt(title: str, content: str, date: str) -> str:
    """构建 S2 prompt：输出 projects[].common_info（不含中方实体，实体由 S4 处理）。"""
    cat_list = ", ".join(c for c in enums.valid_categories() if c != "Unknown")
    stage_list = enums.prompt_value_list("project_stage")
    source_list = enums.prompt_value_list("generation_source")
    storage_list = enums.prompt_value_list("storage_status")
    return f"""提示词版本：{S2_PROMPT_VERSION}
你负责辨认本次新闻的新事件涉及哪些独立工程，再填写字段。本文仅是候选；中企参与由S4/S34独立判断。本步不提取企业明细，也不因非必要字段未知丢掉明确工程。

### 第一任务：先决定记录边界
按以下顺序判断，不能从新闻标题或容量数字直接生成项目：
1. 找出本次新发生的签约、中标、开工、施工、移交、投产等事件，以及事件实际对应的工程/资产。历史业绩、已完成的其他电站、公司背景和无独立事件的未来规划只作上下文，不建立本篇事件。没有合格记录时返回projects空数组。
2. 对每个本次事件，先辨认独立物理资产与场址，再看商业合同。不同场址且分别运行/并网的两座电站必须各一条；共同EPC合同、业主、项目公司、标题打包名、统一验收/完工移交均不得把它们改称同一工程的分站/分期。这一物理边界优先于报道的商业“项目”称谓。原文已明确两个独立项目或分别运行的资产，不要求另有独立合同或完整场址名；可分别服务负荷的两套完整移动变电资产是两个工程，一站内多台变压器仅为该站设备。
3. 只有同一物理资产内部的多企业、采购包、施工包及分期不重复造项目；发电项目内部配套升压站和送出线不另拆TX。分期规模写明细，不能把一期容量冒充全厂。不同电压/容量本身不证明不同工程，须结合原文资产身份。
4. 两个独立项目只有合计容量/金额时，各站数值留空，合计只写明细，不分摊、不复制，也不得把组合容量写进各站名称。名单/设备订单总量不能推造多个电站。
5. {RECORD_TYPE_RULE}
将每条的原文名称、别名及“本次事件对应哪个工程”的理由写入Project Context；拆分后每条source_name只指该独立目标，context先说明该目标的事件，其他站/共同合同仅注明为上下文，不能复制整个组合名充当各站标识。不要把背景项目也列出来再交后续排除。

### 范围与分类
- 纳入发电（含火电）、独立储能、新能源制造业、独立输变电/电网工程及其能源订单/合作。普通矿厂、油气开采/管线、非发电民用或工业设施、无新增装机的纯运维检修不提取；配套能源资产只有确有独立本次事件才提取。
- 制造业口径：{MANUFACTURING_SCOPE_RULE}
- in_scope=true表示上述业务范围；确实越界的候选可置false并解释。in_scope=false 时 Category 一律填 "Unknown"。非项目记录不清楚类别时也可Unknown，不伪装电站。
- 储能不是发电源。单一发电源配储仍归该发电类别；只有两种及以上非备用发电源组合才归「综合能源」。单一新能源的备用柴油/燃气不改变主分类。独立储能无发电源时归「储能」，电池工厂不拆储能站。
- 垃圾/生物质发电归生物质，光热/光伏归光伏，燃机/煤电归火电。绿氢按其明确发电来源分类（如光伏制氢→归「光伏」）；没有明确能源种类则范围外，**不要**仅因带「绿氢」就归「综合能源」。标题或正文给出目标能源来源均有效，来源明确但容量未知可保留。地热制氢电源的中标订单按实际设备供货边界提取；不凭未具名基地描述虚构完整电站，制氢方量不写入发电MW。
- Generation Sources仅选实际发电源：{source_list}。Storage Status选：{storage_list}；TX/MF分别为空数组和not_mentioned。能源证据保留原文，类别/技术标签由Python依据原子事实生成，不建议新标签。

### 字段：只填写归属本条的事实
- 名称用国家中文全称+场址/原文专名+已明确规格+类型；保留原文专名拼写和能源特征，不写EPC等承包方式。Project Context中的原文名称/地区描述允许与展示名称不同，靠上下文对应，不靠同句或逐字匹配。
- 发电用Capacity (MW)，储能功率/能量分别用Storage Power/Storage Energy；只抄原数字与单位，由Python换算。MW与MWh分开，储能MW不得加到发电MW；年发电量、工厂年产能不当装机，禁止按小时或容量系数猜数值。未披露就留空。
- Capacity Evidence可用多个跨句片段，scope说明project/unit/portfolio/unknown并解释归属。明确项目总量优先于四舍五入后的单机乘积；只有完整设备数量×单机规格明确覆盖该工程才可推总量。燃机加蒸汽轮机只给燃机容量时，全厂容量留空；单机、首批、部分和组合数字只进明细。纯储能项目发电容量为空不影响独立储能MW/MWh。
- Storage Power/Energy仅confirmed时填原文，Energy Composition Evidence保留对应储能资产的上下文。计划/预留配置保留状态，不能用背景储能或订单总量充当本站已配置容量。
- TX/MF专用规格：TX用TX Voltage/Line Length/Transmission Capacity Evidence；MF用MF Production Capacity Evidence。四者抄各自原文数字短语，未提及不要求提供证据，非对应类型留空，发电Capacity (MW)在TX/MF 强制为空。电压保留115/22kV等完整规格；kVA不是电压，MVAr不是输送容量，电池工厂 GWh/年是生产产能，不是 BESS 储能能量。
- 阶段：首次点火/冲转/调试不是投运；并网/投产/商业运行才是投入运营，完工/PAC为竣工，纯招标为拟开发。不要用背景工程阶段或企业与咨询公司的合同代替本工程节点。日期缺失留空。
- 业主判定：只填全文明确的产权/股权持有者、开发商、投资建设方、EPC发包方、特许经营权持有人或PPA卖方；制造商投资建厂可为业主。按全文语义区分项目公司/股东与EPC承包方：承担完整EPC不等于拥有项目，不能把承包商与另一已确认业主合写为业主。电力公司/政府对项目给予肯定、赞扬、验收或审批，也不证明其产权或开发角色。设备商、融资机构、购电方同样不是自动业主。合格投标方、入围名单、短名单、资格预审仅表明候选资格，未正式中标/授标/开发不得填业主。合资已明确各方可分填，多个用分号；否则只填项目公司。保留跨句依据，不借别的项目，不把子公司换成母公司；无法确定业主关系时留空。

文章发布日期：{date}
标题：{title}
正文：
{content[:15000]}
(超过15000字符时截断)

### 输出（严格 JSON）
{{
  "projects": [
    {{
      "project_summary": "占位，可空（摘要由后续步骤生成）",
      "in_scope": true,
      "out_of_scope_reason": "若 in_scope=false 则填一句话理由（如：油气开采，非发电/制造/输变电）；in_scope=true 时留空",
      "common_info": {{
        "Record Type": "具体项目|设备订单|一般合作，按上述统一口径；供应给已识别物理工程仍记具体项目，不因参与方只供设备改成订单",
        "Project Name": "中文命名：国家全称+地点+原文项目专名+容量(原文单位)+类型+项目。专名必须保留原文拼写，可含拉丁字母，不得仅因名称含中文的要求删除Çayırlar等专名。无专名则保留原文核心项目描述；不附承包方式",
        "Project Context": {{"source_name": "原文项目标识", "aliases": ["原文明示别名"], "context": "区分本次事件、背景及相邻项目的简短上下文", "is_current_event": true}},
        "Project Name (EN)": "英文官方名或按格式翻译",
        "Country": "国家中文名",
        "Location(State)": "州/省，可空",
        "Category": "只能选 [{cat_list}]；范围外或非项目记录类别不明确可填Unknown",
        "Generation Sources": ["只能从光伏/风电/水电/地热及余热/生物质/核电/火电选择；无证据则空数组"],
        "Storage Status": "confirmed/planned/optional/unclear/not_mentioned 五选一",
        "Storage Power": "confirmed 储能功率原文短语（MW）或空",
        "Storage Energy": "confirmed 储能能量原文短语（MWh/GWh）或空",
        "Energy Composition Evidence": "支撑能源构成判断的原文短证据",
        "Capacity (MW)": "仅发电装机的连续逐字原文短语；TX/MF 强制为空；禁止换算",
        "Capacity Evidence": {{"quote": "支持容量的原文片段或空", "project_anchor": "当前项目原文描述/专名或空", "evidence_quotes": ["容量及归属片段，可跨句"], "scope": "project|unit|portfolio|unknown", "rationale": "容量属于何对象，未披露则说明"}},
        "Capacity Details": "明细或 capacity_source=derived 标注",
        "TX Voltage Evidence": "仅TX：标题或正文中的连续逐字电压短语，否则空",
        "TX Line Length Evidence": "仅TX：标题或正文中的连续逐字线路长度短语，否则空",
        "TX Transmission Capacity Evidence": "仅TX：标题或正文中的连续逐字输送MW/MVA短语，否则空",
        "MF Production Capacity Evidence": "仅MF：标题或正文中的连续逐字年产能短语，否则空",
        "Project Stage": "只能选 [{stage_list}]，未披露为Unknown",
        "Project Developer": "业主——按上文「业主判定」规则：有证据才填，无证据留空；多个用;分隔，只写公司名",
        "Project Developer Evidence": "支持每个业主和当前项目开发/产权关系的原文片段，可保留跨句上下文；缺乏业主关系不得仅凭参与填写。业主留空则空串",
        "(Planned) Start Date": "YYYY-MM-DD 或空",
        "(Planned) Complete Date": "YYYY-MM-DD 或空"
      }}
    }}
  ]
}}
"""
