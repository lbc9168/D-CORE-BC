"""测试 S2 对 energy_taxonomy_v2 的确定性接线和错误输出降级。"""

from s01b_project_extractor.stages.s2_basic import run_s2, validate_s2


class StubLLM:
    """返回固定 S2 JSON 的最小 LLM 桩。"""

    def __init__(self, common_info):
        self.common_info = common_info

    def complete(self, prompt, *, stage, json_mode):
        """忽略 prompt 并返回单项目结果。"""
        return {"projects": [{"common_info": dict(self.common_info)}]}


def test_s2_deterministically_reclassifies_solar_storage():
    """模型旧口径输出综合能源时，原子事实必须把光储改回光伏并保留独立容量。"""
    projects = run_s2("t", "c", "2026-01-01", llm=StubLLM({
        "Project Name": "肯尼亚光储项目",
        "Category": "综合能源",
        "Generation Sources": ["光伏"],
        "Storage Status": "confirmed",
        "Storage Power": "20MW",
        "Storage Energy": "40MWh",
        "Energy Composition Evidence": "建设光伏电站并配套储能系统",
        "Capacity (MW)": "100MW",
    }))
    ci = projects[0]["common_info"]
    assert ci["Category"] == "光伏"
    assert ci["storage_power_mw_parsed"] == 20.0
    assert ci["storage_energy_mwh_parsed"] == 40.0
    assert ci["technology_tags_json"] == '["光储"]'


def test_s2_filters_invalid_generation_source_without_crashing():
    """混入储能等非法发电源时，后处理应保留合法值且 validate 仍可直接发现原始坏输入。"""
    raw = {
        "Project Name": "肯尼亚光伏项目",
        "Category": "综合能源",
        "Generation Sources": ["光伏", "储能"],
        "Storage Status": "confirmed",
        "Energy Composition Evidence": "光伏配套储能",
    }
    assert validate_s2([{"common_info": dict(raw)}]) == ["项目#0 发电源不合法: ['储能']"]
    projects = run_s2("t", "c", "2026-01-01", llm=StubLLM(raw))
    assert projects[0]["common_info"]["Category"] == "光伏"


def test_s2_downgrades_unsubstantiated_confirmed_storage():
    """证据未确认配储时，不接受模型 confirmed 及其容量。"""
    projects = run_s2("t", "c", "2026-01-01", llm=StubLLM({
        "Project Name": "肯尼亚光伏项目",
        "Category": "光伏",
        "Generation Sources": ["光伏"],
        "Storage Status": "confirmed",
        "Storage Power": "20MW",
        "Storage Energy": "40MWh",
        "Energy Composition Evidence": "建设100MW光伏电站",
    }))
    ci = projects[0]["common_info"]
    assert ci["storage_status_normalized"] == "not_mentioned"
    assert ci["storage_power_mw_parsed"] is None
    assert ci["storage_energy_mwh_parsed"] is None
    assert ci["technology_tags_json"] == "[]"
