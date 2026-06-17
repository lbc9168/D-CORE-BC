"""测试 _normalize_capacity_mw：物理不可能的超大 MW（≥50000，几乎必是千瓦原数误填）÷1000 还原；正常值原样。"""
from s01b_project_extractor.tools.project_extractor_tool import _normalize_capacity_mw


def test_impossible_large_divided_by_1000():
    """≥50000 MW 视为千瓦误填 → ÷1000（如 600万千瓦误填 6000000 → 6000 MW）。"""
    assert _normalize_capacity_mw(6000000.0) == 6000.0
    assert _normalize_capacity_mw(950000.0) == 950.0
    assert _normalize_capacity_mw(2242000.0) == 2242.0


def test_threshold_boundary():
    """恰好 50000 视为不可能 → ÷1000；49999 视为正常保留。"""
    assert _normalize_capacity_mw(50000.0) == 50.0
    assert _normalize_capacity_mw(49999.0) == 49999.0


def test_normal_values_unchanged():
    """正常量级 MW 原样返回。"""
    for v in (1.0, 60.0, 1320.0, 22500.0):
        assert _normalize_capacity_mw(v) == v


def test_none_and_nonnumeric():
    """None 原样；非数值原样返回（不抛错）。"""
    assert _normalize_capacity_mw(None) is None
    assert _normalize_capacity_mw("abc") == "abc"
