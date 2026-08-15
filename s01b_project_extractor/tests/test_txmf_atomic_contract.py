"""S01B TX/MF 原子指标、证据 surface 与能源域隔离的回归测试。"""
import json

from s01b_project_extractor.stages.s2_basic import run_s2, validate_s2


class StubLLM:
    """返回固定 S2 JSON，便于验证确定性后处理。"""

    def __init__(self, common_info):
        self.common_info = common_info

    def complete(self, _prompt, **_kwargs):
        """返回单项目固定响应。"""
        return {"projects": [{"common_info": dict(self.common_info)}]}


def _base(category):
    """构造最小合法 S2 common_info。"""
    return {
        "Project Name": "肯尼亚测试项目",
        "Category": category,
        "Project Stage": "在建",
        "Generation Sources": ["光伏"],
        "Storage Status": "confirmed",
        "Storage Power": "50MW",
        "Storage Energy": "100MWh",
        "Energy Composition Evidence": "光伏和储能",
        "Capacity (MW)": "100MW",
    }


def test_tx_atomic_fields_parse_and_force_capacity_empty_from_title():
    """TX 可引用标题证据，三种规格独立解析且不写发电装机。"""
    common = _base("输变电建设")
    common.update({
        "TX Voltage Evidence": "132kV",
        "TX Line Length Evidence": "全长27km",
        "TX Transmission Capacity Evidence": "3×40MVA",
    })
    project = run_s2("132kV线路全长27km，配置3×40MVA", "正文未重复规格", "2026-01-01",
                     llm=StubLLM(common))[0]
    result = project["common_info"]
    assert result["capacity_mw_parsed"] is None
    assert result["capacity_mw_raw_phrase"] == ""
    assert result["voltage_kv_parsed"] == 132
    assert result["line_length_km_parsed"] == 27
    assert result["transmission_capacity_mw_parsed"] == 120
    assert json.loads(result["generation_sources_json"]) == []
    assert result["storage_status_normalized"] == "not_mentioned"
    assert json.loads(result["technology_tags_json"]) == []


def test_mf_atomic_gwh_per_year_is_not_storage_energy():
    """电池厂 GWh/年进入制造产能，不得进入 confirmed 储能。"""
    common = _base("新能源制造业")
    common["MF Production Capacity Evidence"] = "年产能10GWh"
    project = run_s2("电池工厂", "项目建成后年产能10GWh。", "2026-01-01",
                     llm=StubLLM(common))[0]
    result = project["common_info"]
    assert result["production_capacity_value_parsed"] == 10
    assert result["production_capacity_unit_normalized"] == "GWh/年"
    assert result["storage_energy_mwh_parsed"] is None
    assert result["capacity_mw_parsed"] is None


def test_nonmatching_or_nonverbatim_evidence_is_rejected_without_crashing():
    """非对应领域字段和非逐字证据进入校验错误，并在落库前被清空。"""
    common = _base("光伏")
    common["TX Voltage Evidence"] = "不存在的500kV"
    projects = run_s2("光伏项目", "装机100MW", "2026-01-01", llm=StubLLM(common))
    assert projects[0]["common_info"]["TX Voltage Evidence"] == ""
    assert any("TX Voltage Evidence" in error for error in validate_s2(projects))


def test_absent_txmf_evidence_needs_no_error():
    """未提及专用指标时留空即可，不要求虚构证据。"""
    common = _base("输变电建设")
    projects = run_s2("某变电站项目", "项目正式开工。", "2026-01-01", llm=StubLLM(common))
    assert validate_s2(projects) == []
