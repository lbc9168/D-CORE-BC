"""
TDD 测试：capacity_parser.parse_capacity_mw
覆盖：万千瓦/千瓦/GW/吉瓦/兆瓦/MW直读、带小数、产能类GWh/GW/年/万吨/年→null+details、
输送容量→null、解析失败→null、TX/MF→null。
"""
import pytest
from s01b_project_extractor.capacity_parser import parse_capacity_mw


# ── 基础单位换算 ──────────────────────────────────────────────────────────────

def test_wan_qianwa_integer():
    """600万千瓦 → 6000.0 MW（万千瓦×10）。"""
    mw, details = parse_capacity_mw("600万千瓦")
    assert mw == 6000.0
    assert details is None


def test_wan_qianwa_decimal():
    """95万千瓦 → 950.0 MW（万千瓦×10）。"""
    mw, details = parse_capacity_mw("95万千瓦")
    assert mw == 950.0
    assert details is None


def test_wan_qianwa_decimal_fraction():
    """12.5万千瓦 → 125.0 MW。"""
    mw, details = parse_capacity_mw("12.5万千瓦")
    assert mw == 125.0
    assert details is None


def test_qianwa_integer():
    """2242000千瓦 → 2242.0 MW（千瓦÷1000）。"""
    mw, details = parse_capacity_mw("2242000千瓦")
    assert mw == 2242.0
    assert details is None


def test_kw_integer():
    """50000kW → 50.0 MW（kW÷1000）。"""
    mw, details = parse_capacity_mw("50000kW")
    assert mw == 50.0
    assert details is None


def test_gw_integer():
    """3GW → 3000.0 MW（GW×1000）。"""
    mw, details = parse_capacity_mw("3GW")
    assert mw == 3000.0
    assert details is None


def test_gw_decimal():
    """2.5GW → 2500.0 MW。"""
    mw, details = parse_capacity_mw("2.5GW")
    assert mw == 2500.0
    assert details is None


def test_jiwa_integer():
    """3吉瓦 → 3000.0 MW（吉瓦×1000）。"""
    mw, details = parse_capacity_mw("3吉瓦")
    assert mw == 3000.0
    assert details is None


def test_jiwa_decimal():
    """1.4吉瓦 → 1400.0 MW。"""
    mw, details = parse_capacity_mw("1.4吉瓦")
    assert mw == 1400.0
    assert details is None


def test_megawatt_zh():
    """兆瓦直读 = MW，100兆瓦 → 100.0 MW。"""
    mw, details = parse_capacity_mw("100兆瓦")
    assert mw == 100.0
    assert details is None


def test_mw_direct_integer():
    """200MW 直读 → 200.0 MW。"""
    mw, details = parse_capacity_mw("200MW")
    assert mw == 200.0
    assert details is None


def test_mw_direct_decimal():
    """1.8MW → 1.8 MW。"""
    mw, details = parse_capacity_mw("1.8MW")
    assert mw == 1.8
    assert details is None


def test_mwp_treated_as_mw():
    """MWp（峰值）视同 MW。"""
    mw, details = parse_capacity_mw("150MWp")
    assert mw == 150.0
    assert details is None


# ── 产能类 → null + details ──────────────────────────────────────────────────

def test_gwh_is_null():
    """GWh（发电量非装机）→ (None, details)。"""
    mw, details = parse_capacity_mw("1GWh")
    assert mw is None
    assert details is not None and "GWh" in details


def test_gwh_year_is_null():
    """GWh/年（年发电量）→ null。"""
    mw, details = parse_capacity_mw("500GWh/年")
    assert mw is None
    assert details is not None


def test_gw_per_year_is_null():
    """GW/年（组件产能）→ null（产能非装机）。"""
    mw, details = parse_capacity_mw("5GW/年")
    assert mw is None
    assert details is not None


def test_wan_ton_per_year_is_null():
    """万吨/年（制造产能）→ null。"""
    mw, details = parse_capacity_mw("10万吨/年")
    assert mw is None
    assert details is not None


def test_ton_per_year_is_null():
    """吨/年 → null。"""
    mw, details = parse_capacity_mw("50000吨/年")
    assert mw is None
    assert details is not None


def test_mwh_is_null():
    """MWh（储能电量非装机）→ null + details（与 GWh 同理）。"""
    mw, details = parse_capacity_mw("200MWh")
    assert mw is None
    assert details is not None and "MWh" in details


# ── 输送容量 → null ──────────────────────────────────────────────────────────

def test_transmission_capacity_mva_is_null():
    """MVA（视在功率/输送容量）→ null。"""
    mw, details = parse_capacity_mw("500MVA")
    assert mw is None
    assert details is not None


def test_transmission_capacity_kva_is_null():
    """kVA → null。"""
    mw, details = parse_capacity_mw("1000kVA")
    assert mw is None
    assert details is not None


def test_transmission_capacity_kv_is_null():
    """kV（电压等级）→ null。"""
    mw, details = parse_capacity_mw("500kV")
    assert mw is None
    assert details is not None


# ── 解析失败 → null ──────────────────────────────────────────────────────────

def test_empty_string_is_null():
    """空字符串 → (None, None)。"""
    mw, details = parse_capacity_mw("")
    assert mw is None
    assert details is None


def test_none_input_is_null():
    """None 输入 → (None, None)。"""
    mw, details = parse_capacity_mw(None)
    assert mw is None
    assert details is None


def test_no_number_is_null():
    """无数字的文本 → null。"""
    mw, details = parse_capacity_mw("容量待定")
    assert mw is None


def test_unknown_unit_is_null():
    """无法识别的单位（如"桶/日"）→ null。"""
    mw, details = parse_capacity_mw("5000桶/日")
    assert mw is None


# ── TX/MF category → null ────────────────────────────────────────────────────

def test_tx_category_forces_null():
    """category='输变电建设' → (None, None) 不论 raw 是什么。"""
    mw, details = parse_capacity_mw("500MW", category="输变电建设")
    assert mw is None
    assert details is None


def test_mf_category_forces_null():
    """category='新能源制造业' → (None, None) 不论 raw 是什么。"""
    mw, details = parse_capacity_mw("5GW/年", category="新能源制造业")
    assert mw is None
    assert details is None


def test_pv_category_passes_through():
    """普通发电类别不受 TX/MF 置空影响。"""
    mw, details = parse_capacity_mw("100MW", category="光伏")
    assert mw == 100.0


# ── 边界：逗号分隔数字 ──────────────────────────────────────────────────────

def test_comma_separated_number():
    """1,200MW → 1200.0（去千位分隔符）。"""
    mw, details = parse_capacity_mw("1,200MW")
    assert mw == 1200.0


# ── Important #2：kWh 必须被拦截为 null（电量非装机）─────────────────────────

def test_kwh_is_null():
    """500kWh（储能电量）→ null + details（不得误解析为 0.5 MW）。"""
    mw, details = parse_capacity_mw("500kWh")
    assert mw is None
    assert details is not None


def test_kwh_upper_is_null():
    """500KWh（大写K/大写h）→ null + details。"""
    mw, details = parse_capacity_mw("500KWh")
    assert mw is None
    assert details is not None


def test_kwh_storage_phrase_is_null():
    """'200kWh 储能系统'中 kWh → null + details（不误读为装机）。"""
    mw, details = parse_capacity_mw("200kWh 储能系统")
    assert mw is None
    assert details is not None


# ── 终审 I1：中文电量单位 兆瓦时/吉瓦时 必须拦截为 null（曾漏拦产错值）────────

def test_zhaowashi_is_null():
    """200兆瓦时（=MWh 电量）→ null + details（曾被 _MW_RE 误读为 200MW 装机）。"""
    mw, details = parse_capacity_mw("200兆瓦时")
    assert mw is None
    assert details is not None and "兆瓦时" in details


def test_jiwashi_is_null():
    """5吉瓦时（=GWh 电量）→ null + details（曾被 _GW_RE 误读为 5000MW）。"""
    mw, details = parse_capacity_mw("5吉瓦时")
    assert mw is None
    assert details is not None and "吉瓦时" in details


# ── 终审 I2 搭车：混合能源短语的解析器现行为固化 ─────────────────────────────

def test_mixed_phrase_takes_first_mw_number():
    """已知取舍：混合短语「光伏100MW+储能50MW」当前取第一个 MW 数字 → 100.0。

    S2 prompt（2026-07-06 终审 I2 起）要求：原文有总容量短语则逐字抄总容量短语、
    无总数则 Capacity (MW) 留空、子项明细进 Capacity Details——正常路径不会把
    混合明细串传进解析器。本断言固化解析器对漏网混合短语的现行为（取首个装机数字），
    防止未察觉的行为漂移；若未来改为返回 null+details，须显式改此测试。"""
    mw, details = parse_capacity_mw("光伏100MW+储能50MW")
    assert mw == 100.0
    assert details is None
