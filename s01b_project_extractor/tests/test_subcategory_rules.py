"""测试 subcategory_rules：子类只表达光伏集中式/分布式项目形态。"""
import bri_enums as enums

from s01b_project_extractor.subcategory_rules import classify_pv


# ── classify_pv ──

def test_pv_distributed_chinese_keywords():
    assert classify_pv(["某地屋顶光伏项目"]) == "分布式"
    assert classify_pv(["工商业分布式光伏电站"]) == "分布式"
    assert classify_pv(["户用光伏计划"]) == "分布式"


def test_pv_distributed_english_keywords():
    assert classify_pv(["Rooftop solar project in Vietnam"]) == "分布式"
    assert classify_pv(["A distributed PV portfolio"]) == "分布式"


def test_pv_no_evidence_returns_none():
    """无证据返回 None（DB 存 NULL，导出层默认集中式——用户拍板）。"""
    assert classify_pv(["100MW地面光伏电站"]) is None
    assert classify_pv([]) is None


def test_pv_mixed_language_texts():
    """多段中英混排文本任一命中即可。"""
    assert classify_pv(["100MW solar plant", "该项目为屋面光伏"]) == "分布式"


# ── 返回值合法性（子类值必须在 enums 定义内） ──

def test_return_values_are_valid_enum_keys():
    assert "分布式" in enums.subcategories("光伏")
    assert enums.subcategories("综合能源") == []
