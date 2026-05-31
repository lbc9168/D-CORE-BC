"""S1 相关性 + 中企参与门 prompt：只判 RE 相关性与是否有中企，强化 B.1 排除规则。"""


def build_s1_prompt(title: str, content: str, date: str) -> str:
    """构建 S1 门控 prompt：输出 {status, reasoning, project_subject, is_power_generation_plant}。"""
    return f"""你是一个能源新闻相关性审核专家。只做一件事：判断这篇新闻是否是「有中国企业参与的海外可再生能源/电力电站项目」，并给出三选一结论。**不要提取项目明细。**

文章发布日期：{date}
文章标题：{title}
文章正文：
{content[:12000]}
(内容截断)

### 结论枚举（status，三选一）
- "valid_project"：确有具体的海外电站/电力工程项目，且有中国企业参与（业主/承包商/设备商/投资方/融资方等任一）。
- "no_chinese_participation"：是可再生能源/电力项目，但正文中无任何中国企业出现，或各环节参与者均非中企。
- "irrelevant"：不属于我们要收录的「中企参与的海外发电/电力工程项目」。

### 判 irrelevant 的强制排除规则（命中任一即 irrelevant）
1. **非发电主体的基建/民用工程**：学校、医院、机场、铁路、港口、农业灌溉、供水管网、工业园区、住宅等——即便由中企 EPC、即便提到「电力配套/供电工程/屋顶光伏」但仅作附属设施且无明确装机容量。原则：要的是「发电厂」不是「用电户」。
   - **数据中心特例（A.2）**：数据中心只有在「由可再生能源专门供电/自建配套 RE 电站」时才算 valid；普通市电供电的数据中心 → irrelevant。
2. **油气/矿业/管网**：油田、气田、炼化、LNG、油气管道、采矿、冶炼等非发电项目，即便中企参与 → irrelevant。
3. **检修/改造/维护**：仅「检修、维护、改造、升级」且无新增具体装机容量 → irrelevant。
4. **模糊/总量级设备供货（B.2 后者）**：仅「组件供应/销售」「国家或地区级总量设备供货」（如「向埃及供应 2.5GW 太阳能电池」「澳洲 300MW+ 组件供应」）而**不连到某具体电站项目** → irrelevant。（注：连到具体电站的设备供货仍是 valid_project，由后续阶段标 Record Type=设备订单。）
5. **仅获批/审批、无中企具体中标或签约**：某国政府「批准/审批通过」一批项目，但未提及中企的具体中标/签约 → irrelevant。
6. **商务会谈/领导访问/视察类**：仅企业或政府领导间访问、会议、交流、视察既有项目，无具体新项目节点 → irrelevant。
7. **评论/综述/榜单类**：无具体项目节点的行业评论、综述、排行 → irrelevant。

### 中企参与判断（优先级最高）
正文无任何中国企业名称、或项目各环节参与者均为非中企 → "no_chinese_participation"。

### 输出格式（严格 JSON，仅这些字段）
{{
  "status": "valid_project | no_chinese_participation | irrelevant",
  "project_subject": "一句话点明新闻主体（什么项目/什么主体）",
  "is_power_generation_plant": true | false,
  "reasoning": "判定理由：命中了哪条规则或为何 valid（简短）"
}}
"""
