"""S4.1：细 stage → 4 可视化大类映射（单一可信源 enums_mapping.json）。"""
import bri_enums


def test_viz_group_mapping():
    assert bri_enums.viz_group("中标") == "pre_construction"
    assert bri_enums.viz_group("签约") == "pre_construction"
    assert bri_enums.viz_group("开工") == "under_construction"
    assert bri_enums.viz_group("在建") == "under_construction"
    assert bri_enums.viz_group("竣工") == "completed"
    assert bri_enums.viz_group("投入运营") == "completed"
    assert bri_enums.viz_group("收购") == "acquired"
    assert bri_enums.viz_group("拟开发") is None
    assert bri_enums.viz_group("Unknown") is None
    assert bri_enums.viz_group("不存在的") is None


def test_viz_groups_dict_complete():
    g = bri_enums.viz_groups()
    assert set(g.keys()) == {
        "pre_construction", "under_construction", "completed", "acquired"
    }
    for meta in g.values():
        assert "zh" in meta and "en" in meta and "color" in meta and "order" in meta
