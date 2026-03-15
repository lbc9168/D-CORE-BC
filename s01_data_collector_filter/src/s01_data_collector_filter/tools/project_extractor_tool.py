import json
import os
import sqlite3
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
_DEFAULT_COUNTRY_DB  = str(_S01_ROOT / "WorldCountryList.json")
_DEFAULT_PROJECTS_DB = str(_S01_ROOT / "data" / "projects.db")
_DEFAULT_REVIEW_DB   = str(_S01_ROOT / "data" / "review.db")


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


# ── SQLite 初始化 ──────────────────────────────────────────────────────────────

def _init_projects_db(db_path: str) -> sqlite3.Connection:
    """初始化 projects.db，存储有效提取项目（含增量导入标志）。"""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS extracted_projects (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            record_type             TEXT,
            project_name            TEXT,
            project_name_en         TEXT,
            name_en_quality         TEXT,
            country                 TEXT,
            country_iso             TEXT,
            bri_country             TEXT,
            ldc                     TEXT,
            location                TEXT,
            category                TEXT,
            capacity_mw             TEXT,
            capacity_details        TEXT,
            project_stage           TEXT,
            project_developer       TEXT,
            multiple_chinese        TEXT,
            implementing_entity     TEXT,
            implementing_entity_en  TEXT,
            subsidiary_name         TEXT,
            subsidiary_name_en      TEXT,
            involvement_type        TEXT,
            subcontract_amount_usd  TEXT,
            planned_start_date      TEXT,
            planned_complete_date   TEXT,
            total_investment_usd    TEXT,
            investment_date         TEXT,
            financing_model         TEXT,
            financing_model_details TEXT,
            source_link             TEXT,
            publish_date            TEXT,
            official_website        TEXT,
            non_official_website    TEXT,
            additional_remarks      TEXT,
            batch_id                TEXT,
            imported_to_s02a        INTEGER DEFAULT 0,
            created_at              TEXT
        )
    """)
    conn.commit()
    return conn


def _init_review_db(db_path: str) -> sqlite3.Connection:
    """初始化 review.db，存储无中方参与和被过滤的文章记录。"""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS no_chinese_records (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            title          TEXT,
            source_link    TEXT UNIQUE,
            publish_date   TEXT,
            batch_id       TEXT,
            created_at     TEXT
        );
        CREATE TABLE IF NOT EXISTS discarded_records (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            title          TEXT,
            source_link    TEXT UNIQUE,
            publish_date   TEXT,
            discard_reason TEXT,
            batch_id       TEXT,
            created_at     TEXT
        );
    """)
    conn.commit()
    return conn


# ── Prompt 构建 ────────────────────────────────────────────────────────────────

def _build_prompt(title: str, content: str, date: str) -> str:
    return f"""
    你是一个能源项目审核专家。请阅读输入的新闻标题和正文，按要求提取符合要求的中国海外能源项目信息，同时需要注意剔除无关的项目。

    文章发布日期：{date}
    文章标题：{title}
    文章正文：
    {content[:15000]}
    (内容截断)

    ### 任务目标
    1. **判断**：确认项目中是否有中国企业参与（包括作为业主、承包商、供应商等）。
    2. **筛选**：剔除无关新闻。
    3. **提取**：提取项目详细信息，特别是多方参与和多子项的复杂情况。

    ### 中国企业参与判断规则（该规则优先级最高）
    如果新闻正文中无任何中国企业名称出现，或项目各环节参与者均为非中国企业，则直接标记为"no_chinese_participation"。

    ### 内容筛选规则
    在提取数据前，请务必执行以下"去除无关信息"规则。符合任一规则的，直接标记为 "irrelevant"：

    1. **去除"与能源无关的基建项目"**：
       - 如果项目主体是学校、医院、机场、铁路、港口、农业灌溉、供水管网、工业园区等。
       - 即使文中提到"电力配套"、"供电工程"、"屋顶光伏改造"（若仅作为附属设施且未提及具体装机容量），也视为无关。
       - **原则**：我们要找的是"发电厂"，而不是"用电户"。

    2. **去除"项目检修和改造信息"**：
        - 如果项目是进行"检修"、"维护"、"改造"、“升级”等，且未提及具体装机容量，则视为无关。

    3. **去除"模糊的组件供应信息"**：
        - 如项目只涉及"组件供应"、"组件销售"等，且未提及具体装机容量或电站项目信息，则视为无关。

    4. **去除只在获批阶段，且无中企参与信息的项目信息**
        - 如果新闻中只提到某国政府部门"批准"或"审批通过"一系列项目，或者某些项目"获批"或"获得批准"，且未提及与中国企业相关的具体的中标或签约信息，则视为无关。

    5. **去除"商务会谈信息"**
        - 如果新闻中只提到各企业领导之间、或者企业与政府部门领导之间的访问、会议交流等，没有提到具体的项目信息，则视为无关。

    ### 关键处理规则
    1. **混合能源项目（如光伏+储能）**：
       - **Category**: 必须选择 "综合能源"。
       - **Capacity (MW)**: 请计算所有子项目的**总装机量**，仅填数字。例如：光伏100MW+储能50MW，此处填 "150"。
       - **Capacity Details**: 在此字段填写明细。例如："PV: 100MW, BESS: 50MW"。

    2. **单一能源项目**：
       - **Category**: 选对应的能源种类（如"光伏"）。
       - **Capacity (MW)**: 填数字。
       - **Capacity Details**: 从新闻原文中提取与能源种类和装机容量相关的信息填入，如没有信息则不填。

    3. **联合开发商（multiple project developers）**：
       - 多个业主或开发商用分号 ";" 隔开。

    4. **中方企业填写规则**：
       - 所有参与的中方企业（包括总包、分包、设备商、股权投资方、金融机构等）均需列入 `chinese_entities`。
       - 对于中方金融机构（如进出口银行、国家开发银行、中国银行、工商银行、建设银行、农业银行、民生银行、中国出口信用保险公司等），其 "Type of involvement" 必须填写 **"融资方"**，同时在 "Financing Model Details" 中填写该机构名称。
       - 注意区分：**"融资方"** 仅用于纯金融机构（只提供贷款/担保，不参与工程实施）；**"投融资+EPC"** 用于同一企业既作为股权投资方又承担 EPC 工程任务的情况。

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
            "Record Type": "String",
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
    - **Record Type**: 描述本条记录的性质，只能选 [具体项目, 设备订单, 一般合作]。
        - **具体项目**：新闻指向一个可识别的具体工程项目（有项目名、地点、装机容量等信息）。
        - **设备订单**：中国企业向某国/某地区签订设备或零部件供货合同，但新闻中没有出现具体的工程项目名称或明确的目标电站。
        - **一般合作**：MOU/框架协议/战略合作协议等，尚无具体项目可指。
        - **判断优先级**：只要新闻中出现了具体的项目名称或电站，即判定为"具体项目"，即使中方角色是设备供应商。
    - **Project Name**: 【必须使用中文填写】格式："国家+区域/省份+装机容量(MW)+项目类型+"项目"+补充说明"。示例："巴基斯坦旁遮普省200MW光伏项目"、"埃塞俄比亚500MW水电项目（二期）"。国家名、项目类型必须用中文；区域/省份无通用中文译名时可保留英文（如"Sindh省"）；【严禁】直接照抄英文原文（如"Pakistan 200MW Solar Project"）。
    - **Project Name (EN)**: 项目名称的英文版本。优先使用项目的官方英文名（如"Karot Hydropower Project"）；若无官方名，则按格式翻译："Country + Capacity MW + Energy Type + Project/Plant [补充说明]"，示例："Pakistan 200MW Solar PV Project (Punjab Province)"、"Ethiopia 500MW Hydropower Project (Phase II)"。
    - **Category**: 只能选 [新能源制造业，输变电建设，火电，光伏, 风电, 水电，核电，地热及余热, 生物质, 储能, 综合能源]。
        - 如果项目是建设"光伏组件厂"、"锂电池制造基地"、"多晶硅工厂"、"新能源装备产业园"，则视为“新能源制造业”。
            - **区分关键**：看产出单位。如果是"GW/年（产能）"、"万吨/年"，则是工厂。如果是"MW/GW（装机容量）"，才是电站。
        - 如果项目仅涉及"输电线路"、"变电站扩建"、"电网改造"、"升压站建设"等，则选择“输变电”。
        - 如果项目是建设"火电厂"、"燃气轮机厂"、"天然气发电厂"、“煤电”，且不涉及与其他新能源发电形式结合，则视为“火电”。
    - **status**: 枚举值 "valid_project" | "no_chinese_participation" | "irrelevant"
    - **Type of involvement of Chinese Entities**: 只能选 [BOT/BOOT, EPC总承包, 设计/施工服务方, 股权投资方, 设备供应商, 投融资+EPC, 融资方, Unknown]。
    - **Financing Model**: 只能选 [信贷融资, 债券融资, 股权融资, 混合融资, 其他, Unknown]。
    - **Project Stage**: 只能选 [拟开发，收购, 中标, 签约, 开工, 在建, 竣工, 投入运营, Unknown]。
        - Project Stage判断标准补充：“完工”被视为“竣工”；“并网发电”被视为“投入运营”；“收购, 中标, 签约, 开工, 在建”你自行判断，以新闻原文为准。
        - 如果项目只涉及到"投标"、"招标"、"竞标"、"投标意向"、"竞标意向"，且未提及与中国企业相关的具体的中标或签约信息，则视为“拟开发”。
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
    projects_db_path: str = Field(
        default=_DEFAULT_PROJECTS_DB,
        description="有效项目输出数据库路径（SQLite，跨批次增量累积）",
    )
    review_db_path: str = Field(
        default=_DEFAULT_REVIEW_DB,
        description="审核记录数据库路径（无中方参与 + 被剔除文章，SQLite）",
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
        "将结果分三类写入 SQLite 数据库：有效项目（→ projects_db_path）、"
        "无中方参与项目和被剔除无关新闻（→ review_db_path）。"
        "返回各类别数量摘要和数据库路径。"
    )
    args_schema: Type[BaseModel] = ProjectExtractorInput

    def _run(
        self,
        input_file: str,
        projects_db_path: str = _DEFAULT_PROJECTS_DB,
        review_db_path: str = _DEFAULT_REVIEW_DB,
        country_db_path: str = _DEFAULT_COUNTRY_DB,
    ) -> str:
        """从 Excel 文件读取新闻，调用 AI 提取项目信息并写入数据库。"""
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return "错误：未找到 GEMINI_API_KEY 环境变量，请检查 .env 文件。"

        client = genai.Client(api_key=api_key)
        generation_config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.14,
        )
        country_db = _load_country_db(country_db_path)

        try:
            df = pd.read_excel(input_file)
        except FileNotFoundError:
            return f"错误：找不到输入文件 {input_file}"

        ts = datetime.now().strftime("%Y%m%d_%H%M")
        batch_id = Path(input_file).stem
        return self._process_df(df, batch_id, ts, client, generation_config, country_db,
                                projects_db_path, review_db_path)

    def _run_from_db(
        self,
        batch_id: str,
        raw_news_db_path: str,
        projects_db_path: str = _DEFAULT_PROJECTS_DB,
        review_db_path: str = _DEFAULT_REVIEW_DB,
        country_db_path: str = _DEFAULT_COUNTRY_DB,
    ) -> str:
        """从 raw_news.db 按 batch_id 读取关键词匹配的新闻，调用 AI 提取并写入数据库。
        供 run_bulk 两阶段流程直接调用，不依赖中间 Excel 文件。
        """
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return "错误：未找到 GEMINI_API_KEY 环境变量，请检查 .env 文件。"

        client = genai.Client(api_key=api_key)
        generation_config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.14,
        )
        country_db = _load_country_db(country_db_path)

        try:
            conn = sqlite3.connect(raw_news_db_path)
            rows = conn.execute(
                "SELECT url, title, content, publish_date FROM raw_news "
                "WHERE batch_id=? AND passed_keyword_filter=1 ORDER BY id",
                (batch_id,),
            ).fetchall()
            conn.close()
        except Exception as e:
            return f"错误：读取 raw_news.db 失败: {e}"

        if not rows:
            return f"raw_news.db 中 batch_id={batch_id} 无关键词匹配记录，跳过。"

        df = pd.DataFrame(rows, columns=["原文链接", "文章标题", "正文内容", "发布日期"])
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        return self._process_df(df, batch_id, ts, client, generation_config, country_db,
                                projects_db_path, review_db_path)

    # ── 核心处理逻辑（供 _run 和 _run_from_db 共用）────────────────────────

    def _process_df(
        self,
        df: "pd.DataFrame",
        batch_id: str,
        ts: str,
        client: "genai.Client",
        generation_config: "types.GenerateContentConfig",
        country_db: dict,
        projects_db_path: str,
        review_db_path: str,
    ) -> str:
        """处理一个 DataFrame（来源可以是 Excel 或 raw_news.db），执行 AI 提取并写入数据库。"""
        # 加载已处理过的 URL（去重）
        already_processed: set[str] = set()
        try:
            _chk = _init_projects_db(projects_db_path)
            cur = _chk.cursor()
            cur.execute("SELECT DISTINCT source_link FROM extracted_projects WHERE source_link IS NOT NULL AND source_link != ''")
            already_processed.update(r[0] for r in cur.fetchall())
            _chk.close()
        except Exception:
            pass
        try:
            _chk2 = _init_review_db(review_db_path)
            cur2 = _chk2.cursor()
            cur2.execute("SELECT source_link FROM no_chinese_records WHERE source_link IS NOT NULL AND source_link != '' "
                         "UNION SELECT source_link FROM discarded_records WHERE source_link IS NOT NULL AND source_link != ''")
            already_processed.update(r[0] for r in cur2.fetchall())
            _chk2.close()
        except Exception:
            pass
        if already_processed:
            print(f">>> 增量模式：已跳过 {len(already_processed)} 个已处理 URL")

        valid_rows, no_chinese_rows, discarded_rows, all_ai_results = [], [], [], []
        total = len(df)
        print(f"开始处理 {total} 条数据...")

        pre_filter_keywords = [
            "光伏", "太阳能", "风电", "风力", "水电", "地热", "储能",
            "电力", "电站", "发电", "MW", "GW",
        ]

        last_valid_pub_date = ""

        for row_num, (_, row) in enumerate(df.iterrows(), start=1):
            title = str(row.get("文章标题", ""))
            content = str(row.get("正文内容", ""))
            pub_date = str(row.get("发布日期", ""))
            orig_link = str(row.get("原文链接", ""))

            # 增量去重：跳过已处理的 URL
            if orig_link and orig_link not in ("nan", "NA", "None") and orig_link in already_processed:
                print(f"[{row_num}/{total}] 跳过（已处理）: {title[:25]}...")
                continue

            # publish_date 前向填充：无法正确读取时用上一条有效日期
            if pub_date and pub_date not in ("nan", "NA", "None", "") and any(c.isdigit() for c in pub_date):
                last_valid_pub_date = pub_date
            else:
                if last_valid_pub_date:
                    print(f"[{row_num}/{total}] publish_date 无效（原值='{pub_date}'），使用前一条日期: {last_valid_pub_date}")
                    pub_date = last_valid_pub_date

            # Python 端预筛，节省 API 调用
            if not any(k in content for k in pre_filter_keywords):
                print(f"[{row_num}/{total}] Python预筛剔除: {title[:25]}...")
                discarded_rows.append({
                    "文章标题": title,
                    "发布日期": pub_date,
                    "原文链接": orig_link,
                    "正文内容": content,
                    "拒绝原因": "Python关键词预筛不匹配",
                })
                continue

            print("--------------------------------")
            print(f"[{row_num}/{total}] AI处理中: {title[:25]}...")
            prompt = _build_prompt(title, content, pub_date)

            MAX_RETRIES = 3
            result = None
            last_error = ""
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    response = client.models.generate_content(
                        model="gemini-2.5-flash-lite",
                        contents=prompt,
                        config=generation_config,
                    )
                    result = json.loads(response.text)
                    break  # 成功则跳出重试循环
                except Exception as e:
                    last_error = str(e)
                    if attempt < MAX_RETRIES:
                        wait = 2 ** attempt  # 2s, 4s
                        print(f"  -> 处理出错（第{attempt}次）: {e}，{wait}s 后重试...")
                        time.sleep(wait)
                    else:
                        print(f"  -> 处理出错（已重试{MAX_RETRIES}次，放弃）: {e}")

            if result is None:
                discarded_rows.append({
                    "文章标题": title,
                    "正文内容": content,
                    "拒绝原因": f"程序异常（重试{MAX_RETRIES}次失败）: {last_error}",
                })
            else:
                analysis = result.get("analysis", {})
                status = result.get("status")
                reasoning = analysis.get("reasoning", "无详细理由")
                subject = analysis.get("project_subject", "未知主体")

                print(f"  -> 分析: [{subject}] - {reasoning}")
                print(f"  -> 结论: {status}")

                all_ai_results.append({"index": row_num, "title": title, "result": result})

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
                            "Record Type": common.get("Record Type", "具体项目"),
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

            # 处理完毕后标记该 URL（防止同一批次内同一链接出现多次）
            if orig_link and orig_link not in ("nan", "NA", "None"):
                already_processed.add(orig_link)

            time.sleep(1)

        # ── 写入 SQLite 数据库 ────────────────────────────────────────────────
        created_at  = datetime.now().isoformat()
        proj_conn   = _init_projects_db(projects_db_path)
        review_conn = _init_review_db(review_db_path)

        # 写入 projects.db（有中国主体的有效项目）
        for row_data in valid_rows:
            proj_conn.execute(
                """INSERT INTO extracted_projects
                   (record_type, project_name, project_name_en, name_en_quality, country, country_iso,
                    bri_country, ldc, location, category, capacity_mw, capacity_details,
                    project_stage, project_developer, multiple_chinese, implementing_entity,
                    implementing_entity_en, subsidiary_name, subsidiary_name_en,
                    involvement_type, subcontract_amount_usd, planned_start_date,
                    planned_complete_date, total_investment_usd, investment_date,
                    financing_model, financing_model_details,
                    source_link, publish_date, official_website, non_official_website,
                    additional_remarks, batch_id, imported_to_s02a, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
                (
                    row_data.get("Record Type", "具体项目"),
                    row_data.get("Project Name", ""),
                    row_data.get("Project Name (EN)", ""),
                    row_data.get("Name EN Quality", ""),
                    row_data.get("Country", ""),
                    row_data.get("Country ISO", ""),
                    row_data.get("BRI country", ""),
                    row_data.get("LDCs", ""),
                    row_data.get("Location (State)", ""),
                    row_data.get("Category", ""),
                    row_data.get("Capacity (MW)", ""),
                    row_data.get("Capacity Details", ""),
                    row_data.get("Project Stage", ""),
                    row_data.get("Project Developer", ""),
                    row_data.get("Multiple Chinese Enterprises Involved", ""),
                    row_data.get("Implementing Chinese Entity", ""),
                    row_data.get("Implementing Chinese Entity (EN)", ""),
                    row_data.get("Subsidiary Name", ""),
                    row_data.get("Subsidiary Name (EN)", ""),
                    row_data.get("Type of involvement of Chinese Entities", ""),
                    row_data.get("Subcontract Amount (Million USD)", ""),
                    row_data.get("(Planned) Start Date", ""),
                    row_data.get("(Planned) Complete Date", ""),
                    row_data.get("Total Project Investment (Million USD)", ""),
                    row_data.get("Investment (or Financial Close) Date", ""),
                    row_data.get("Financing Model", ""),
                    row_data.get("Financing Model Details", ""),
                    row_data.get("Source Link", ""),
                    row_data.get("Publish Date", ""),
                    row_data.get("Official Website link", ""),
                    row_data.get("Non-official website link", ""),
                    row_data.get("Additional Remarks", ""),
                    batch_id,
                    created_at,
                ),
            )
        proj_conn.commit()
        proj_conn.close()

        # 写入 review.db → no_chinese_records（无中方参与）
        for row in no_chinese_rows:
            try:
                review_conn.execute(
                    """INSERT OR IGNORE INTO no_chinese_records
                       (title, source_link, publish_date, batch_id, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        row.get("文章标题", ""),
                        row.get("原文链接", ""),
                        row.get("发布日期", ""),
                        batch_id,
                        created_at,
                    ),
                )
            except Exception:
                pass
        review_conn.commit()

        # 写入 review.db → discarded_records（被过滤文章）
        for row in discarded_rows:
            try:
                review_conn.execute(
                    """INSERT OR IGNORE INTO discarded_records
                       (title, source_link, publish_date, discard_reason, batch_id, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        row.get("文章标题", ""),
                        row.get("原文链接", ""),
                        row.get("发布日期", ""),
                        row.get("拒绝原因", ""),
                        batch_id,
                        created_at,
                    ),
                )
            except Exception:
                pass
        review_conn.commit()
        review_conn.close()

        # AI 原始结果 JSON（存放在 projects.db 同级目录）
        json_path_str = ""
        if all_ai_results:
            json_dir = Path(projects_db_path).parent
            json_path = json_dir / f"All_AI_Results_{ts}.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(all_ai_results, f, ensure_ascii=False, indent=2)
            json_path_str = f"\n  AI原始结果: {len(all_ai_results)} 条 → {json_path}"

        if not valid_rows and not no_chinese_rows and not discarded_rows:
            return "处理完成，但未生成任何输出（无符合条件的数据）。"

        return (
            f"提取完成。\n"
            f"  有效项目:  {len(valid_rows)} 条 → {os.path.abspath(projects_db_path)}\n"
            f"  无中方参与: {len(no_chinese_rows)} 条 → {os.path.abspath(review_db_path)}\n"
            f"  被剔除新闻: {len(discarded_rows)} 条 → {os.path.abspath(review_db_path)}"
            + json_path_str
        )
