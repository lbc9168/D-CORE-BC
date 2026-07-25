"""测试 subcategory_rules：光伏分布式证据判定、综合能源光储判定（含负例与结构化储能路径）。"""
import bri_enums as enums

from s01b_project_extractor.subcategory_rules import classify_hb, classify_pv


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


# ── classify_hb ──

def test_hb_solar_storage_via_text():
    assert classify_hb(["PV: 100MW, BESS: 50MWh"], False) == "光储"
    assert classify_hb(["光伏+储能一体化项目"], False) == "光储"


def test_hb_solar_storage_via_structured_column():
    """文本只提光伏、储能证据来自结构化列（has_storage_value=True）。"""
    assert classify_hb(["光伏100MW电站"], True) == "光储"


def test_hb_wind_solar_storage_is_other():
    """含风（风光储）→ 非纯光储，返回 None（=其他）。"""
    assert classify_hb(["风光储一体化项目"], True) is None


def test_hb_hydrogen_is_other():
    """光伏制氢保守归其他。"""
    assert classify_hb(["光伏制氢一体化项目，配套储能"], False) is None


def test_hb_no_pv_returns_none():
    assert classify_hb(["综合智慧能源项目"], False) is None
    assert classify_hb(["储能电站"], True) is None


# ── 返回值合法性（子类值必须在 enums 定义内） ──

def test_return_values_are_valid_enum_keys():
    assert "分布式" in enums.subcategories("光伏")
    assert "光储" in enums.subcategories("综合能源")
