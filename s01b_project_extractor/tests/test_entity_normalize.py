"""母子公司归一测试（已按 v2 契约重写）：子公司/别名/集团自身 → 规范顶级集团。

数据源：company_hierarchy_v2.json（schema_version=2）。

旧断言变更清单（按新契约重写）：
  1. test_known_subsidiary_maps_to_parent
       旧：仅断言 parent；未断言 sub
       新：同时断言 sub == verbatim（不变量：子公司命中时 sub=原文）
  2. test_known_sub3_maps_to_root_parent
       旧：仅断言 parent；未断言 sub
       新：同时断言 sub == verbatim
  3. test_second_group_subsidiary_maps_correctly
       旧：仅断言 parent
       新：同时断言 sub == verbatim
  4. test_top_level_parent_maps_to_itself
       旧/新 一致：(parent, "") 无变更
  5. test_unknown_entity_passthrough
       旧：parent=verbatim, sub="" —— 但旧实现有包含匹配，该断言在旧版可能已通过（不在表就透传）
       新：同样透传，但明确是因为三层都未命中，不是包含匹配回退
  6. test_empty_returns_unknown / test_whitespace_returns_unknown
       不变

Real pairs from company_hierarchy_v2.json:
  KNOWN_SUBSIDIARY = "中国葛洲坝集团有限公司"   # sub of 中国能源建设集团有限公司
  KNOWN_PARENT     = "中国能源建设集团有限公司"   # top-level group
  KNOWN_SUB3       = "中国葛洲坝集团（甘肃）工程有限公司" # direct sub of 中国能源建设集团有限公司（v2 扁平化）
  KNOWN_SUB2_OTHER = "中电建新能源集团股份有限公司"        # sub of 中国电力建设集团有限公司
  KNOWN_PARENT_2   = "中国电力建设集团有限公司"
"""
import pytest
from s01b_project_extractor.stages.entity_normalize import (
    UNMATCHED_NEEDS_REVIEW,
    VERIFIED_CHINESE,
    VERIFIED_NON_CHINESE,
    normalize_entity,
    resolve_entity,
)


def _clear_cache():
    """清 v2 索引 lru_cache + 去尾缀预构索引，隔离测试。"""
    from s01b_project_extractor.stages import entity_normalize
    entity_normalize._hierarchy_index_v2.cache_clear()
    entity_normalize._nationality_index.cache_clear()
    entity_normalize._stripped_index_cache = {}


@pytest.fixture(autouse=True)
def auto_clear():
    _clear_cache()
    yield
    _clear_cache()


# Real values from company_hierarchy_v2.json
KNOWN_SUBSIDIARY = "中国葛洲坝集团有限公司"           # sub of 中国能源建设集团有限公司
KNOWN_PARENT     = "中国能源建设集团有限公司"           # top-level group
KNOWN_SUB3       = "中国葛洲坝集团（甘肃）工程有限公司"  # direct sub (v2 扁平化) under KNOWN_PARENT
KNOWN_SUB2_OTHER = "中电建新能源集团股份有限公司"         # sub of 中国电力建设集团有限公司
KNOWN_PARENT_2   = "中国电力建设集团有限公司"


def test_known_subsidiary_maps_to_parent():
    """直接子公司应归一到顶级集团，sub 等于 verbatim（不变量）。"""
    parent, sub = normalize_entity(KNOWN_SUBSIDIARY)
    assert parent == KNOWN_PARENT, f"Expected parent={KNOWN_PARENT!r}, got {parent!r}"
    # 新契约：路径1 命中 → sub = verbatim
    assert sub == KNOWN_SUBSIDIARY, f"Expected sub=verbatim={KNOWN_SUBSIDIARY!r}, got {sub!r}"


def test_known_sub3_maps_to_root_parent():
    """子公司（v2 中扁平化，原 sub3）应归一到同一顶级集团，sub=verbatim。"""
    parent, sub = normalize_entity(KNOWN_SUB3)
    assert parent == KNOWN_PARENT, f"Expected parent={KNOWN_PARENT!r}, got {parent!r}"
    # 新契约：sub = verbatim
    assert sub == KNOWN_SUB3, f"Expected sub=verbatim={KNOWN_SUB3!r}, got {sub!r}"


def test_second_group_subsidiary_maps_correctly():
    """另一集团的子公司应归一到其对应顶级集团，sub=verbatim。"""
    parent, sub = normalize_entity(KNOWN_SUB2_OTHER)
    assert parent == KNOWN_PARENT_2, f"Expected parent={KNOWN_PARENT_2!r}, got {parent!r}"
    # 新契约：sub = verbatim
    assert sub == KNOWN_SUB2_OTHER, f"Expected sub=verbatim={KNOWN_SUB2_OTHER!r}, got {sub!r}"


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


def test_malaysia_non_chinese_companies_are_explicitly_blocked():
    """Citaglobal/BGMC 命中国别登记表后必须明确判为非中企。"""
    assert resolve_entity("Citaglobal").verification_status == VERIFIED_NON_CHINESE
    assert resolve_entity("BGMC Holdings").verification_status == VERIFIED_NON_CHINESE


def test_reviewed_malaysia_parent_mappings_are_chinese_verified():
    """中电国际和三峡发展（马）应分别归国家电投和三峡集团。"""
    china_power = resolve_entity("中电国际")
    assert china_power.verification_status == VERIFIED_CHINESE
    assert china_power.implementing == "国家电力投资集团有限公司"
    three_gorges = resolve_entity("三峡发展（马）公司")
    assert three_gorges.verification_status == VERIFIED_CHINESE
    assert three_gorges.implementing == "中国长江三峡集团有限公司"


def test_unmatched_entity_requires_review():
    """未命中任何受控表的名称不得作为已核实中企。"""
    assert resolve_entity("某未登记海外企业").verification_status == UNMATCHED_NEEDS_REVIEW
