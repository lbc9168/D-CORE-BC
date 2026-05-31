"""母子公司归一测试：子公司/二级集团 → 规范顶级集团（复用 company_hierarchy.json）。

Real pair used:
  KNOWN_SUBSIDIARY = "中国葛洲坝集团有限公司"   # subsidiaries_level2 of:
  KNOWN_PARENT     = "中国能源建设集团有限公司"   # top-level (parent_company_cn=null)

Also tested:
  SUB3 under 中国葛洲坝集团有限公司:
  KNOWN_SUB3       = "中国葛洲坝集团（甘肃）工程有限公司"
  → parent stays   = "中国能源建设集团有限公司"
"""
from s01b_project_extractor.stages.entity_normalize import normalize_entity

# Real values from company_hierarchy.json
KNOWN_SUBSIDIARY = "中国葛洲坝集团有限公司"           # sub2 of 中国能源建设集团有限公司
KNOWN_PARENT     = "中国能源建设集团有限公司"           # top-level group
KNOWN_SUB3       = "中国葛洲坝集团（甘肃）工程有限公司" # sub3 under KNOWN_SUBSIDIARY
KNOWN_SUB2_OTHER = "中电建新能源集团股份有限公司"        # sub2 of 中国电力建设集团有限公司
KNOWN_PARENT_2   = "中国电力建设集团有限公司"


def test_known_subsidiary_maps_to_parent():
    """直接子公司应归一到顶级集团。"""
    parent, sub = normalize_entity(KNOWN_SUBSIDIARY)
    assert parent == KNOWN_PARENT, f"Expected parent={KNOWN_PARENT!r}, got {parent!r}"


def test_known_sub3_maps_to_root_parent():
    """三级子公司应归一到同一顶级集团。"""
    parent, sub = normalize_entity(KNOWN_SUB3)
    assert parent == KNOWN_PARENT, f"Expected parent={KNOWN_PARENT!r}, got {parent!r}"


def test_second_group_subsidiary_maps_correctly():
    """另一集团的子公司应归一到其对应顶级集团。"""
    parent, sub = normalize_entity(KNOWN_SUB2_OTHER)
    assert parent == KNOWN_PARENT_2, f"Expected parent={KNOWN_PARENT_2!r}, got {parent!r}"


def test_top_level_parent_maps_to_itself():
    """顶级集团名称本身应归一到自身（sub 为空）。"""
    parent, sub = normalize_entity(KNOWN_PARENT)
    assert parent == KNOWN_PARENT
    assert sub == ""


def test_unknown_entity_passthrough():
    """不在表中的公司名应原样透传，sub 为空。"""
    parent, sub = normalize_entity("某不在表中的公司XYZ")
    assert parent == "某不在表中的公司XYZ"
    assert sub == ""


def test_empty_returns_unknown():
    """空字符串应返回 ('Unknown', '')。"""
    parent, sub = normalize_entity("")
    assert parent == "Unknown"
    assert sub == ""


def test_whitespace_returns_unknown():
    """纯空白应返回 ('Unknown', '')。"""
    parent, sub = normalize_entity("   ")
    assert parent == "Unknown"
    assert sub == ""
