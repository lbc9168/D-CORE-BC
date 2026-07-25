"""测试 storage_parser：extract_storage 与 migrate_v2 行为对拍；extract_pv_capacity 中英格式。"""
from s01b_project_extractor.storage_parser import (
    extract_pv_capacity,
    extract_storage,
    mentions_pv,
)


# ── extract_storage（与 scripts/migrate_v2.py 原正则行为对拍） ──

def test_storage_power_requires_keyword_before_number():
    """储能关键词在数值前才认功率；发电容量不误抓。"""
    assert extract_storage("储能100MW") == (100.0, None)
    assert extract_storage("BESS: 50MW") == (50.0, None)
    assert extract_storage("光伏电站100MW") == (None, None)


def test_storage_power_and_energy_pair():
    """功率与能量可同时提取。"""
    assert extract_storage("储能规模100MW/200MWh") == (100.0, 200.0)


def test_storage_energy_matches_mwh_without_keyword():
    """MWh 直读（migrate_v2 原行为：能量单位本身即储能语义）。"""
    assert extract_storage("电池容量 400MWh") == (None, 400.0)


def test_storage_energy_gwh_and_chinese_units():
    """GWh/吉瓦时 ×1000 换算、兆瓦时=MWh（misses 实证扩展）。"""
    assert extract_storage("BESS: 11.98 GWh") == (None, 11980.0)
    assert extract_storage("配套4吉瓦时电池储能") == (None, 4000.0)
    assert extract_storage("储能规模200兆瓦时") == (None, 200.0)


def test_storage_empty_or_none():
    assert extract_storage(None) == (None, None)
    assert extract_storage("") == (None, None)


# ── extract_pv_capacity（混合项目光伏子项） ──

def test_pv_capacity_english_colon_format():
    """prompt 标准格式 'PV: 100MW, BESS: 50MW' 提取光伏侧。"""
    assert extract_pv_capacity("PV: 100MW, BESS: 50MW") == 100.0


def test_pv_capacity_chinese_formats():
    assert extract_pv_capacity("光伏装机容量约200兆瓦") == 200.0
    assert extract_pv_capacity("光伏70万千瓦，配套储能") == 700.0


def test_pv_capacity_gw_and_kw_units():
    """GW/吉瓦 ×1000、kW/kWp/千瓦 ÷1000（misses 实证扩展）。"""
    assert extract_pv_capacity("PV: 1.7GW, BESS: 4GWh") == 1700.0
    assert extract_pv_capacity("光伏2吉瓦基地") == 2000.0
    assert extract_pv_capacity("PV: 359kW, BESS: 366kW-500kWh") == 0.359
    assert extract_pv_capacity("光伏500千瓦车棚") == 0.5


def test_pv_capacity_energy_units_not_misread_as_power():
    """兆瓦时/千瓦时/吉瓦时是能量单位，不得误读为光伏功率。"""
    assert extract_pv_capacity("光伏储能系统100兆瓦时") is None
    assert extract_pv_capacity("光伏配储500千瓦时") is None
    assert extract_pv_capacity("光伏储能2吉瓦时") is None


def test_pv_capacity_solar_keyword():
    assert extract_pv_capacity("solar 50 MW + BESS 20 MWh") == 50.0


def test_pv_capacity_requires_pv_keyword():
    """无光伏关键词不提取（储能功率不误当光伏）。"""
    assert extract_pv_capacity("储能100MW") is None


def test_pv_capacity_mwh_not_matched_as_mw():
    """MWh 是能量单位，不得当作光伏 MW。"""
    assert extract_pv_capacity("PV: 100MWh") is None


def test_pv_capacity_empty_or_none():
    assert extract_pv_capacity(None) is None
    assert extract_pv_capacity("") is None


# ── mentions_pv（misses 判定用） ──

def test_mentions_pv():
    assert mentions_pv("光伏+储能一体化") is True
    assert mentions_pv("纯储能电站") is False
    assert mentions_pv(None) is False
