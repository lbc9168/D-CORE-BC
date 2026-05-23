"""数值解析单一可信源测试。"""
import bri_parse as p


def test_plain():
    assert p.parse_number("50") == 50.0
    assert p.parse_number("50.5") == 50.5


def test_with_unit_and_space():
    assert p.parse_number("50 MW") == 50.0
    assert p.parse_number("100MW") == 100.0
    assert p.parse_number("1,200") == 1200.0


def test_approx():
    assert p.parse_number("约50") == 50.0
    assert p.parse_number("约 50 兆瓦") == 50.0


def test_range_takes_midpoint():
    assert p.parse_number("50-60") == 55.0
    assert p.parse_number("50-60MW") == 55.0
    assert p.parse_number("50~60") == 55.0


def test_unparseable_returns_none():
    assert p.parse_number("") is None
    assert p.parse_number(None) is None
    assert p.parse_number("待定") is None
    assert p.parse_number("nan") is None


def test_negative_and_zero():
    assert p.parse_number("0") == 0.0
