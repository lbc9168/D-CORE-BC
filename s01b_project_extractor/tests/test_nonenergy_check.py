"""确定性非能源兜底：check_nonenergy / capacity_is_tonnage 纯函数判定。

夹具取自 2026-07-02 审计 CSV（71 confirmed / 7 legit / 6 borderline）代表样本 + 常规能源项目负例。
"""
import pytest
from s01b_project_extractor.guardrails.nonenergy_check import (
    DETERMINISTIC_GUARD_SOURCE,
    capacity_is_tonnage,
    check_nonenergy,
)

# ── 必须拦住：审计 confirmed_nonenergy 代表样本（各行业至少一条）──
CONFIRMED_NONENERGY = [
    # (name, summary, category, capacity_mw, capacity_details)
    ("加拿大盐湖钾肥矿业并购项目", "并购世界级钾盐矿，产能380万吨/年", "综合能源", "3800000", "380万吨/年"),  # 基准案 CAN-HB-0001
    ("阿联酋乌姆盖万150MIGD海水淡化项目", "", "综合能源", "", ""),
    ("阿根廷青山实业控股Jujuy氯碱化工厂项目", "", "综合能源", "", ""),
    ("玻利维亚阿玛雅金矿采矿项目", "", "综合能源", "", ""),
    ("巴林7个天然气增压站工程项目", "", "综合能源", "", ""),
    ("伊拉克SCP水泥生产线项目", "", "综合能源", "", ""),
    ("塞尔维亚普拉霍沃港口项目", "", "综合能源", "", ""),
    ("阿尔及利亚Gara Djebilet铁矿开发及一体化钢铁厂项目", "", "综合能源", "", ""),
    ("埃及苏伊士炼油及石化厂项目", "", "综合能源", "", ""),
    ("马里Bougouni锂矿项目", "", "综合能源", "", ""),
    ("印尼莫罗瓦利青山工业园区焦炉气产LNG联产甲醇项目", "", "综合能源", "", ""),
    ("南非姆普马兰加省Hiryo煤制肥综合体", "", "综合能源", "", ""),
    ("中哈（连云港）物流合作基地及相关通道项目", "", "综合能源", "", ""),
    ("Muga Potash Greenfield Project (Spain)", "", "综合能源", "", ""),
    ("印尼纬达贝工业园区重卡充换电站项目", "", "综合能源", "", ""),  # 「电站」字样不得保护充换电站
    ("莫桑比克鲁伍马盆地1号区块天然气项目", "", "综合能源", "", ""),
    ("吉布提阿萨尔盐湖溴化工项目", "", "综合能源", "", ""),
]

# ── 不许误杀：审计 legit_energy 全部 7 条 ──
LEGIT_ENERGY = [
    ("刚果（金）三大矿区3个光储柴微网项目", "", "综合能源", "30", "光储柴微网"),
    ("塞尔维亚2GW风光发电及绿氢项目", "", "综合能源", "2000", "风电+光伏 2GW"),
    ("塞尔维亚1.5吉瓦风光制氢项目", "", "综合能源", "1500", "风光 1.5GW"),
    ("中老115千伏联网电力贸易项目", "", "输变电建设", "", ""),
    ("赞比亚乔瓦矿山光储微网项目", "", "综合能源", "30", "PV 20MW + BESS 10MWh"),
    ("塞尔维亚博尔地区2GW可再生能源及绿氢项目", "", "综合能源", "2000", "2GW"),
    ("秘鲁马尔科纳区2MW低压微电网项目", "", "综合能源", "2", "2MW"),
]

# ── 不许误杀：常规能源项目负例 ──
NORMAL_ENERGY = [
    ("巴基斯坦光伏电站项目", "中国电建签署100MW光伏EPC合同", "光伏", "100", ""),
    ("印度尼西亚卡扬河9000MW水电项目", "", "水电", "9000", ""),
    ("沙特阿拉伯Rumah 2联合循环1800MW发电站项目", "", "火电", "1800", ""),
    ("孟加拉帕亚拉1320MW燃煤电站项目", "", "火电", "1320", ""),
    ("埃及2.5吉瓦太阳能电池供应项目", "整笔供货", "新能源制造业", "", "2.5GW/年产能"),
    ("印尼10万吨/年多晶硅厂项目", "", "新能源制造业", "", "10万吨/年"),  # 制造业吨/年豁免
]

# ── 绿氢用户规则（2026-07-02）：须同时有明确 RE 种类 + 装机 ──
HYDROGEN_EXCLUDE = [
    ("西班牙韦尔瓦160MW绿氢项目", "", "综合能源", "160", ""),        # 有容量无种类 → exclude
    ("肯尼亚绿色氨项目", "", "综合能源", "", ""),                    # 都无 → exclude
    ("阿联酋绿色氢炼钢项目", "", "综合能源", "", ""),                # 绿色氢+炼钢 → exclude
    ("西班牙远景电解槽工厂项目", "只提绿氢电解，无能源种类", "综合能源", "", ""),
]

# ── 不许误杀：垃圾焚烧发电 / 生物质等真发电项目——名称无「发电」但容量以处理吨量计 ──
# （2026-07-04 回溯清扫发现：规则3只看名称，把主库在册的 WtE 电厂误杀。见 docs/changes）
WASTE_TO_ENERGY_LEGIT = [
    # (name, summary, category, mw, details)
    # 综合能源：名称无「发电」，靠摘要「绿色电力 / 发电」证明是真发电
    ("澳门垃圾焚化中心第三期扩建工程", "该项目设计日处理生活垃圾1300吨，预计每年可提供绿色电力超2亿度，已顺利竣工。",
     "综合能源", "200", "日处理生活垃圾1300吨，配置两台650吨/日逆推机械炉排炉，年发电量超2亿度。"),
    ("吉尔吉斯共和国奥什市垃圾科技处置项目主体土建工程", "该项目为垃圾发电工程，装机规模为850吨/日处理能力。",
     "综合能源", "0", "850吨/日处理规模"),
    # 生物质 / 地热及余热：吨/日为其正常口径，按类别豁免
    ("澳门有机资源回收中心项目", "旨在将有机废弃物转化为再生能源和有机肥料，以EPC+O模式建设运营。",
     "生物质", None, "日处理150吨厨余垃圾、230吨厕所废水和190吨油脂废水"),
    ("塞尔维亚米特洛维卡焚烧锅炉岛EPC项目", "项目将处理生活垃圾并进行热电联产。",
     "地热及余热", "Unknown", "垃圾处理量: 650吨/天"),
]

# ── 仍须拦住：容量为吨/年的真非发电项目（防止修规则3时放行过度）──
TONNAGE_NONENERGY_STILL_EXCLUDED = [
    # 天然气处理厂：mw=2250 为毒值、摘要全是天然气处理，无任何发电语义
    ("伊拉克哈法亚GPP项目", "中国石油工程建设公司在伊拉克承建了哈法亚天然气处理厂（GPP）项目。",
     "综合能源", "2250", "天然气设计处理能力为30亿立方米/年，凝液处理为95万吨/年，硫磺1.5万吨/年。"),
    # 油气平台：capacity_details 含辅助供电「风电+太阳能」误导词，但摘要证明是油气平台——不得放行
    ("尼日尔三角洲南部海域壳牌HI项目", "海油工程作为EPC总承包商承建HI项目，包括一座上部组块和一座导管架，已完工交付。",
     "综合能源", "", "采用“风电+太阳能”混合供电模式，年运维费用降低60%，年减碳约40吨"),
]


@pytest.mark.parametrize("name,summary,cat,mw,details", CONFIRMED_NONENERGY)
def test_confirmed_nonenergy_excluded(name, summary, cat, mw, details):
    assert check_nonenergy(name, summary, cat, mw, details) is not None, f"应拦住: {name}"


@pytest.mark.parametrize("name,summary,cat,mw,details", LEGIT_ENERGY + NORMAL_ENERGY)
def test_legit_energy_not_excluded(name, summary, cat, mw, details):
    assert check_nonenergy(name, summary, cat, mw, details) is None, f"误杀: {name}"


@pytest.mark.parametrize("name,summary,cat,mw,details", HYDROGEN_EXCLUDE)
def test_hydrogen_without_re_type_excluded(name, summary, cat, mw, details):
    assert check_nonenergy(name, summary, cat, mw, details) is not None, f"绿氢规则应拦住: {name}"


@pytest.mark.parametrize("name,summary,cat,mw,details", WASTE_TO_ENERGY_LEGIT)
def test_waste_to_energy_not_excluded(name, summary, cat, mw, details):
    assert check_nonenergy(name, summary, cat, mw, details) is None, f"误杀真发电: {name}"


@pytest.mark.parametrize("name,summary,cat,mw,details", TONNAGE_NONENERGY_STILL_EXCLUDED)
def test_tonnage_nonenergy_still_excluded(name, summary, cat, mw, details):
    assert check_nonenergy(name, summary, cat, mw, details) is not None, f"应仍拦住: {name}"


# ── 必须拦住：民用基建（2026-07-04 主库回填诊断——灌溉/排水/道路/机场等漏网，基准案 SUR-XX-0001）──
CIVIL_INFRA_EXCLUDE = [
    ("苏里南尼克里区灌溉和排水系统设施修复项目", "中国电建完成灌溉和排水系统设施修复，改善灌溉条件支持农业生产。",
     "Unknown", None, "清理沟渠290公里，道路修复75公里，建设和修复水利设施59处"),  # 主库 SUR-XX-0001
    ("坦桑尼亚卡索利灌溉项目（二期）", "", "Unknown", None, ""),
    ("肯尼亚恩佐亚河下游灌溉工程建设项目", "", "综合能源", None, ""),
    ("莫桑比克贝拉大型水利水渠二期项目", "主要包括大型混凝土水渠、蓄水池和入海排水口的建设等基础设施工程。",
     "水电", None, ""),  # 主库 MOZ-HY-0001，防洪排涝土建，类别亦误标
    ("喀麦隆克里比-罗拉贝高速公路", "", "综合能源", None, ""),
    ("迪拜阿尔瓦斯尔道路改造项目", "", "输变电建设", None, ""),
    ("孟加拉国达卡机场扩建项目", "", "输变电建设", None, ""),
    ("巴西帕拉州机场项目四标段", "负责四座机场航站楼和跑道的现代化改造和施工。", "输变电建设", None, ""),
    ("阿尔及利亚Tindouf省和Béchar省铁路线项目", "", "输变电建设", None, ""),
    ("中老铁路", "", "综合能源", None, ""),
    ("安哥拉EN120国道扎伊尔省姆班扎刚果市至威热省松戈市179公里土路及桥梁建设项目", "", "输变电建设", None, ""),
    ("援巴哈马国家体育场维修改造项目", "", "Unknown", None, ""),
    ("俄罗斯彼尔姆多功能体育馆项目", "", "综合能源", None, ""),
    ("北马其顿共和国医院项目", "", "输变电建设", None, ""),
    ("柬埔寨乡村学校建设项目", "", "Unknown", None, ""),
    ("肯尼亚水利学院教学楼项目", "新建一栋12层教学综合大楼。", "综合能源", None, ""),
    ("俄罗斯波罗的海化工综合体给排水项目", "", "综合能源", None, ""),  # 化工+排水双命中，保护词修正后仍须拦住
]

# ── 不许误杀：名称含民用词但有发电/在册语义的合法项目 + 保护词缺口修正例 ──
CIVIL_PROTECT_LEGIT = [
    # 民用词 + 发电语义共存（主库在册）
    ("塞内加尔1.01MW光储灌溉暨现代农业发展项目", "", "综合能源", "1.01", ""),
    ("津巴布韦1.144MW光伏发电提水灌溉项目", "", "光伏", "1.144", ""),
    ("加纳帕鲁谷水电枢纽与灌溉项目", "", "水电", None, ""),
    ("新加坡樟宜机场商务园20MW光伏项目", "", "光伏", "20", ""),
    ("中老铁路野象谷、磨憨光伏发电项目", "", "光伏", None, ""),
    ("几内亚36座医院配套太阳能光伏发电项目", "", "光伏", None, ""),
    ("日本茨城筑波市原医院综合智慧零碳电厂项目", "", "综合能源", None, ""),
    ("越南AEON MALL、河内联合国际学校、Tan De服装企业屋顶光伏项目", "", "光伏", None, ""),
    # BIPV：名称无中文发电词、details 只有 kW——靠 bipv 保护（主库 DEU-PV-0006）
    ("德国托尔高Johann-Walter文理中学体育馆BIPV项目", "为文理中学体育馆提供CIGS发电玻璃。", "光伏", "0.009", "PV: 9kW"),
    # 输变电误杀修正（2026-07-04 发现已 apply 的 projects.db 清扫错杀这批在录入范围内的电网工程）
    ("刚果（金）TFM矿区供电项目", "刚果（金）近十几年输变电领域最大的EPC总承包项目。", "输变电建设", None, ""),
    ("伊拉克米桑油田电网项目南线工程", "建设132千伏供电和传输系统，包括3座变电站和27公里输电线路。", "输变电建设", None, ""),
    ("伊拉克米桑油田132千伏电网北线总承包项目", "包含60公里线路及4个变电站。", "输变电建设", None, ""),
    ("中缅油气管道贵港站110千伏线路工程", "110千伏线路工程，线路全长25.6公里。", "输变电建设", None, ""),
    # 多晶硅误杀修正：冶金硅+多晶硅=光伏产业链硅料（主库 AGO-MF-0002）
    ("安哥拉18万吨冶金硅和15万吨多晶硅项目", "规划产能最大的海外硅料项目，中国光伏企业加速全球布局。",
     "新能源制造业", None, "冶金硅: 180,000 tons/year, 多晶硅: 150,000 tons/year"),
]


@pytest.mark.parametrize("name,summary,cat,mw,details", CIVIL_INFRA_EXCLUDE)
def test_civil_infra_excluded(name, summary, cat, mw, details):
    assert check_nonenergy(name, summary, cat, mw, details) is not None, f"应拦住民用基建: {name}"


@pytest.mark.parametrize("name,summary,cat,mw,details", CIVIL_PROTECT_LEGIT)
def test_civil_protect_legit_not_excluded(name, summary, cat, mw, details):
    assert check_nonenergy(name, summary, cat, mw, details) is None, f"误杀: {name}"


# ── 必须拦住：Unknown 类别兜底（规则 4）——词表列不全的长尾民用项目（2026-07-04 主库存量实例）──
UNKNOWN_NO_POWER_EXCLUDE = [
    ("阿联酋沙迦海扬阿里姆2期306套别墅项目", "中核二二在阿联酋沙迦承接了306套别墅项目，是其在中东区域的首个EPC总承包项目。", "Unknown", None, ""),
    ("印尼达利卡拉旺食品厂项目", "中建三局在印尼承建了达利食品厂项目，包含4个厂房和1个综合楼。", "Unknown", None, ""),
    ("柬埔寨半岛综合度假村项目", "柬埔寨迄今为止最大的商业综合体。", "Unknown", None, ""),
    ("纳米比亚金融机构监管局办公楼项目", "合同额2.37亿纳币，总工期24个月。", "Unknown", None, ""),
    ("尼日利亚阿比亚州综合农业产业园项目", "包括土地整理、加工厂建设及基础设施建设。", "Unknown", None, ""),
    ("阿曼拉斯玛卡兹原油储罐项目", "包括海上单点系泊、陆上储运及配套辅助设施。", "Unknown", None, ""),
    ("巴基斯坦机床采购项目", "", "Unknown", None, ""),
    ("肯尼亚蒙巴萨路三角洲地区开发建设经济适用房项目", "", "Unknown", None, ""),
    # 空项目名的提取残留（主库 SRB-XX-0002）：即使摘要含发电词也不是可用记录
    ("", "本文未提及中国企业参与塞尔维亚的此次风电或太阳能拍卖项目，因此不提取具体项目信息。", "Unknown", None, ""),
]

# ── 不许误杀：Unknown 类别但有发电语义 / 装机容量证据 ──
UNKNOWN_LEGIT = [
    ("某国某待分类项目", "该项目为光伏电站的前期筹备工作。", "Unknown", None, ""),  # 摘要有发电语义
    ("某国某待分类项目", "", "Unknown", "50", ""),                                # 有装机数值
    ("某国不明类型能源项目", "", "Unknown", None, "PV: 20MW"),                     # 明细有 MW 语义
]


@pytest.mark.parametrize("name,summary,cat,mw,details", UNKNOWN_NO_POWER_EXCLUDE)
def test_unknown_no_power_excluded(name, summary, cat, mw, details):
    assert check_nonenergy(name, summary, cat, mw, details) is not None, f"Unknown兜底应拦住: {name or '(空名)'}"


@pytest.mark.parametrize("name,summary,cat,mw,details", UNKNOWN_LEGIT)
def test_unknown_with_power_evidence_not_excluded(name, summary, cat, mw, details):
    assert check_nonenergy(name, summary, cat, mw, details) is None, f"误杀: {name}"


def test_empty_inputs_pass():
    assert check_nonenergy("", "", "", "", "") is None
    assert check_nonenergy(None, None, None, None, None) is None


def test_capacity_is_tonnage():
    assert capacity_is_tonnage("3800000", "380万吨/年") is True
    assert capacity_is_tonnage("", "60万吨LNG") is True
    assert capacity_is_tonnage("100", "PV: 100MW, BESS: 50MWh") is False
    assert capacity_is_tonnage("30", "日处理1万吨垃圾, 装机30MW") is False  # 有 MW 语义不置空
    assert capacity_is_tonnage("", "") is False
    assert capacity_is_tonnage(None, None) is False


def test_guard_source_constant():
    assert DETERMINISTIC_GUARD_SOURCE == "deterministic_guard"
