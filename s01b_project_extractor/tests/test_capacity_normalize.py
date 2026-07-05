"""
_normalize_capacity_mw 已删除（换算由 capacity_parser.parse_capacity_mw 接管）。
本文件保留为等效验证：原 _normalize_capacity_mw 的三个核心场景，现在由 parse_capacity_mw
通过逐字短语解析来正确处理——不再需要 ≥50000 ÷1000 的启发式兜底。

等效场景：
  "6000000千瓦"（原始短语）→ 6000.0 MW（千瓦÷1000）
  "950000千瓦"（原始短语）  → 950.0 MW
  "2242000千瓦"（原始短语）  → 2242.0 MW
"""
from s01b_project_extractor.capacity_parser import parse_capacity_mw


def test_large_qianwa_converts_correctly():
    """原千瓦大数经 parse_capacity_mw 正确换算（不依赖 ≥50000 启发式）。"""
    mw, _ = parse_capacity_mw("6000000千瓦")
    assert mw == 6000.0

    mw, _ = parse_capacity_mw("950000千瓦")
    assert mw == 950.0

    mw, _ = parse_capacity_mw("2242000千瓦")
    assert mw == 2242.0


def test_wan_qianwa_converts_correctly():
    """万千瓦短语（如 LLM 新输出格式）正确换算（×10）。"""
    mw, _ = parse_capacity_mw("600万千瓦")
    assert mw == 6000.0

    mw, _ = parse_capacity_mw("95万千瓦")
    assert mw == 950.0


def test_normal_mw_unchanged():
    """正常 MW 量级直接解析，不受任何 ÷1000 影响。"""
    for raw, expected in [("1MW", 1.0), ("60MW", 60.0), ("1320MW", 1320.0), ("22500MW", 22500.0)]:
        mw, _ = parse_capacity_mw(raw)
        assert mw == expected, f"{raw} → 期望 {expected}，实际 {mw}"


def test_none_and_empty_return_null():
    """None / 空字符串 → (None, None)。"""
    assert parse_capacity_mw(None) == (None, None)
    assert parse_capacity_mw("") == (None, None)
