"""Fixed offline regressions reconstructed from raw_id 27311, not API transcripts."""
from copy import deepcopy

import pytest

from s01b_project_extractor.stages.s2_basic import run_s2


class Stub:
    def __init__(self, ci):
        self.ci = ci

    def complete(self, *args, **kwargs):
        return {"projects": [{"common_info": deepcopy(self.ci)}]}


def extract(text, **overrides):
    ci = {
        "Project Name": "阿根廷Río Cullen风储项目", "Country": "阿根廷",
        "Category": "风电", "Generation Sources": ["风电"],
        "Capacity (MW)": "4.5MW", "Storage Status": "confirmed",
        "Storage Power": "4.5MW", "Storage Energy": "",
        "Energy Composition Evidence": "项目配备电池储能系统",
    }
    ci.update(overrides)
    return run_s2("", text, "2026-05-07", llm=Stub(ci))[0]["common_info"]


SOURCE = (
    "位于Río Grande以北130公里的Río Cullen项目，总投资额超过6000万美元。"
    "在当前全球油气资本开支趋于谨慎的背景下，TotalEnergies依然选择在此时重金投入，足见其对该项目作为“技术试验田”的战略定位。\n"
    "项目现场安装了两台金风科技单机容量4.5MW机组，塔筒高度达86米，叶片扫风直径136米。"
)


def test_real_failure_unit_product_and_undisclosed_storage():
    ci = extract(SOURCE)
    assert ci["capacity_mw_parsed"] == 9
    assert "2 × 4.5 MW = 9 MW" in ci["capacity_details_note"]
    assert ci["capacity_mw_raw_phrase"] in SOURCE
    assert ci["storage_power_mw_parsed"] is None
    assert ci["storage_energy_mwh_parsed"] is None
    assert ci["Storage Power"] == ""


@pytest.mark.parametrize("text", [
    "Delta项目安装了两台金风科技单机容量4.5MW机组。",
    "Río Cullen项目仍在筹备。另一座Delta项目安装了两台金风科技单机容量4.5MW机组。",
    "Río Cullen项目拟建设。Delta项目已投产。项目现场安装了两台金风科技单机容量4.5MW机组。",
    "Río Cullen项目中，其中两台金风科技单机容量4.5MW机组。",
    "Río Cullen项目首批安装了两台金风科技单机容量4.5MW机组。",
    "Río Cullen项目未安装两台金风科技单机容量4.5MW机组。",
    "Río Cullen项目安装了金风科技单机容量4.5MW机组。",
    "Río Cullen项目正在建设；项目现场安装了两台金风科技单机容量4.5MW机组。",
    "Río Cullen项目安装了两台燃气单机容量4.5MW机组。",
])
def test_no_cross_project_partial_negative_or_missing_count(text):
    assert extract(text)["capacity_mw_parsed"] is None


def test_explicit_total_beats_partial_equipment():
    text = "Río Cullen项目总装机容量20MW，其中两台金风科技单机容量4.5MW机组。"
    assert extract(text)["capacity_mw_parsed"] == 20


def test_chinese_count_and_kw_conversion():
    text = "纳沃伊项目安装了十二台单机容量4500kW风电机组。"
    ci = extract(text, **{"Project Name": "乌兹别克斯坦纳沃伊风电项目", "Country": "乌兹别克斯坦", "Capacity (MW)": "4500kW"})
    assert ci["capacity_mw_parsed"] == 54


def test_project_bound_storage_mw_mwh_kept_separate():
    text = "Río Cullen项目总装机容量9MW，并配置储能系统4MW/8MWh。"
    ci = extract(text, **{"Capacity (MW)": "9MW", "Storage Power": "4MW", "Storage Energy": "8MWh"})
    assert ci["capacity_mw_parsed"] == 9
    assert ci["storage_power_mw_parsed"] == 4
    assert ci["storage_energy_mwh_parsed"] == 8


def test_cross_project_storage_rejected():
    text = "Río Cullen项目总装机容量9MW。Delta项目配置储能4MW/8MWh。"
    ci = extract(text, **{"Capacity (MW)": "9MW", "Storage Power": "4MW", "Storage Energy": "8MWh"})
    assert ci["storage_power_mw_parsed"] is None
    assert ci["storage_energy_mwh_parsed"] is None


def test_invalid_evidence_visible_not_replaced_by_arithmetic():
    ci = extract(SOURCE, **{"Capacity Evidence": {"quote": "invented", "project_anchor": "Río Cullen"}})
    assert ci["capacity_mw_parsed"] is None
    assert ci["capacity_evidence_errors"]


def test_actual_quote_without_claimed_number_cannot_validate_capacity():
    ci = extract("Río Cullen项目已经投产。", **{
        "Capacity Evidence": {"quote": "Río Cullen项目已经投产", "project_anchor": "Río Cullen"},
    })
    assert ci["capacity_mw_parsed"] is None
    assert "capacity value absent" in ";".join(ci["capacity_evidence_errors"])


def test_storage_energy_never_becomes_generation_power():
    text = "Río Cullen储能项目配备储能系统4MW/8MWh。"
    ci = extract(text, **{"Category": "储能", "Generation Sources": [], "Capacity (MW)": "4MW", "Storage Power": "4MW", "Storage Energy": "8MWh"})
    assert ci["capacity_mw_parsed"] is None
    assert ci["storage_power_mw_parsed"] == 4

@pytest.mark.parametrize("power,energy", [("175兆瓦", "350兆瓦时"), ("175MW", "350MWh")])
def test_real_sergeli_compound_project_name_and_equivalent_units(power, energy):
    text = "赛尔吉利175兆瓦/350兆瓦时储能电站项目地处乌兹别克斯坦首都塔什干市郊，总装机容量175兆瓦，是中国电建目前在中亚在建单体规模最大的电化学储能项目。"
    ci = extract(text, **{
        "Project Name": "乌兹别克斯坦赛尔吉利175兆瓦/350兆瓦时储能电站项目",
        "Country": "乌兹别克斯坦", "Category": "储能", "Generation Sources": [],
        "Capacity (MW)": "", "Storage Power": power, "Storage Energy": energy,
        "Energy Composition Evidence": "赛尔吉利175兆瓦/350兆瓦时储能电站项目",
    })
    assert ci["capacity_mw_parsed"] is None
    assert ci["storage_power_mw_parsed"] == 175
    assert ci["storage_energy_mwh_parsed"] == 350
    assert ci["storage_power_mw_raw_phrase"] == "175兆瓦"
    assert ci["storage_energy_mwh_raw_phrase"] == "350兆瓦时"


def test_equivalent_units_do_not_borrow_other_project_capacity():
    text = "赛尔吉利储能电站项目已投产。纳沃伊175兆瓦/350兆瓦时储能电站项目已投产。"
    ci = extract(text, **{
        "Project Name": "乌兹别克斯坦赛尔吉利储能电站项目", "Country": "乌兹别克斯坦",
        "Category": "储能", "Generation Sources": [], "Capacity (MW)": "",
        "Storage Power": "175MW", "Storage Energy": "350MWh",
    })
    assert ci["storage_power_mw_parsed"] is None
    assert ci["storage_energy_mwh_parsed"] is None


def test_live_cayirlar_explicit_total_after_preserving_source_project_name():
    # Live 27264 failed because S2 omitted Çayırlar. This reconstructed case fixes
    # that input explicitly; it does not rewrite the saved failed model response.
    text = "本次合作的Çayırlar风电项目总装机容量为43.5兆瓦（MW）。项目最大的亮点在于采用了先进的集成化“风电+储能”解决方案。"
    ci = extract(text, **{
        "Project Name": "土耳其伊兹密尔Çayırlar43.5兆瓦风电项目", "Country": "土耳其",
        "Location(State)": "伊兹密尔", "Capacity (MW)": "43.5兆瓦",
        "Capacity Evidence": {"quote": "本次合作的Çayırlar风电项目总装机容量为43.5兆瓦（MW）。", "project_anchor": "Çayırlar风电项目"},
        "Storage Power": "", "Storage Energy": "",
    })
    assert ci["capacity_mw_parsed"] == 43.5
    assert ci["storage_power_mw_parsed"] is None
    assert ci["storage_energy_mwh_parsed"] is None
    assert ci["capacity_evidence_errors"] == []


def test_live_cayirlar_missing_project_name_still_rejected():
    text = "本次合作的Çayırlar风电项目总装机容量为43.5兆瓦（MW）。"
    ci = extract(text, **{
        "Project Name": "土耳其伊兹密尔43.5兆瓦风电项目", "Country": "土耳其",
        "Location(State)": "伊兹密尔", "Capacity (MW)": "43.5兆瓦",
        "Capacity Evidence": {"quote": text, "project_anchor": "Çayırlar风电项目"},
    })
    assert ci["capacity_mw_parsed"] is None


def test_live_malaysia_single_overall_plan_preserves_mwp_and_phase_details():
    # Live 27500 incorrectly split the 40MWp overall plan. This is an explicitly
    # corrected source-faithful single-project input, not a successful live result.
    quote = "双方将共同落地马来西亚大型光储一体化标杆项目，采用亿纬锂能 Mr.Big Family 系列储能解决方案。\n该项目整体规划装机规模 40MWp，分两期推进：一期 25MWp 已完成签约，预计 2026 年 10 月 15 日并网投运；二期 15MWp 将有序接续。"
    details = "整体40MWp；一期25MWp已签约，预计2026年10月15日并网；二期15MWp接续。"
    ci = extract(quote, **{
        "Project Name": "马来西亚大型光储一体化标杆项目40MWp", "Country": "马来西亚",
        "Category": "光伏", "Generation Sources": ["光伏"], "Capacity (MW)": "40MWp",
        "Capacity Evidence": {"quote": quote, "project_anchor": "马来西亚大型光储一体化标杆项目"},
        "Capacity Details": details, "Storage Power": "", "Storage Energy": "",
    })
    assert ci["capacity_mw_parsed"] == 40
    assert ci["capacity_mw_raw_phrase"].endswith("40MWp")
    assert ci["capacity_mw_raw_phrase"] in quote
    assert ci["Capacity Details"] == details
    assert ci["storage_power_mw_parsed"] is None
    assert ci["storage_energy_mwh_parsed"] is None


@pytest.mark.parametrize("short_quote", [True, False])
def test_live_baldon_phase_capacity_and_storage_energy_separated(short_quote):
    first = "近日，由金风科技与合作伙伴联合开发的澳大利亚Baldon风储融合项目，成功获得澳大利亚能源市场运营机构AEMO和输电运营商Transgrid对发电机性能标准（GPS）的并网批准。"
    second = "项目一期规划360MW风电和132MWh储能，采用金风科技创新的风储直流耦合技术路线。"
    quoted = "项目一期规划360MW风电和132MWh储能" if short_quote else first + second
    ci = extract(first + second, **{
        "Project Name": "澳大利亚Baldon360MW风电+132MWh储能风储融合项目", "Country": "澳大利亚",
        "Capacity (MW)": "360MW", "Storage Power": "360MW", "Storage Energy": "132MWh",
        "Energy Composition Evidence": "项目一期规划360MW风电和132MWh储能",
        "Capacity Evidence": {"quote": quoted, "project_anchor": "Baldon风储融合项目"},
    })
    assert ci["capacity_mw_parsed"] == 360
    assert ci["storage_power_mw_parsed"] is None
    assert ci["storage_energy_mwh_parsed"] == 132
    if short_quote:
        assert ci["capacity_evidence_original"]["quote"] == quoted
        assert ci["Capacity Evidence"]["quote"] in first + second


def test_phase_anaphora_cannot_cross_other_project():
    text = "Baldon风储融合项目获得批准。Moorabool项目正在建设。项目一期规划360MW风电和132MWh储能。"
    ci = extract(text, **{
        "Project Name": "澳大利亚Baldon360MW风电项目", "Country": "澳大利亚",
        "Capacity (MW)": "360MW", "Storage Power": "", "Storage Energy": "132MWh",
        "Capacity Evidence": {"quote": "项目一期规划360MW风电和132MWh储能", "project_anchor": "Baldon风储融合项目"},
    })
    assert ci["capacity_mw_parsed"] is None
    assert ci["storage_energy_mwh_parsed"] is None


def test_live_duqm_overall_storage_specs_do_not_become_generation_capacity():
    text = "据悉，杜库姆储能项目规模为500MW/2000MWh，由阿曼国家电网公司主导开发。招标人为国电南瑞科技股份有限公司阿曼分公司。"
    ci = extract(text, **{
        "Project Name": "阿曼杜库姆500MW/2000MWh储能项目", "Country": "阿曼",
        "Category": "储能", "Generation Sources": [], "Capacity (MW)": "",
        "Storage Power": "500MW", "Storage Energy": "2000MWh",
        "Energy Composition Evidence": "杜库姆储能项目规模为500MW/2000MWh",
    })
    assert ci["capacity_mw_parsed"] is None
    assert ci["storage_power_mw_parsed"] == 500
    assert ci["storage_energy_mwh_parsed"] == 2000


def test_second_live_goldwind_short_site_quote_restores_two_sentence_context():
    text = SOURCE
    quote = "项目现场安装了两台金风科技单机容量4.5MW机组，塔筒高度达86米，叶片扫风直径136米。"
    ci = extract(text, **{
        "Capacity (MW)": "两台金风科技单机容量4.5MW机组",
        "Capacity Evidence": {"quote": quote, "project_anchor": "Río Cullen项目"},
    })
    assert ci["capacity_mw_parsed"] == 9
    assert ci["capacity_evidence_original"]["quote"] == quote
    assert ci["Capacity Evidence"]["quote"] in text
    assert "Río Cullen项目" in ci["Capacity Evidence"]["quote"]
    assert ci["storage_power_mw_parsed"] is None


@pytest.mark.parametrize("bridge", [
    "另一Delta项目同样安装风电机组。",
    "TotalEnergies近年来增加了能源投资。",
    "该项目正在准备；",
])
def test_site_quote_cannot_bridge_unrelated_or_ambiguous_sentence(bridge):
    quote = "项目现场安装了两台金风科技单机容量4.5MW机组。"
    text = "Río Cullen项目正在建设。" + bridge + quote
    ci = extract(text, **{"Capacity Evidence": {"quote": quote, "project_anchor": "Río Cullen项目"}})
    assert ci["capacity_mw_parsed"] is None
    assert "capacity_evidence_context_restored" not in ci


def test_site_quote_must_still_be_continuous_original_text():
    ci = extract(SOURCE, **{"Capacity Evidence": {
        "quote": "项目现场安装了两台金风科技单机容量5MW机组。", "project_anchor": "Río Cullen项目",
    }})
    assert ci["capacity_mw_parsed"] is None
    assert "capacity_evidence_context_restored" not in ci


def test_goldwind_source_anchor_variant_restores_only_shared_proper_name():
    text = "当地宣布，位于Río Cullen油田的风储项目成功投产。" + SOURCE
    quote = "项目现场安装了两台金风科技单机容量4.5MW机组，塔筒高度达86米，叶片扫风直径136米。"
    ci = extract(text, **{
        "Capacity (MW)": "两台金风科技单机容量4.5MW机组",
        "Capacity Evidence": {"quote": quote, "project_anchor": "Río Cullen油田的风储项目"},
        "Energy Composition Evidence": "孤岛运行",
    })
    assert ci["capacity_mw_parsed"] == 9
    assert ci["capacity_evidence_original"]["project_anchor"] == "Río Cullen油田的风储项目"
    assert ci["Capacity Evidence"]["project_anchor"] == "Río Cullen"
    assert ci["storage_status_normalized"] == "confirmed"
    assert ci["technology_tags_json"] == '["风储"]'
    assert ci["storage_power_mw_parsed"] is None
    assert ci["storage_energy_mwh_parsed"] is None


@pytest.mark.parametrize("status_sentence", [
    "位于Delta油田的风储项目成功投产。",
    "位于Río Cullen油田的风储项目预计明年投产。",
    "位于Río Cullen油田的风储项目未建成。",
])
def test_storage_status_not_borrowed_or_inferred_from_future_operation(status_sentence):
    ci = extract(status_sentence + SOURCE, **{"Energy Composition Evidence": "孤岛运行"})
    assert ci["storage_status_normalized"] != "confirmed"
    assert "storage_status_source_restored" not in ci


def test_unrelated_model_anchor_does_not_get_replaced_by_target_name():
    quote = "项目现场安装了两台金风科技单机容量4.5MW机组，塔筒高度达86米，叶片扫风直径136米。"
    ci = extract(SOURCE, **{"Capacity Evidence": {"quote": quote, "project_anchor": "Delta风储项目"}})
    assert ci["capacity_mw_parsed"] is None


def test_cayirlar_source_project_suffix_variant_keeps_original_model_anchor():
    text = "本次合作的Çayırlar风电项目总装机容量为43.5兆瓦（MW）。"
    ci = extract(text, **{
        "Project Name": "土耳其伊兹密尔Çayırlar风电场（风储融合）项目", "Country": "土耳其",
        "Location(State)": "伊兹密尔", "Capacity (MW)": "43.5兆瓦（MW）",
        "Capacity Evidence": {"quote": text, "project_anchor": "Çayırlar风电场"},
        "Storage Power": "", "Storage Energy": "",
    })
    assert ci["capacity_mw_parsed"] == 43.5
    assert ci["capacity_evidence_original"]["project_anchor"] == "Çayırlar风电场"
    assert ci["Capacity Evidence"]["project_anchor"] == "Çayırlar"


@pytest.mark.parametrize("anchor,quote", [
    ("Delta风电场", "本次合作的Çayırlar风电项目总装机容量为43.5兆瓦（MW）。"),
    ("Çayırlar风电场", "本次合作的Çayırlar风电项目总装机容量为99兆瓦（MW）。"),
])
def test_suffix_repair_never_accepts_different_project_or_invented_quote(anchor, quote):
    text = "本次合作的Çayırlar风电项目总装机容量为43.5兆瓦（MW）。"
    ci = extract(text, **{
        "Project Name": "土耳其伊兹密尔Çayırlar风电场项目", "Country": "土耳其", "Location(State)": "伊兹密尔",
        "Capacity (MW)": "43.5兆瓦（MW）", "Capacity Evidence": {"quote": quote, "project_anchor": anchor},
    })
    assert ci["capacity_mw_parsed"] is None


@pytest.mark.parametrize("configuration", [
    "采用亿纬锂能 Mr.Big Family 系列储能解决方案",
    "配备电池储能系统",
])
def test_malaysia_explicit_storage_configuration_is_confirmed_before_operation(configuration):
    text = "双方将共同落地马来西亚大型光储一体化标杆项目，" + configuration + "。\n该项目整体规划装机规模 40MWp，预计明年并网投运。"
    ci = extract(text, **{
        "Project Name": "马来西亚大型光储一体化标杆项目", "Country": "马来西亚",
        "Category": "光伏", "Generation Sources": ["光伏"], "Capacity (MW)": "40MWp",
        "Capacity Evidence": {"quote": text, "project_anchor": "马来西亚大型光储一体化标杆项目"},
        "Energy Composition Evidence": "光储一体化", "Storage Power": "", "Storage Energy": "",
    })
    assert ci["storage_status_normalized"] == "confirmed"
    assert ci["technology_tags_json"] == '["光储"]'
    assert ci["storage_status_evidence_original"]["quote"] == "光储一体化"
    assert ci["storage_power_mw_parsed"] is None
    assert ci["storage_energy_mwh_parsed"] is None


@pytest.mark.parametrize("configuration", ["拟采用储能解决方案", "考虑配置电池储能系统", "未配备储能系统", "可选配置储能系统"])
def test_optional_or_negative_storage_configuration_does_not_confirm(configuration):
    text = "Río Cullen项目" + configuration + "。"
    ci = extract(text, **{"Energy Composition Evidence": "孤岛运行", "Storage Power": "", "Storage Energy": ""})
    assert ci["storage_status_normalized"] != "confirmed"
    assert "storage_status_source_restored" not in ci


def test_storage_configuration_from_other_project_not_inherited():
    ci = extract("Río Cullen项目获批准。Delta项目配备储能系统。", **{"Energy Composition Evidence": "孤岛运行"})
    assert ci["storage_status_normalized"] != "confirmed"


@pytest.mark.parametrize("name,developer,developer_quote,expected", [
    ("埃及苏伊士运河经济区风电机组及关键部件制造工厂", "三一集团", "三一集团计划建设风电机组及关键部件制造工厂", None),
    ("埃及阳光电源储能电池工厂", "阳光电源", "阳光电源计划建设储能电池工厂", None),
    ("埃及Cornex公司电池储能工厂", "Cornex公司", "Cornex公司将投资2亿美元建设年产能5吉瓦时的电池储能工厂", 5),
])
def test_live_27057_manufacturing_capacity_belongs_only_to_cornex(name, developer, developer_quote, expected):
    text = "三一集团计划建设风电机组及关键部件制造工厂。阳光电源计划建设储能电池工厂，Cornex公司将投资2亿美元建设年产能5吉瓦时的电池储能工厂。"
    ci = extract(text, **{
        "Project Name": name, "Country": "埃及", "Category": "新能源制造业",
        "Generation Sources": [], "Project Developer": developer, "Project Developer Evidence": developer_quote,
        "MF Production Capacity Evidence": "年产能5吉瓦时的电池储能工厂",
        "Capacity Details": "年产能5吉瓦时的电池储能工厂",
    })
    assert ci["production_capacity_value_parsed"] == expected
    if expected is None:
        assert ci["capacity_needs_review"] is True
        assert ci["production_capacity_raw_phrase"] == ""
        assert "5吉瓦时" not in ci["Capacity Details"]
    else:
        assert ci["production_capacity_unit_normalized"] == "GWh/年"


@pytest.mark.parametrize("raw,expected", [("1.1吉瓦", 1100), ("900兆瓦", None)])
def test_live_27057_suez_quote_isolates_capacity_from_ras_shokeir(raw, expected):
    text = "苏伊士湾是全球公认的优质风场，吸引了多个大型风电项目，包括规划中的北苏伊士湾1000兆瓦集群、由中国电建承建的苏伊士湾1.1吉瓦项目（已于2025年底启动），以及法国ENGIE集团开发的Ras Shokeir 900兆瓦项目（预计2026年动工）。"
    ci = extract(text, **{
        "Project Name": "埃及苏伊士湾1.1吉瓦风电项目", "Country": "埃及", "Location(State)": "苏伊士湾",
        "Capacity (MW)": raw,
        "Capacity Evidence": {"quote": "由中国电建承建的苏伊士湾1.1吉瓦项目", "project_anchor": "苏伊士湾1.1吉瓦项目"},
        "Storage Power": "", "Storage Energy": "",
    })
    assert ci["capacity_mw_parsed"] == expected
