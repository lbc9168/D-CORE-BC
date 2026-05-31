"""S2 项目基本信息 prompt：项目识别（防过度拆分）+ 名/国/地点/分类/阶段/容量；A.1 分类、A.3 容量反推、B.5 阶段细化。"""
import bri_enums as enums


def build_s2_prompt(title: str, content: str, date: str) -> str:
    """构建 S2 prompt：输出 projects[].common_info（不含中方实体，实体由 S4 处理）。"""
    cat_list = ", ".join(c for c in enums.valid_categories() if c != "Unknown")
    stage_list = enums.prompt_value_list("project_stage")
    return f"""你是能源项目信息提取专家。识别这篇新闻中的**具体电站/电力工程项目**并提取基本信息。**不要提取中方实体明细（另有专门步骤）。**

文章发布日期：{date}
标题：{title}
正文：
{content[:15000]}
(截断)

### 项目识别边界（防过度拆分，最重要）
- **一个物理电站/工程 = 一个项目**，即使新闻提到它的多个阶段（签约/开工/投产）或多方参与，也只算**一条**。
- 同一新闻同一项目的「一期/二期」若为**同一规划下的分期**，仍可合并为一条并在名称注明分期；只有明确是**独立选址/独立立项**的不同电站才拆成多条。
- 「框架协议/总量供货/国家级目标」不指向具体电站的，**不要**编成项目（这类由相关性步骤已大多剔除）。
- 不确定是否同一项目时，**倾向合并**而非拆分。

### 关键规则
- **A.1 分类映射**：垃圾发电/生物质发电→「生物质」；光热→按新能源发电归「光伏」或「综合能源」（若与光伏并存）；组件厂/锂电厂/多晶硅厂/装备产业园→「新能源制造业」（看产出单位：GW/年、万吨/年=工厂；MW/GW 装机=电站）；仅输电线路/变电站/电网改造/升压站→「输变电建设」；火电/燃机/燃气电厂/煤电（不与新能源结合）→「火电」。
- **混合能源（光伏+储能等）**：Category=「综合能源」；Capacity (MW)=各子项总装机数字之和；Capacity Details 填明细如「PV: 100MW, BESS: 50MW」。
- **A.3 容量反推**：若正文只有年发电量（如「年发电 X 亿千瓦时」）而无装机容量，按容量系数反推装机并在 Capacity Details 注明「capacity_source=derived」。
- **B.5 阶段细化**：「签临时移交证书/PAC」=「竣工」非「投入运营」；「项目公司与咨询公司签约」≠项目本身签约（不要据此判签约）；「并网发电/投产/商业运行」=「投入运营」；「完工」=「竣工」；仅「投标/招标/竞标/投标意向」且无中企具体中标/签约=「拟开发」。

### 输出（严格 JSON）
{{
  "projects": [
    {{
      "project_summary": "占位，可空（摘要由后续步骤生成）",
      "common_info": {{
        "Record Type": "具体项目 | 设备订单 | 一般合作",
        "Project Name": "中文，格式：国家+区域+容量MW+类型+项目+补充；严禁照抄英文",
        "Project Name (EN)": "英文官方名或按格式翻译",
        "Country": "国家中文名",
        "Location(State)": "州/省，可空",
        "Category": "只能选 [{cat_list}]",
        "Capacity (MW)": "数字或空",
        "Capacity Details": "明细或 capacity_source=derived 标注",
        "Project Stage": "只能选 [{stage_list}]",
        "Project Developer": "业主/开发商，多个用;分隔",
        "(Planned) Start Date": "YYYY-MM-DD 或空",
        "(Planned) Complete Date": "YYYY-MM-DD 或空"
      }}
    }}
  ]
}}
"""
