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


@pytest.mark.parametrize("name,summary,cat,mw,details", CONFIRMED_NONENERGY)
def test_confirmed_nonenergy_excluded(name, summary, cat, mw, details):
    assert check_nonenergy(name, summary, cat, mw, details) is not None, f"应拦住: {name}"


@pytest.mark.parametrize("name,summary,cat,mw,details", LEGIT_ENERGY + NORMAL_ENERGY)
def test_legit_energy_not_excluded(name, summary, cat, mw, details):
    assert check_nonenergy(name, summary, cat, mw, details) is None, f"误杀: {name}"


@pytest.mark.parametrize("name,summary,cat,mw,details", HYDROGEN_EXCLUDE)
def test_hydrogen_without_re_type_excluded(name, summary, cat, mw, details):
    assert check_nonenergy(name, summary, cat, mw, details) is not None, f"绿氢规则应拦住: {name}"


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
