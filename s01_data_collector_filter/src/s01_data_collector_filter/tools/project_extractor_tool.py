import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Type

import pandas as pd
from crewai.tools import BaseTool
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

load_dotenv()

# 工具文件位于 src/s01_data_collector_filter/tools/，上溯三层到子项目根目录
_S01_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_COUNTRY_DB = str(_S01_ROOT / "WorldCountryList.json")


# ── 国家数据库 ─────────────────────────────────────────────────────────────────

def _load_country_db(json_path: str) -> dict:
    """
    从 JSON 文件加载国家映射表。
    每个国家的正式中文名和所有 aliases_cn 均展开为独立键，指向同一条记录。
    失败时返回最小化内置表。
    """
    try:
        with open(json_path, encoding="utf-8") as f:
            countries = json.load(f)
        db = {}
        for c in countries:
            record = {
                "iso":     c["iso"],
                "bri":     c["bri"],
                "ldc":     c["ldc"],
                "name_en": c.get("name_en", ""),
            }
            db[c["name_cn"]] = record
            for alias in c.get("aliases_cn", []):
                db[alias] = record
        n_countries = len(countries)
        n_keys = len(db)
        print(f">>> 已加载国家列表：{n_countries} 个国家，{n_keys} 个名称/别名键")
        return db
    except Exception as e:
        print(f">>> [警告] 无法读取国家列表 {json_path}: {e}，使用内置最小列表")
        return {"中国": {"iso": "CHN", "bri": "Yes", "ldc": "No", "name_en": "China"}}


def _get_country_meta(country_name: str, db: dict) -> dict:
    """根据国家名查询 ISO / BRI / LDC 信息，支持精确与模糊匹配。"""
    empty = {"iso": "NA", "bri": "NA", "ldc": "NA"}
    if not country_name or country_name == "NA":
        return empty

    country_name = str(country_name).strip()

    # 1. 精确匹配（中文名）
    if country_name in db:
        return db[country_name]

    # 2. 模糊匹配（中文名互含）
    for db_name, data in db.items():
        if db_name in country_name or country_name in db_name:
            return data

    # 3. 英文名匹配
    for db_name, data in db.items():
        en = data.get("name_en", "")
        if en and (en.lower() in country_name.lower() or country_name.lower() in en.lower()):
            return data

    return empty


# ── Prompt 构建 ────────────────────────────────────────────────────────────────

def _build_prompt(title: str, content: str, date: str) -> str:
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
    如果新闻正文中无任何中国企业名称出现，或项目各环节参与者均为非中国企业，则直接标记为"no_chinese_participation"。

    ### 内容筛选规则
    在提取数据前，请务必执行以下"去除无关信息"规则。符合任一规则的，直接标记为 "irrelevant"：

    1. **去除"纯化石能源电站"**：
       - 如电站只涉及化石能源（如燃气、煤电、火电、燃气轮机、天然气等），且未与其他新能源发电形式结合，应视为无关。

    2. **去除"非电站基建"**：
       - 如果项目主体是学校、医院、机场、铁路、港口、农业灌溉、供水管网、工业园区等。
       - 即使文中提到"电力配套"、"供电工程"、"屋顶光伏改造"（若仅作为附属设施且未提及具体装机容量），也视为无关。
       - **原则**：我们要找的是"发电厂"，而不是"用电户"。
       
    3. **去除"新能源制造业"**：
       - 如果项目是建设"光伏组件厂"、"锂电池制造基地"、"多晶硅工厂"、"新能源装备产业园"。
       - **区分关键**：看产出单位。如果是"GW/年（产能）"、"万吨/年"，则是工厂（无关）。如果是"MW/GW（装机容量）"且并网发电，才是电站（有效）。

    4. **去除"纯输变电"**：
       - 如果项目仅涉及"输电线路"、"变电站扩建"、"电网改造"、"升压站建设"等。
       - **例外**：只有当输变电工程是**专门配套**文中明确提及的某个**具体新能源电站**时，才保留，并提取那个**电站**的信息。

    5. **去除"项目检修信息"**：
        - 如果项目是进行"检修"、"维护"等，且未提及具体装机容量，则视为无关。

    6. **去除"模糊的组件供应信息"**：
        - 如项目只涉及"组件供应"、"组件销售"等，且未提及具体装机容量或电站项目信息，则视为无关。

    7. **去除仍在投标竞标阶段的信息**：
        - 如果项目只涉及到"投标"、"招标"、"竞标"、"投标意向"、"竞标意向"，且未提及与中国企业相关的具体的中标或签约信息，则视为无关。
        - 如果能够明确项目已经达成意向、中标、或签约，则视为有效。

    8. **去除只在获批阶段的项目信息**
        - 如果新闻中只提到某国政府部门"批准"或"审批通过"一系列项目，或者某些项目"获批"或"获得批准"，且未提及与中国企业相关的具体的中标或签约信息，则视为无关。

    9. **去除"商务会谈信息"**
        - 如果新闻中只提到各企业领导之间、或者企业与政府部门领导之间的访问、会议交流等，没有提到具体的项目信息，则视为无关。

    ### 关键处理规则
    1. **混合能源项目（如光伏+储能）**：
       - **Category**: 必须选择 "综合能源"。
       - **Capacity (MW)**: 请计算所有子项目的**总装机量**，仅填数字。例如：光伏100MW+储能50MW，此处填 "150"。
       - **Capacity Details**: 在此字段填写明细。例如："PV: 100MW, BESS: 50MW"。

    2. **单一能源项目**：
       - **Category**: 选对应的能源种类（如"光伏"）。
       - **Capacity (MW)**: 填数字。
       - **Capacity Details**: 重复具体描述，如没有信息则不填。

    3. **联合开发商**：
       - 多个业主用分号 ";" 隔开。

    4. **中方企业填写规则**：
       - 中方金融机构不需要填写在"Implementing Chinese Entity"当中，但需要在"Financing Model Details"字段中填写金融机构名称。
       - 中方金融机构包括进出口银行、中国银行、中国建设银行、中国工商银行、中国农业银行、中国民生银行、中国出口信用保险公司等。
       - 其他所有参与的中方企业（除金融机构外），如总包、分包、设备商等，都必须列入 `chinese_entities`。

    5. **日期推断与回填规则 (Date Inference)**：
       - **优先级 1 (明确日期)**：如果文中包含具体日期（如"2025年11月15日"），直接填入 `YYYY-MM-DD`。
       - **优先级 2 (近日/日前)**：如果文中仅提及 **"近日"、"日前"** 且表示动作**已发生**，请使用 **文章发布日期 ({date})** 的年份和月份，并将日期统一设为 **01日** (格式：`YYYY-MM-01`)。
       - **优先级 3 (预计年份)**：如果仅提及"预计2026年投产"，填入 `2026-01-01`。
       - **关键词严格映射**：
         - **(Planned) Start Date**：仅对应 [开工, 奠基, 破土动工, 浇筑第一罐混凝土]。**严禁**填入签约、中标、备案日期。
         - **(Planned) Commercial Operation Date**：仅对应 [并网, 投产, 商业运行, 竣工, 全容量并网]。

    6. **融资模式（Financing Models）规则**：
       - 如果文中提到"贷款"、"融资"、"信贷"、"银团贷款"、"双边贷款"、"买方信贷"、"优惠贷款"等，则填入"信贷融资"。
       - 如果文中提到"债券"、"公司债"、"点心债"、"绿色债券"等，则填入"债券融资"。
       - 如果文中提到"股权"、"股权融资"、"股权投资"等，则填入"股权融资"。
       - 如果文中提到"混合融资"，或者同一项目同时涉及信贷融资、债券融资、股权融资中的两项或两项以上，则填入"混合融资"。
       - 如果无法判断，则填入"Unknown"。

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
    - **宁德时代 (CATL)**, **比亚迪 (BYD)**, **隆基绿能 (LONGi)**, **晶科能源 (Jinko)**
    - **晶澳科技 (JA Solar)**, **阳光电源 (Sungrow)**, **亿纬锂能 (EVE)**, **天合光能 (Trina Solar)**
    - **通威股份 (Tongwei)**, **金风科技 (Goldwind)**, **远景能源 (Envision)**

    **填写规则**：
    - 如果文中只提到以上列表里包含的子公司（如"葛洲坝集团签约..."），Implementing Entity 填对应的母公司，Subsidiary Name 填该子公司。
    - 如果文中只提到母公司，则Subsidiary Name处不填。
    - 如果文中公司不在上述列表中，请根据上下文自主判断母子公司关系。
    - 如果无法判断母子公司关系，则将企业名称填入"Implementing Chinese Entity"，"Subsidiary Name"内不填。

    ### 输出格式 (JSON)
    请严格返回符合以下 Schema 的 JSON：
    {{
      "analysis": {{
        "project_subject": "String",
        "primary_function": "String",
        "is_power_generation_plant": "Boolean",
        "reasoning": "String"
      }},
      "status": "String",
      "projects": [
        {{
          "common_info": {{
            "Project Name": "String",
            "Project Name (EN)": "String",
            "Country": "String",
            "Location(State)": "String",
            "Category": "String",
            "Capacity (MW)": "String",
            "Capacity Details": "String",
            "Project Stage": "String",
            "Project Developer": "String",
            "(Planned) Start Date": "String",
            "(Planned) Complete Date": "String",
            "Total Project Investment (Million USD)": "String",
            "Investment (or Financial Close) Date": "String",
            "Involvement of Chinese Financial Insitution": "String",
            "Financing Model": "String",
            "Financing Model Details": "String",
            "Official Website link": "String",
            "Non-official website link": "String",
            "Additional Remarks": "String"
          }},
          "chinese_entities": [
            {{
              "Implementing Chinese Entity": "String",
              "Implementing Chinese Entity (EN)": "String",
              "Subsidiary Name": "String",
              "Subsidiary Name (EN)": "String",
              "Type of involvement of Chinese Entities": "String",
              "Subcontract Amount (Million USD)": "String"
            }}
          ]
        }}
      ]
    }}

    ### 字段填写补充说明
    - **Project Name**: 【必须使用中文填写】格式："国家+区域/省份+装机容量(MW)+项目类型+"项目"+补充说明"。示例："巴基斯坦旁遮普省200MW光伏项目"、"埃塞俄比亚500MW水电项目（二期）"。国家名、项目类型必须用中文；区域/省份无通用中文译名时可保留英文（如"Sindh省"）；【严禁】直接照抄英文原文（如"Pakistan 200MW Solar Project"）。
    - **Project Name (EN)**: 项目名称的英文版本。优先使用项目的官方英文名（如"Karot Hydropower Project"）；若无官方名，则按格式翻译："Country + Capacity MW + Energy Type + Project/Plant [补充说明]"，示例："Pakistan 200MW Solar PV Project (Punjab Province)"、"Ethiopia 500MW Hydropower Project (Phase II)"。
    - **Category**: 只能选 [光伏, 风电, 水电, 地热及余热, 生物质, 储能, 综合能源]。
    - **status**: 枚举值 "valid_project" | "no_chinese_participation" | "irrelevant"
    - **Type of involvement of Chinese Entities**: 只能选 [BOT/BOOT, EPC总承包, 设计/施工服务方, 股权投资方, 设备供应商, 投融资+EPC, Unknown]。
    - **Financing Model**: 只能选 [信贷融资, 债券融资, 股权融资, 混合融资, 其他, Unknown]。
    - **Project Stage**: 只能选 [收购, 中标, 签约, 开工, 在建, 竣工, 投入运营, Unknown]。
    - **Implementing Chinese Entity (EN)**: 中方企业的官方英文名。请优先使用上方知识库括号内的英文名称，如"PowerChina"、"China Three Gorges Corporation (CTG)"、"CEEC"、"Huaneng"等。知识库未收录的企业，提供合理的英文译名。
    - **Subsidiary Name (EN)**: 子公司的官方英文名或英文译名。如"Sinohydro Bureau 5"、"SEPCO III"等。
    - **未知信息处理**: 找不到的信息，除非是设定必须填入"Unknown"的情况，其余皆不填。
    - **Official/Non-official Website link** 和 **Additional Remarks**: 不填写任何信息。
    """


# ── Input Schema ──────────────────────────────────────────────────────────────

class ProjectExtractorInput(BaseModel):
    input_file: str = Field(
        ...,
        description="爬虫产出的 Excel 文件路径（含发布日期、文章标题、正文内容、原文链接字段）",
    )
    output_dir: str = Field(
        default="data/processed",
        description="输出文件保存目录",
    )
    country_db_path: str = Field(
        default=_DEFAULT_COUNTRY_DB,
        description="国家数据库 JSON 文件路径，用于补充 ISO、BRI、LDC 信息及别名匹配",
    )


# ── Tool ──────────────────────────────────────────────────────────────────────

class ProjectExtractorTool(BaseTool):
    name: str = "project_extractor_tool"
    description: str = (
        "读取爬虫产出的新闻 Excel 文件，逐条调用 AI 进行筛选和结构化提取，"
        "将结果分三类输出：有效项目数据库、无中方参与项目、被剔除无关新闻。"
        "返回各类文件路径和数量摘要。"
    )
    args_schema: Type[BaseModel] = ProjectExtractorInput

    def _run(
        self,
        input_file: str,
        output_dir: str = "data/processed",
        country_db_path: str = _DEFAULT_COUNTRY_DB,
    ) -> str:
        # 初始化 Gemini
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return "错误：未找到 GEMINI_API_KEY 环境变量，请检查 .env 文件。"

        client = genai.Client(api_key=api_key)
        generation_config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.14,
        )

        # 加载国家数据库
        country_db = _load_country_db(country_db_path)

        # 读取输入文件
        try:
            df = pd.read_excel(input_file)
        except FileNotFoundError:
            return f"错误：找不到输入文件 {input_file}"

        os.makedirs(output_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M")

        valid_rows, no_chinese_rows, discarded_rows, all_ai_results = [], [], [], []
        total = len(df)
        print(f"开始处理 {total} 条数据...")

        pre_filter_keywords = [
            "光伏", "太阳能", "风电", "风力", "水电", "地热", "储能",
            "电力", "电站", "发电", "MW", "GW",
        ]

        for idx, row in df.iterrows():
            title = str(row.get("文章标题", ""))
            content = str(row.get("正文内容", ""))
            pub_date = str(row.get("发布日期", ""))
            orig_link = str(row.get("原文链接", ""))

            # Python 端预筛，节省 API 调用
            if not any(k in content for k in pre_filter_keywords):
                print(f"[{idx+1}/{total}] Python预筛剔除: {title[:25]}...")
                discarded_rows.append({
                    "文章标题": title,
                    "发布日期": pub_date,
                    "原文链接": orig_link,
                    "正文内容": content,
                    "拒绝原因": "Python关键词预筛不匹配",
                })
                continue

            print("--------------------------------")
            print(f"[{idx+1}/{total}] AI处理中: {title[:25]}...")
            prompt = _build_prompt(title, content, pub_date)

            try:
                response = client.models.generate_content(
                    model="gemini-2.5-flash-lite",
                    contents=prompt,
                    config=generation_config,
                )
                result = json.loads(response.text)

                analysis = result.get("analysis", {})
                status = result.get("status")
                reasoning = analysis.get("reasoning", "无详细理由")
                subject = analysis.get("project_subject", "未知主体")

                print(f"  -> 分析: [{subject}] - {reasoning}")
                print(f"  -> 结论: {status}")

                all_ai_results.append({"index": idx + 1, "title": title, "result": result})

                if status == "irrelevant":
                    discarded_rows.append({
                        "文章标题": title,
                        "发布日期": pub_date,
                        "原文链接": orig_link,
                        "正文内容": content,
                        "拒绝原因": f"[{subject}] {reasoning}",
                    })

                elif status == "no_chinese_participation":
                    no_chinese_rows.append({
                        "文章标题": title,
                        "发布日期": pub_date,
                        "原文链接": orig_link,
                        "备注": "AI判定无中方参与",
                    })

                elif status == "valid_project":
                    for proj in result.get("projects", []):
                        common = proj.get("common_info", {})
                        entities = proj.get("chinese_entities", [])
                        country_name = common.get("Country", "")
                        meta = _get_country_meta(country_name, country_db)

                        base = {
                            "Project Name": common.get("Project Name", ""),
                            "Project Name (EN)": common.get("Project Name (EN)", ""),
                            "Name EN Quality": "ai_translated",
                            "Country": country_name,
                            "Country ISO": meta["iso"],
                            "BRI country": meta["bri"],
                            "LDCs": meta["ldc"],
                            "Location (State)": common.get("Location(State)", ""),
                            "Category": common.get("Category", ""),
                            "Capacity (MW)": common.get("Capacity (MW)", ""),
                            "Capacity Details": common.get("Capacity Details", ""),
                            "Project Stage": common.get("Project Stage", ""),
                            "Project Developer": common.get("Project Developer", ""),
                            "Multiple Chinese Enterprises Involved": (
                                "Yes" if len(entities) > 1 else "No"
                            ),
                            "(Planned) Start Date": common.get("(Planned) Start Date", ""),
                            "(Planned) Complete Date": common.get("(Planned) Complete Date", ""),
                            "Total Project Investment (Million USD)": common.get(
                                "Total Project Investment (Million USD)", ""
                            ),
                            "Investment (or Financial Close) Date": common.get(
                                "Investment (or Financial Close) Date", ""
                            ),
                            "Involvement of Chinese Banks": common.get(
                                "Involvement of Chinese Financial Insitution", ""
                            ),
                            "Financing Model": common.get("Financing Model", ""),
                            "Financing Model Details": common.get("Financing Model Details", ""),
                            "Publish Date": pub_date,
                            "Source Link": orig_link,
                            "Official Website link": "",
                            "Non-official website link": "",
                            "Additional Remarks": "",
                        }

                        if not entities:
                            row_data = base.copy()
                            row_data.update({
                                "Implementing Chinese Entity": "",
                                "Implementing Chinese Entity (EN)": "",
                                "Subsidiary Name": "",
                                "Subsidiary Name (EN)": "",
                                "Type of involvement of Chinese Entities": "",
                                "Subcontract Amount (Million USD)": "",
                            })
                            valid_rows.append(row_data)
                        else:
                            for entity in entities:
                                row_data = base.copy()
                                row_data.update({
                                    "Implementing Chinese Entity": entity.get(
                                        "Implementing Chinese Entity", ""
                                    ),
                                    "Implementing Chinese Entity (EN)": entity.get(
                                        "Implementing Chinese Entity (EN)", ""
                                    ),
                                    "Subsidiary Name": entity.get("Subsidiary Name", ""),
                                    "Subsidiary Name (EN)": entity.get("Subsidiary Name (EN)", ""),
                                    "Type of involvement of Chinese Entities": entity.get(
                                        "Type of involvement of Chinese Entities", ""
                                    ),
                                    "Subcontract Amount (Million USD)": entity.get(
                                        "Subcontract Amount (Million USD)", ""
                                    ),
                                })
                                valid_rows.append(row_data)

            except Exception as e:
                print(f"  -> 处理出错: {e}")
                discarded_rows.append({
                    "文章标题": title,
                    "正文内容": content,
                    "拒绝原因": f"程序异常: {str(e)}",
                })

            time.sleep(1)

        # ── 保存结果 ──────────────────────────────────────────────────────────
        saved_files = []

        col_order = [
            "Project Name", "Project Name (EN)", "Name EN Quality",
            "Country", "Country ISO", "BRI country", "LDCs",
            "Location (State)", "Category", "Capacity (MW)", "Capacity Details",
            "Project Stage", "Project Developer", "Multiple Chinese Enterprises Involved",
            "Implementing Chinese Entity", "Implementing Chinese Entity (EN)",
            "Subsidiary Name", "Subsidiary Name (EN)",
            "Type of involvement of Chinese Entities",
            "(Planned) Start Date", "(Planned) Complete Date",
            "Total Project Investment (Million USD)", "Subcontract Amount (Million USD)",
            "Investment (or Financial Close) Date",
            "Involvement of Chinese Banks", "Financing Model", "Financing Model Details",
            "Source Link", "Publish Date",
            "Official Website link", "Non-official website link", "Additional Remarks",
        ]

        if valid_rows:
            path = os.path.join(output_dir, f"BRI_RE_Final_Database_{ts}.xlsx")
            df_v = pd.DataFrame(valid_rows)
            final_cols = [c for c in col_order if c in df_v.columns]
            df_v[final_cols].to_excel(path, index=False)
            saved_files.append(f"有效项目 ({len(df_v)} 行): {os.path.abspath(path)}")

        if no_chinese_rows:
            path = os.path.join(output_dir, f"BRI_RE_No_Chinese_{ts}.xlsx")
            pd.DataFrame(no_chinese_rows).to_excel(path, index=False)
            saved_files.append(f"无中方参与 ({len(no_chinese_rows)} 条): {os.path.abspath(path)}")

        if discarded_rows:
            path = os.path.join(output_dir, f"BRI_RE_Discarded_{ts}.xlsx")
            pd.DataFrame(discarded_rows).to_excel(path, index=False)
            saved_files.append(f"被剔除新闻 ({len(discarded_rows)} 条): {os.path.abspath(path)}")

        if all_ai_results:
            path = os.path.join(output_dir, f"All_AI_Results_{ts}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(all_ai_results, f, ensure_ascii=False, indent=2)
            saved_files.append(f"AI原始结果 ({len(all_ai_results)} 条): {os.path.abspath(path)}")

        if not saved_files:
            return "处理完成，但未生成任何输出文件（无符合条件的数据）。"

        return "提取完成。输出文件：\n" + "\n".join(f"  - {f}" for f in saved_files)
