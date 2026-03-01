import os
import pandas as pd
from google import genai
from google.genai import types
import json
import time
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ================= 配置区域 =================
GOOGLE_API_KEY = os.getenv("GEMINI_API_KEY")

# 2. 输入和输出文件名
INPUT_FILE = "Energy_News_Filtered_20260225_1855.xlsx"  # 你的爬虫数据文件
OUTPUT_VALID = f"BRI_RE_Final_Database_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"      # 最终结果（有中方参与）
OUTPUT_NO_CHINESE = f"BRI_RE_No_Chinese_Involvement_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx" # 有效项目但无中方
OUTPUT_DISCARDED = f"BRI_RE_Discarded_News_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"  # 被剔除的无关新闻

# 3. 配置 Gemini
client = genai.Client(api_key=GOOGLE_API_KEY)
generation_config = types.GenerateContentConfig(
    response_mime_type="application/json",
    temperature=0.14,
)

# ================= 辅助功能：国家数据映射 =================
# 从外部 CSV 文件加载国家数据
try:
    # 假设 CSV 文件在同一目录下
    _country_df = pd.read_csv("WorldCountryList.csv")
    COUNTRY_DB = {}
    
    for _, _row in _country_df.iterrows():
        _name_cn = str(_row['Country_CN']).strip()
        _bri = str(_row['BRI_Country']).strip()
        _ldc = str(_row['LDCs']).strip()

        COUNTRY_DB[_name_cn] = {
            "iso": str(_row['CountryISO']).strip(),
            "bri": _bri,
            "ldc": _ldc
        }
    print(f">>> 已加载国家列表，共 {len(COUNTRY_DB)} 条记录")

except Exception as e:
    print(f">>> [警告] 无法读取 WorldCountryList.csv: {e}")
    print(">>> 将使用内置的最小化国家列表作为后备")
    COUNTRY_DB = {
        "中国": {"iso": "CHN", "bri": "Yes", "ldc": "No"},
    }

def get_country_meta(country_name):
    """根据国家名返回 ISO, BRI, LDC 信息"""
    if not country_name or country_name == "NA":
        return {"iso": "NA", "bri": "NA", "ldc": "NA"}
    
    country_name = str(country_name).strip()
    
    # 1. 精确匹配
    if country_name in COUNTRY_DB:
        return COUNTRY_DB[country_name]
    
    # 2. 模糊匹配：如果数据库中的名字在提取的名字里，或者提取的名字在数据库名字里
    # 比如 "沙特" 匹配 "沙特阿拉伯"，"阿联酋" 匹配 "阿拉伯联合酋长国"
    for db_name, data in COUNTRY_DB.items():
        if db_name in country_name or country_name in db_name:
            return data
            
    # 3. 针对英文名匹配中文数据库的补充逻辑
    # 如果输入的 country_name 是英文，尝试匹配 CSV 中的 Country_EN 列
    try:
        # 重新读取一次 CSV 以便进行英文匹配（或者在初始化时建立英文索引）
        _df = pd.read_csv("WorldCountryList.csv")
        _match = _df[(_df['Country_EN'].str.contains(country_name, case=False, na=False)) | 
                     (country_name.lower() in _df['Country_EN'].str.lower().values)]
        if not _match.empty:
            _row = _match.iloc[0]
            return {
                "iso": str(_row['CountryISO']).strip(),
                "bri": str(_row['BRI_Country']).strip(),
                "ldc": str(_row['LDCs']).strip()
            }
    except:
        pass

    return {"iso": "NA", "bri": "NA", "ldc": "NA"}

# ================= 核心逻辑：Prompt 构建 =================
def build_prompt(title, content, date):
    # 注意：下面的 JSON 模板中，所有的大括号都使用了双重大括号 {{ }} 进行转义
    # 只有 {title}, {date}, {content} 是单层大括号，用于 Python 填入变量
    return f"""
    你是一个能源项目审核专家。请阅读输入的新闻标题和正文，提取符合要求的中国海外新能源电站项目信息，同时需要注意剔除与新能源电站无关的项目。

    文章发布日期：{date}
    文章标题：{title}
    文章正文：
    {content[:15000]} 
    (内容截断)

    ### 任务目标
    1. **筛选**：剔除无关新闻。
    2. **判断**：确认项目中是否有中国企业参与（包括作为业主、承包商、供应商等）。
    3. **提取**：提取项目详细信息，特别是多方参与和多子项的复杂情况。

    ### 中国企业参与判断规则（该规则优先级最高）
    如果新闻正文中无任何中国企业名称出现，或项目各环节参与者均为非中国企业，则直接标记为"no_chinese_participation"。。

    ### 内容筛选规则
    在提取数据前，请务必执行以下“去除无关信息”规则。符合任一规则的，直接标记为 "irrelevant"：
    
    1. **去除“非电站基建”和“纯化石能源电站”**：
       - 如果项目主体是学校、医院、机场、铁路、港口、农业灌溉、供水管网、工业园区等。
       - 即使文中提到“电力配套”、“供电工程”、“屋顶光伏改造”（若仅作为附属设施且未提及具体装机容量），也视为无关。
       - **原则**：我们要找的是“发电厂”，而不是“用电户”。
       - **补充规则**：如电站只涉及化石能源（如燃气、煤电、天然气等），即使项目是发电厂，也应视为无关。

    2. **去除“新能源制造业”**：
       - 如果项目是建设“光伏组件厂”、“锂电池制造基地”、“多晶硅工厂”、“新能源装备产业园”。
       - **区分关键**：看产出单位。如果是“GW/年（产能）”、“万吨/年”，则是工厂（无关）。如果是“MW/GW（装机容量）”且并网发电，才是电站（有效）。

    3. **去除“纯输变电”**：
       - 如果项目仅涉及“输电线路”、“变电站扩建”、“电网改造”、“升压站建设”等。
       - **例外**：只有当输变电工程是**专门配套**文中明确提及的某个**具体新能源电站**（如“为配套XX光伏电站建设的送出工程”）时，才保留，并提取那个**电站**的信息。

    4. **去除“项目检修信息”**：
        - 如果项目是进行“检修”、“维护”等，且未提及具体装机容量，则视为无关。

    5. **去除“模糊的组件供应信息”**：
        - 如项目只涉及“组件供应”、“组件销售”等，且未提及具体装机容量或电站项目信息，则视为无关。

    6. **去除仍在投标竞标阶段的信息**：
        - 如果项目只涉及到“投标”、“招标”、“竞标”、“投标意向”、“竞标意向”，且未提及与中国企业相关的具体的中标或签约信息，则视为无关。
        - 如果能够明确项目已经达成意向、中标、或签约，则视为有效。
 
    7. **去除只在获批阶段的项目信息**
        - 如果新闻中只提到某国政府部门“批准”或“审批通过”一系列项目，或者某些项目“获批”或“获得批准”，且未提及与中国企业相关的具体的中标或签约信息，则视为无关。

    8. **去除“商务会谈信息”**
        - 如果新闻中只提到各企业领导之间、或者企业与政府部门领导之间的访问、会议交流等，没有提到具体的项目信息，则视为无关。


    ### 关键处理规则
    1. **混合能源项目（如光伏+储能）**：
       - **Category**: 必须选择 "综合能源"。
       - **Capacity (MW)**: 请计算所有子项目的**总装机量**，仅填数字。例如：光伏100MW+储能50MW，此处填 "150"。
       - **Capacity Details**: 在此字段填写明细。例如："PV: 100MW, BESS: 50MW"。
    
    2. **单一能源项目**：
       - **Category**: 选对应的能源种类（如“光伏”）。
       - **Capacity (MW)**: 填数字。
       - **Capacity Details**: 重复具体描述，如没有信息则不填。

    3. **联合开发商**：
       - 多个业主用分号 ";" 隔开。

    4. **中方企业填写规则**：
       - 中方金融机构不需要填写在“Implementing Chinese Entity”当中，但需要在“Financing Model Details”字段中填写金融机构名称。
       - 中方金融机构包括进出口银行、中国银行、中国建设银行、中国工商银行、中国农业银行、中国民生银行、中国出口信用保险公司等。
       - 其他所有参与的中方企业（除金融机构外），如总包、分包、设备商等，都必须列入 `chinese_entities`。

    5. **日期推断与回填规则 (Date Inference)**：
       - **优先级 1 (明确日期)**：如果文中包含具体日期（如“2025年11月15日”），直接填入 `YYYY-MM-DD`。
       - **优先级 2 (近日/日前)**：如果文中仅提及 **“近日”、“日前”** 且表示动作**已发生**（如“近日已开工”、“日前成功并网”），请使用 **文章发布日期 ({date})** 的年份和月份，并将日期统一设为 **01日** (格式：`YYYY-MM-01`)。
       - **优先级 3 (预计年份)**：如果仅提及“预计2026年投产”，填入 `2026-01-01`。
       - **关键词严格映射**：
         - **(Planned) Start Date**：仅对应 [开工, 奠基, 破土动工, 浇筑第一罐混凝土]。**严禁**填入签约、中标、备案日期。
         - **(Planned) Commercial Operation Date**：仅对应 [并网, 投产, 商业运行, 竣工, 全容量并网]。

    6. **融资模式（Financing Models）规则**：
       - 如果文中提到“贷款”、“融资”、“信贷”、“银团贷款”、“双边贷款”、“买方信贷”、“优惠贷款”等，则填入“信贷融资”。
       - 如果文中提到“债券”、“公司债”、“点心债”、“绿色债券”等，则填入“债券融资”。
       - 如果文中提到“股权”、“股权融资”、“股权投资”等，则填入“股权融资”。
       - 如果文中提到“混合融资”，或者同一项目同时涉及信贷融资、债券融资、股权融资中的两项或两项以上，则填入“混合融资”。
       - 如果无法判断，则填入“Unknown”。


    ### 实体标准化知识库 (Knowledge Base)
    **请参考以下映射表填写 "Implementing Chinese Entity" (母公司) 和 "Subsidiary Name" (子公司)**。
    如果文中出现下列子公司，必须将其归入对应的母公司：

    **1. 工程建设双巨头**
    - **中国电建 (PowerChina)**:
      - 包含：中国水利水电X局 (Sinohydro), 山东电建 (SEPCO), 上海电建, 贵州工程, 华东/西北/中南/成都/昆明/贵阳等勘测设计研究院, 电建国际, 电建股份等。
    - **中国能建 (CEEC)**:
      - 包含：葛洲坝集团 (CGGC), 中电工程国际公司，中电工程 (CPECC, 东北/西北/西南/华东/华北/中南院), 广东火电, 浙江火电, 天津电建, 安徽电建, 中能建国际等。

    **2. 发电集团 (五大四小)**
    - **国家电投 (SPIC)**: 上海电力, 远达环保, 吉电股份, 中国电力国际 (China Power), 黄河水电, 国核工程。
    - **国家能源集团 (CHN Energy)**: 中国神华, 国电电力, **龙源电力**, 龙源技术, 长源电力。
    - **中国华能 (Huaneng)**: 华能国际, 华能新能源, 西安热工院, 澜沧江水电。
    - **中国大唐 (Datang)**: 大唐发电, 大唐新能源, 桂冠电力。
    - **中国华电 (Huadian)**: 华电国际, 黔源电力, 华电海外投资。
    - **三峡集团 (CTG)**: 长江电力, 三峡能源, 三峡国际。
    - **中广核 (CGN)**: 中广核新能源, 中广核电力, 中广核矿业。
    - **中核集团 (CNNC)**: 中国核电, 中核二二/二三/二四, 中国中原。
    - **华润电力 (CR Power)**: 华润电力控股。

    **3. 电网与装备**
    - **国家电网 (SGCC)**: 中国电科院, 许继集团, 平高集团, 各省电力公司。
    - **南方电网 (CSG)**: 广东/云南/贵州等电网公司, 广州电力交易中心。
    - **中国电气装备**: 许继电气, 平高电气, 中国西电。
    - **东方电气 / 哈电集团**: 及其下属电机/锅炉/汽轮机厂。
    - **上海电气**: 上海电气电站集团, 上海电气输配电集团（华普电缆、南华兰陵电气等）, 苏州天沃科技。

    **4. 知名民企**
    - **宁德时代 (CATL)**
    - **比亚迪 (BYD)**
    - **隆基绿能 (LONGi)**
    - **晶科能源 (Jinko)**
    - **晶澳科技 (JA Solar)**
    - **阳光电源 (Sungrow)**
    - **亿纬锂能 (EVE)**
    - **天合光能 (Trina Solar)**
    - **通威股份 (Tongwei)**
    - **金风科技 (Goldwind)**
    - **远景能源 (Envision)**
    

    **填写规则**：
    - 如果文中只提到以上列表里包含的子公司（如“葛洲坝集团签约...”），Implementing Entity 填对应的母公司，Subsidiary Name 填该子公司。
    - 如果文中只提到母公司，则Subsidiary Name处不填。
    - 如果文中公司不在上述列表中，请根据上下文自主判断母子公司关系。
    - 如果无法判断母子公司关系，则将企业名称填入"Implementing Chinese Entity"，"Subsidiary Name"内不填。

    
    ### 输出格式 (JSON)
    请严格返回符合以下 Schema 的 JSON：
    {{
      "analysis": {{
        "project_subject": "String", // 项目主体是什么？例如：某光伏电站 / 某锂电池工厂 / 某学校教学楼
        "primary_function": "String", // 核心功能：发电 / 制造 / 用电 / 输电
        "is_power_generation_plant": "Boolean", // 是否为以发电为目的的电站项目？(工厂填false)
        "reasoning": "String" // 用一句话解释为什么有效或无效
      }},
      "status": "String", // 枚举: "valid_project", "no_chinese_participation", "irrelevant"
      "projects": [ 
        {{
          "common_info": {{
            "Project Name": "String", //需要严格按照统一格式，为“国家+区域（省、州等二级行政区域）+装机容量（MW）+项目类型 + “项目”字样 + 补充信息（如标段，期数等，补充信息可不填）”
            "Country": "String", // 新闻正文中提到的项目所在国家
            "Location(State)": "String", // 新闻正文中提到的项目所在省份或地区，没提到则不填
            "Category": "String", // 混合项目选 "综合能源"
            "Capacity (MW)": "String", // 仅填写数字总和
            "Capacity Details": "String", // 填写混合项目的明细，如 "PV: 100MW, Storage: 50MW"
            "Project Stage": "String", // 只能选: [收购、中标、签约、开工、在建、竣工、投入运营、Unknown]
            "Project Developer": "String", //如文中同时出现多个业主，则用分号 ";" 隔开
            "(Planned) Start Date": "String", // YYYY-MM 或不填（如无法判断）
            "(Planned) Complete Date": "String", // YYYY-MM 或不填（如无法判断）
            "Total Project Investment (Million USD)": "String", 
            "Investment (or Financial Close) Date": "String", 
            "Involvement of Chinese Financial Insitution": "String", // 是/Unknown
            "Financing Model": "String", // 只能选: [信贷融资, 债券融资, 股权融资, 混合融资，其他, Unknown]
            "Financing Model Details": "String", // 根据新闻正文当中的具体信息以及“Financing Model”的选项，填写融资模式的详细信息
            "Official Website link": "String", // 不填写任何信息
            "Non-official website link": "String", // 不填写任何信息
            "Additional Remarks": "String" // 不填写任何信息
          }},
          "chinese_entities": [ 
            {{
              "Implementing Chinese Entity": "String", // 这里不填写金融机构，只填写除金融机构外的中方企业。
              "Subsidiary Name": "String", 
              "Type of involvement of Chinese Entities": "String", // 只能选: [BOT/BOOT, EPC总承包, 设计/施工服务方, 股权投资方, 设备供应商, 投融资+EPC, Unknown]
              "Subcontract Amount (Million USD)": "String"
            }}
          ]
        }}
      ]
    }}

    ### 字段填写补充说明
    - **Project Name**: 需要严格按照统一格式，为“国家+区域（省、州等二级行政区域）+装机容量（MW）+项目类型 + “项目”字样 + 补充信息（如标段，期数等，补充信息可不填）”
    - **Category**: 只能选 [光伏, 风电, 水电, 地热及余热, 生物质, 储能, 综合能源]。
    - **Implementing Chinese Entity**: 中方金融机构不需要填写在“Implementing Chinese Entity”当中，但需要在“Financing Model Details”字段中填写金融机构名称。
        - 中方金融机构包括进出口银行、中国银行、中国建设银行、中国工商银行、中国农业银行、中国民生银行、中国出口信用保险公司等。
        - 其他所有参与的中方企业（除金融机构外），如总包、分包、设备商等，都必须列入“Implementing Chinese Entity”。
    - **Type of involvement of Chinese Entities**: 只能选 [BOT/BOOT, EPC总承包, 设计/施工服务方, 股权投资方, 设备供应商, 投融资+EPC, Unknown]。
    - **Financing Model**: 只能选 [信贷融资, 债券融资, 股权融资, 混合融资，其他, Unknown]。
    - **Financing Model Details**: 根据新闻正文当中的具体信息以及“Financing Model”的选项，填写融资模式的详细信息。
    - **Project Stage**: 只能选 [收购、中标、签约、开工、在建、竣工、投入运营、Unknown]。
    - **未知信息处理**: 找不到的信息，除非是设定必须填入“Unknown”的情况，其余皆不填。
    """


# ================= 主程序逻辑 =================
def process_news_data():
    print("正在读取输入文件...")
    try:
        df = pd.read_excel(INPUT_FILE)
    except FileNotFoundError:
        print(f"错误：找不到文件 {INPUT_FILE}")
        return

    # 初始化存储结果的列表
    valid_rows = []
    no_chinese_rows = []
    discarded_rows = []
    all_ai_results = []  # 新增：用于统一存储所有 AI 返回的原始结果

    total_count = len(df)
    
    print(f"开始处理 {total_count} 条数据...")

    for index, row in df.iterrows():
        # 提取 Excel 中的原始字段
        title = str(row.get('文章标题', ''))
        content = str(row.get('正文内容', ''))
        pub_date = str(row.get('发布日期', ''))
        orig_link = str(row.get('原文链接', ''))
        
        # --- Python 端简单关键词预筛 (Pre-filter) ---
        # 如果正文中完全没有这些词，直接跳过 API 调用，省钱
        keywords = ["光伏","太阳能","风电","风力","水电","地热","储能","电力","电站","发电","MW","GW"]
        if not any(k in content for k in keywords):
            print(f"[{index+1}/{total_count}] Python预筛剔除: {title[:20]}...")
            discarded_rows.append({
                "文章标题": title,
                "发布日期": pub_date,
                "原文链接": orig_link,
                "正文内容": content,  # 新增：保存新闻原文
                "拒绝原因": "Python关键词预筛不匹配"
            })
            continue
        print("--------------------------------")
        # --- 调用 API ---
        print(f"[{index+1}/{total_count}] AI处理中: {title[:20]}...")
        prompt = build_prompt(title, content, pub_date)
        
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt,
                config=generation_config,
            )
            result = json.loads(response.text)

            # --- 【新增】获取 AI 的分析过程并打印，便于调试 ---
            analysis = result.get("analysis", {})
            status = result.get("status")
            reasoning = analysis.get("reasoning", "无详细理由")
            subject = analysis.get("project_subject", "未知主体")
            
            # 在控制台显示 AI 的判断逻辑，让你心里有底
            print(f"  -> 分析: [{subject}] - {reasoning}")
            print(f"  -> 结论: {status}")
            
            # --- 修改：不再单独保存每个 JSON，而是存入总列表 ---
            all_ai_results.append({
                "index": index + 1,
                "title": title,
                "result": result
            })
            
            status = result.get("status")
            
            # --- 分流逻辑 ---
            if status == "irrelevant":
                discarded_rows.append({
                    "文章标题": title,
                    "发布日期": pub_date,
                    "原文链接": orig_link,
                    "正文内容": content,  # 新增：保存新闻原文
                    "拒绝原因": f"[{subject}] {reasoning}"
                })
            
            elif status == "no_chinese_participation":
                # 即使无中方，也可以尝试提取项目基本信息（取决于你是否需要）
                # 这里简单处理，只存基本信息
                no_chinese_rows.append({
                    "文章标题": title,
                    "发布日期": pub_date,
                    "原文链接": orig_link,
                    "备注": "AI判定无中方参与"
                })
                
            elif status == "valid_project":
                projects = result.get("projects", [])
                for proj in projects:
                    common = proj.get("common_info", {})
                    entities = proj.get("chinese_entities", [])
                    
                    # 自主判断字段 (查表)
                    country_name = common.get("Country", "")
                    # 尝试从正文中提取中文国家名进行匹配，因为 AI 提取的 Country 可能是英文
                    # 如果 AI 提取的是英文，而 COUNTRY_DB 的键是中文，则会匹配失败
                    country_meta = get_country_meta(country_name)
                    
                    # 准备通用数据
                    base_data = {
                        "Project Name": common.get("Project Name", ""),
                        "Country": country_name,
                        "Country ISO": country_meta["iso"],
                        "BRI country": country_meta["bri"],
                        "LDCs": country_meta["ldc"],
                        "Location (State)": common.get("Location(State)", ""),
                        "Category": common.get("Category", ""),
                        "Capacity (MW)": common.get("Capacity (MW)", ""),                     
                        # --- 【新增】Capacity Details 字段，紧跟在规模之后 ---
                        "Capacity Details": common.get("Capacity Details", ""),
                        "Project Stage": common.get("Project Stage", ""),
                        "Project Developer": common.get("Project Developer", ""),
                        # 逻辑：如果实体列表大于1，则为“Yes”
                        "Multiple Chinese Enterprises Involved": "Yes" if len(entities) > 1 else "No",
                        "(Planned) Start Date": common.get("(Planned) Start Date", ""),
                        "(Planned) Complete Date": common.get("(Planned) Complete Date", ""),
                        "Total Project Investment (Million USD)": common.get("Total Project Investment (Million USD)", ""),
                        "Investment (or Financial Close) Date": common.get("Investment (or Financial Close) Date", ""),
                        "Involvement of Chinese Banks": common.get("Involvement of Chinese Banks", ""),
                        "Financing Model": common.get("Financing Model", ""), # 固定值
                        "Financing Model Details": common.get("Financing Model Details", ""), # 固定值
                        "Publish Date": pub_date, # 回填原Excel日期        
                        "Source Link": orig_link, # 回填原Excel链接
                        "Official Website link": "",
                        "Non-official website link": "",
                        "Additional Remarks": ""
                    }
                    
                    # 炸裂 (Explode) 实体：一行变多行
                    if not entities:
                        # 极其罕见的情况：AI说是valid但没提取到实体，留空占位
                        row_data = base_data.copy()
                        row_data.update({
                            "Implementing Chinese Entity": "",
                            "Subsidiary Name": "",
                            "Type of involvement of Chinese Entities": "",
                            "Subcontract Amount (Million USD)": ""
                        })
                        valid_rows.append(row_data)
                    else:
                        for entity in entities:
                            row_data = base_data.copy()
                            row_data.update({
                                "Implementing Chinese Entity": entity.get("Implementing Chinese Entity", ""),
                                "Subsidiary Name": entity.get("Subsidiary Name", ""),
                                "Type of involvement of Chinese Entities": entity.get("Type of involvement of Chinese Entities", ""),
                                "Subcontract Amount (Million USD)": entity.get("Subcontract Amount (Million USD)", "")
                            })
                            valid_rows.append(row_data)

        except Exception as e:
            print(f"  -> 处理出错: {e}")
            # 出错也记录，防止漏数据
            discarded_rows.append({
                "文章标题": title, 
                "正文内容": content,  # 新增：保存新闻原文
                "拒绝原因": f"程序异常: {str(e)}"
            })
        
        # 避免触发 API 速率限制 (Flash 限制较宽，但保险起见)
        time.sleep(1)

    # ================= 保存文件 =================
    print("\n处理完成，正在保存文件...")
    
    # 1. 保存有效数据库
    if valid_rows:
        df_valid = pd.DataFrame(valid_rows)
        # 调整列顺序以符合你的要求
        cols_order = [
            "Project Name", "Country", "Country ISO",
            "BRI country", "LDCs", "Location (State)",
            "Category", "Capacity (MW)", "Capacity Details", "Project Stage", "Project Developer",
            "Multiple Chinese Enterprises Involved",
            "Implementing Chinese Entity", "Subsidiary Name",
            "Type of involvement of Chinese Entities",
            "(Planned) Start Date", "(Planned) Complete Date",
            "Total Project Investment (Million USD)", "Subcontract Amount (Million USD)",
            "Investment (or Financial Close) Date",
            "Involvement of Chinese Banks", "Financing Model",
            "Financing Model Details", "Source Link", "Publish Date",
            "Official Website link", "Non-official website link", "Additional Remarks"
        ]
        # 仅保留存在的列
        final_cols = [c for c in cols_order if c in df_valid.columns]
        df_valid[final_cols].to_excel(OUTPUT_VALID, index=False)
        print(f"✅ 有效项目已保存至: {OUTPUT_VALID} (共 {len(df_valid)} 行)")
    else:
        print("⚠️ 没有提取到有效项目。")

    # 2. 保存无中方参与项目
    if no_chinese_rows:
        pd.DataFrame(no_chinese_rows).to_excel(OUTPUT_NO_CHINESE, index=False)
        print(f"✅ 无中方参与项目已保存至: {OUTPUT_NO_CHINESE}")

    # 3. 保存被剔除新闻
    if discarded_rows:
        pd.DataFrame(discarded_rows).to_excel(OUTPUT_DISCARDED, index=False)
        print(f"✅ 被剔除新闻已保存至: {OUTPUT_DISCARDED}")

    # 4. 保存所有 AI 原始返回结果到一个 JSON 文件
    if all_ai_results:
        json_output_name = "All_AI_Results.json"
        with open(json_output_name, "w", encoding="utf-8") as f:
            json.dump(all_ai_results, f, ensure_ascii=False, indent=2)
        print(f"✅ 所有 AI 原始结果已保存至: {json_output_name}")

if __name__ == "__main__":
    process_news_data()