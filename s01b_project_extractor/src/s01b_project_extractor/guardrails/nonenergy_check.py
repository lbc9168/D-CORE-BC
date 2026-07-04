"""Layer 0: 确定性非能源兜底——纯 Python 判定非能源重工业项目与吨/年容量，不依赖 LLM。

背景：2026-07-02 审计发现 77 条矿业/油气/化工等项目被 LLM 误分类为综合能源上页面
（基准案 CAN-HB-0001 钾肥矿 → capacity_mw=3,800,000 即 3.8TW）。排除规则在 prompt
里本已齐全但 gemini-2.5-flash-lite 不遵守，故写库前加本确定性兜底。
审计明细：docs/rules_and_plans/2026-07-02-misclassified-nonenergy-audit.csv。
"""
import re

# exclude_source 标记：与 S2 in_scope 判定（"extraction"）区分，便于日后审计
DETERMINISTIC_GUARD_SOURCE = "deterministic_guard"

# 非能源产业词（命中「项目名称」为强信号；源自审计 71 例归纳）
_NONENERGY_WORDS = [
    # 矿业采选/冶金
    "矿业", "采矿", "选矿", "矿山", "矿区", "铁矿", "铜矿", "金矿", "镍矿", "锂矿",
    "铝土矿", "磷矿", "石墨矿", "煤矿", "多金属矿", "采选", "矿服", "尾矿",
    "钾肥", "钾盐", "锂盐", "氧化铝", "电解铝", "镍铁", "冶炼", "冶金", "钻孔", "爆破",
    # 油气炼化
    "油气", "气田", "油田", "炼油", "炼化", "石化", "LNG", "液化天然气",
    "天然气", "输气管道", "凝析油",
    "焦炉气", "煤气柜", "FPU",
    # 化工化肥
    "化工", "化肥", "氯碱", "甲醇", "MTBE", "硫磺", "溴化", "煤制肥",
    # 建材/钢铁
    "水泥", "熟料", "钢铁", "钢厂", "高炉", "炼钢",
    # 港口物流（「电力贸易」由发电语义词保护，故不含泛词「贸易」）
    "港口", "码头", "物流", "清关", "货运站", "边关站",
    # 水务
    "海水淡化", "供水", "污水",
    # 民用基建（2026-07-04 主库回填诊断补充：基准案 SUR-XX-0001 灌溉排水修复；
    # 「光储灌溉」「机场光伏」等合法共存例由发电语义词保护，不含「水利」以免误杀水利枢纽水电）
    "灌溉", "排水", "水渠", "道路", "公路", "机场", "铁路", "桥梁",
    "体育场", "体育馆", "医院", "学校", "教学楼",
]

# 非能源英文词（部分项目名为纯英文，如 "Muga Potash Greenfield Project"）；小写匹配
_NONENERGY_WORDS_EN = [
    "potash", "gold mine", "copper", "iron ore", "refinery", "petrochemical",
    "cement", "alumina", "smelter", "gas field", "chlor-alkali", "lng",
]

# 硬排除词：含「电站」字样但属用电设施，先于发电语义保护判定
_NONENERGY_OVERRIDE_WORDS = ["充换电站", "充电站"]

# 发电语义词（负信号：项目名或容量明细命中则不排除——保护矿区光储微网等合法项目）
_POWER_WORDS = [
    "发电", "电站", "电厂", "光伏", "风电", "水电", "储能", "光储", "微网", "微电网",
    "太阳能", "风能", "风光", "光热", "地热", "生物质", "并网", "输变电", "变电站",
    "输电", "联网", "电力贸易", "装机", "mw", "gw", "兆瓦",
    "联合循环", "燃机",
    # 输变电语义（2026-07-04：TFM矿区供电/米桑油田132千伏电网等真输变电工程曾被
    # 「矿区/油田」词误杀——电网工程在录入范围（发电/新能源制造/输变电）内）
    "电网", "千伏", "供电",
    # 新能源制造/光伏建筑语义：多晶硅=光伏产业链硅料（AGO 冶金硅+多晶硅项目曾被「冶金」误杀）；
    # bipv 保护名称无中文发电词的光伏建筑一体化项目
    "多晶硅", "bipv",
]

# 明确可再生能源种类词（绿氢规则用；用户判定规则 2026-07-02）
_RE_TYPE_WORDS = ["光伏", "风电", "水电", "太阳能", "风能", "风光", "光热", "地热", "生物质", "可再生能源"]

# 绿氢/电解类词：须同时有「明确 RE 种类 + 明确装机容量」才纳入
_HYDROGEN_WORDS = ["绿氢", "绿色氢", "绿氨", "绿色氨", "制氢", "电解槽", "电解水"]

# 规则 3 豁免类别：这些类别以处理吨量计装机为正常口径（垃圾焚烧发电/生物质/余热），不按吨/年判非发电
_TONNAGE_EXEMPT_CATEGORIES = {"新能源制造业", "生物质", "地热及余热"}

# 确凿发电语义（规则 3 放行用）：名称无「发电」但摘要证明确为发电项目时不误杀（如垃圾焚烧发电）
_GENERATION_EVIDENCE = ["发电", "绿色电力"]

# 吨/年产能模式 与 MW 装机语义
_TONNAGE_RE = re.compile(r"[\d,.]+\s*万?\s*吨|吨/年|万吨|t/a|tpa|mtpa", re.IGNORECASE)
_MW_TOKEN_RE = re.compile(r"MW|GW|兆瓦|千瓦|kW", re.IGNORECASE)


def _has_positive_mw(capacity_mw, name: str, details: str) -> bool:
    """是否有装机容量证据：capacity_mw 为正数，或名称/明细含 MW/GW 语义。"""
    try:
        if float(capacity_mw) > 0:
            return True
    except (TypeError, ValueError):
        pass
    return bool(_MW_TOKEN_RE.search(name + " " + details)) or "吉瓦" in (name + details)


def capacity_is_tonnage(capacity_raw, capacity_details) -> bool:
    """容量字段本身是否以吨计（且无任何 MW 语义）——是则该数值不是装机，应置空。"""
    text = f"{capacity_raw or ''} {capacity_details or ''}"
    if not text.strip():
        return False
    return bool(_TONNAGE_RE.search(text)) and not _MW_TOKEN_RE.search(text)


def check_nonenergy(name, summary, category, capacity_mw, capacity_details):
    """确定性非能源判定：返回排除理由字符串（命中）或 None（放行）。

    规则按序：⓪ 硬排除词（充换电站）；① 绿氢/电解类须同时有明确 RE 种类+装机容量；
    ② 名称命中非能源词且名称+容量明细无发电语义；③ 发电类别但容量只有吨/年（制造业豁免）；
    ④ Unknown 类别兜底——空项目名，或全文无发电语义且无装机（词表列不全的长尾民用项目）。
    """
    name = str(name or "")
    summary = str(summary or "")
    details = str(capacity_details or "")
    category = str(category or "")
    text = name + " " + summary
    protect_text = (name + " " + details).lower()

    # 规则 0：硬排除词（「充换电站」含「电站」字样，须先于发电语义保护判定）
    for w in _NONENERGY_OVERRIDE_WORDS:
        if w in name:
            return f"非发电设施（{w}）"

    # 规则 1：绿氢/电解类——须同时有明确可再生能源种类 + 装机容量（用户规则 2026-07-02）
    if any(w in text for w in _HYDROGEN_WORDS):
        if any(w in text for w in _RE_TYPE_WORDS) and _has_positive_mw(capacity_mw, name, details):
            return None  # 合法（如风光制氢）；类别归属由 S2 prompt 规则处理
        return "绿氢/电解类项目缺明确可再生能源种类或装机容量（用户规则2026-07-02）"

    # 规则 2：名称命中非能源产业词，且名称+容量明细无发电语义 → 排除
    hit = next((w for w in _NONENERGY_WORDS if w in name), None) or next(
        (w for w in _NONENERGY_WORDS_EN if w in name.lower()), None
    )
    if hit and not any(w in protect_text for w in _POWER_WORDS):
        return f"非能源产业项目（名称命中「{hit}」且无发电语义）"

    # 规则 3：非豁免类别但容量字段只有吨/年（产能计量而非装机，疑似非发电）
    # capacity_mw 数值本身无单位信息（且可能是毒值，如天然气处理厂 mw=2250），故不据其判定。
    # 只查 name+summary 的发电语义，不查 capacity_details——后者可能含辅助供电「风电+太阳能」等
    # 误导词（如壳牌油气平台）。名称无「发电」但摘要证明确为发电项目（垃圾焚烧发电）不得误杀。
    if category not in _TONNAGE_EXEMPT_CATEGORIES and capacity_is_tonnage(None, details):
        gen_text = name + " " + summary
        if not any(w in name.lower() for w in _POWER_WORDS) and not any(
            w in gen_text for w in _GENERATION_EVIDENCE
        ):
            return "容量为吨/年产能而非装机，疑似非发电项目"

    # 规则 4：Unknown 类别兜底（2026-07-04）——S2 不敢给发电类别的行是词表漏网聚集地
    # （别墅/食品厂/度假村/办公楼等长尾民用项目枚举不完）。与规则 2 相反，这里查全文
    # （名称+摘要+容量明细）且认装机数值，只有毫无发电证据才排除，偏保守。
    if category == "Unknown":
        if not name.strip():
            return "Unknown类别且项目名为空（提取残留，非可用记录）"
        full_text = (name + " " + summary + " " + details).lower()
        if not any(w in full_text for w in _POWER_WORDS) and not _has_positive_mw(
            capacity_mw, name, details
        ):
            return "Unknown类别且全文无发电语义（民用/未分类漏网兜底）"

    return None
