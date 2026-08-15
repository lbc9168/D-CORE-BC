"""分类数据域、储能状态与技术标签父类门禁的枚举契约测试。"""
import bri_enums as enums


def test_txmf_category_profiles_are_explicit():
    """TX/MF Category 必须声明 txmf 数据域及专用规格类型。"""
    assert enums.category_data_domain("输变电建设") == "txmf"
    assert enums.category_spec_profile("输变电建设") == "tx"
    assert enums.category_data_domain("新能源制造业") == "txmf"
    assert enums.category_spec_profile("新能源制造业") == "mf"


def test_controlled_tags_have_strict_parent_categories():
    """四个技术标签只在各自父类下合法，且不是无条件四选一。"""
    assert enums.valid_technology_tags() == ["光储", "风储", "风光", "风光储"]
    assert enums.technology_tag_allowed("光伏", "光储")
    assert not enums.technology_tag_allowed("综合能源", "光储")
    assert enums.technology_tag_allowed("风电", "风储")
    assert enums.technology_tag_allowed("综合能源", "风光")
    assert enums.technology_tag_allowed("综合能源", "风光储")


def test_storage_priority_and_mf_units_are_controlled():
    """储能状态优先级及既有 MF 四单位由单一映射提供。"""
    assert enums.storage_status_priority("confirmed") > enums.storage_status_priority("planned")
    assert enums.mf_production_units() == ["GW/年", "GWh/年", "万吨/年", "Unknown"]
