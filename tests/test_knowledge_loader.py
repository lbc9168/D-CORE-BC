"""knowledge 加载器测试：主要设备表与容量系数表读取正确。"""
from s01b_project_extractor.knowledge import main_equipment, capacity_factor, derive_capacity_mw


def test_main_equipment_for_known_category():
    items = main_equipment("光伏")
    assert "组件" in items and "逆变器" in items


def test_main_equipment_unknown_category_returns_empty():
    assert main_equipment("不存在的分类") == []


def test_capacity_factor_lookup():
    assert capacity_factor("光伏") == 0.17
    assert capacity_factor("水电") == 0.45


def test_derive_capacity_from_annual_generation():
    # 光伏 factor 0.17：年发电量 14_892_000 MWh → 约 10000 MW
    mw = derive_capacity_mw(14_892_000, "光伏")
    assert 9900 < mw < 10100


def test_derive_capacity_unknown_category_returns_none():
    assert derive_capacity_mw(1000, "未知") is None
