"""Legal identity contracts: preserve recall without assigning arbitrary companies."""
import importlib.util
import json
from pathlib import Path

import pytest

from s01b_project_extractor.stages import entity_normalize as normalizer


@pytest.fixture(autouse=True)
def isolated_indexes():
    normalizer._hierarchy_index_v2.cache_clear()
    normalizer._nationality_index.cache_clear()
    yield
    normalizer._hierarchy_index_v2.cache_clear()
    normalizer._nationality_index.cache_clear()


def _use_groups(monkeypatch, groups):
    monkeypatch.setattr(normalizer, "_load_hierarchy_v2", lambda: {"groups": groups})
    monkeypatch.setattr(normalizer, "_nationality_index", lambda: {})


def test_confirmed_aliases_share_actual_entity_and_preserve_raw():
    short = normalizer.resolve_entity("北方国际")
    legal = normalizer.resolve_entity("北方国际合作股份有限公司")
    english = normalizer.resolve_entity("norinco international cooperation ltd.")
    assert short.entity_id == legal.entity_id == english.entity_id
    assert short.entity_name == legal.entity_name == "北方国际合作股份有限公司"
    assert short.raw_name == "北方国际"
    assert short.implementing == "中国兵器工业集团有限公司"
    assert short.parent_entity_id == normalizer.resolve_entity("中国北方工业有限公司").entity_id


def test_shared_brand_keeps_group_without_inventing_legal_company():
    result = normalizer.resolve_entity("中国能建")
    assert result.verification_status == normalizer.VERIFIED_CHINESE
    assert result.group_id == normalizer.resolve_entity("中国能源建设集团有限公司").entity_id
    assert result.entity_id is None
    assert result.entity_name == "中国能建"


def test_child_is_not_parent_alias():
    child = normalizer.resolve_entity("海油工程")
    parent = normalizer.resolve_entity("中国海洋石油集团有限公司")
    assert child.entity_name == "海洋石油工程股份有限公司"
    assert child.entity_id != parent.entity_id
    assert child.group_id == parent.entity_id
    assert child.parent_entity_id == parent.entity_id


def test_typographic_parentheses_and_suffix_lookup_are_safe():
    a = normalizer.resolve_entity("中国葛洲坝集团（甘肃）工程有限公司")
    b = normalizer.resolve_entity("中国葛洲坝集团(甘肃)工程有限公司")
    assert a.entity_id == b.entity_id and a.entity_id
    assert normalizer.resolve_entity("国家电网").entity_id == normalizer.resolve_entity("国家电网有限公司").entity_id
    # Group and listed company share this stem; only the group is identifiable.
    assert normalizer.resolve_entity("哈尔滨电气").entity_id is None
    assert normalizer.resolve_entity("哈尔滨电气").group_id


def test_canonical_english_name_is_indexed_not_machine_translation():
    result = normalizer.resolve_entity("LONGi Green Energy Technology Co., Ltd.")
    assert result.entity_id == normalizer.resolve_entity("隆基").entity_id
    assert normalizer.resolve_entity("Longi Invented Overseas Company").entity_id is None


def test_missing_legal_name_preserves_known_group_only():
    result = normalizer.resolve_entity("上海电气中央研究院")
    assert result.verification_status == normalizer.VERIFIED_CHINESE
    assert result.group_id and result.entity_id is None
    assert result.parent_entity_id is None


def test_recurrent_platform_and_distinct_legal_entities_follow_user_research_scope():
    assert normalizer.resolve_entity("Citaglobal").verification_status == normalizer.VERIFIED_NON_CHINESE
    platform = normalizer.resolve_entity("Recurrent Energy")
    llc = normalizer.resolve_entity("Recurrent Energy, LLC")
    bv = normalizer.resolve_entity("Recurrent Energy B.V.")
    assert platform.verification_status == normalizer.VERIFIED_CHINESE
    assert platform.entity_id is None
    assert platform.group_id == llc.group_id == bv.group_id
    assert llc.entity_id and bv.entity_id and llc.entity_id != bv.entity_id
    assert llc.group_id != normalizer.resolve_entity("阿特斯阳光电力集团股份有限公司").entity_id
    assert llc.parent_entity_id is None  # SEC group list does not establish direct parent.


def test_verified_sinomine_control_does_not_merge_the_two_legal_entities():
    child = normalizer.resolve_entity("中矿资源")
    parent = normalizer.resolve_entity("中色矿业集团有限公司")
    assert child.entity_id != parent.entity_id
    assert child.parent_entity_id == child.group_id == parent.entity_id
    group = next(g for g in normalizer._load_hierarchy_v2()["groups"] if g["entity_id"] == parent.entity_id)
    assert group["is_central_soe"] is False


@pytest.mark.parametrize("bare", ["中央研究院", "金融集团", "环保集团", "核电集团", "上海三菱", "石油工程建设公司", "贵州工程公司"])
def test_generic_contextual_names_cannot_establish_chinese_group(bare):
    result = normalizer.resolve_entity(bare)
    assert result.entity_id is None and result.group_id is None
    assert result.verification_status == normalizer.UNMATCHED_NEEDS_REVIEW


def test_verified_mitsubishi_parent_is_shanghai_mechanical_not_top_group():
    lift = normalizer.resolve_entity("上海三菱电梯有限公司")
    mechanical = normalizer.resolve_entity("上海机电")
    assert lift.parent_entity_id == mechanical.entity_id
    assert lift.group_id == mechanical.group_id
    assert lift.parent_entity_id != lift.group_id


def test_historical_group_is_not_asserted_after_unverified_reorganization():
    result = normalizer.resolve_entity("湖南云箭集团有限公司")
    assert result.entity_id
    assert result.group_id is None and result.parent_entity_id is None
    assert result.implementing == "湖南云箭集团有限公司"
    assert result.verification_status == normalizer.VERIFIED_CHINESE


def test_verified_state_company_chains_preserve_separate_legal_ids():
    for child_name, parent_name in (
        ("中电国际", "国家电力投资集团有限公司"),
        ("中建安装", "中国建筑股份有限公司"),
        ("西电国际", "中国西电电气股份有限公司"),
        ("中国机械进出口（集团）有限公司", "通用技术集团国际控股有限公司"),
    ):
        child = normalizer.resolve_entity(child_name)
        parent = normalizer.resolve_entity(parent_name)
        assert child.parent_entity_id == parent.entity_id
        assert child.entity_id != parent.entity_id


@pytest.mark.parametrize("name", ["苏美达能源", "中能建葛洲坝工程科技有限公司"])
def test_verified_subject_replaces_old_unresolved_placeholder(name):
    result = normalizer.resolve_entity(name)
    assert result.entity_id and result.group_id


def test_multilevel_parent_inheritance_without_flat_parent_invention(monkeypatch):
    groups = [{"canonical_name_cn": "确认中国集团", "entity_id": "root", "is_chinese": True,
               "subsidiaries": [
                   {"name_cn": "海外控股公司", "entity_id": "middle", "parent_entity_id": "root"},
                   {"name_cn": "海外运营公司", "entity_id": "leaf", "parent_entity_id": "middle"},
                   {"name_cn": "已知集团未知直接母公司", "entity_id": "flat"},
               ]}]
    _use_groups(monkeypatch, groups)
    result = normalizer.resolve_entity("海外运营公司")
    assert result.verification_status == normalizer.VERIFIED_CHINESE
    assert result.parent_entity_id == "middle" and result.group_id == "root"
    assert normalizer.resolve_entity("已知集团未知直接母公司").parent_entity_id is None


def test_cross_group_alias_is_unresolved_in_both_orders(monkeypatch):
    groups = [{"canonical_name_cn": name, "aliases": ["共同简称"]} for name in ("甲公司", "乙公司")]
    for ordered in (groups, list(reversed(groups))):
        normalizer._hierarchy_index_v2.cache_clear()
        _use_groups(monkeypatch, ordered)
        result = normalizer.resolve_entity("共同简称")
        assert result.matched_source == "ambiguous"
        assert result.entity_id is None and result.group_id is None


def test_rename_keeps_persisted_id(monkeypatch):
    groups = [{"canonical_name_cn": "新法人名称", "entity_id": "persisted-id", "aliases": ["旧法人名称"]}]
    _use_groups(monkeypatch, groups)
    assert normalizer.resolve_entity("旧法人名称").entity_id == "persisted-id"
    assert normalizer.resolve_entity("新法人名称").entity_name == "新法人名称"


@pytest.mark.parametrize("parent", ["missing", "child"])
def test_invalid_parent_link_fails_with_context(monkeypatch, parent):
    _use_groups(monkeypatch, [{"canonical_name_cn": "集团", "entity_id": "root", "subsidiaries": [
        {"name_cn": "子公司", "entity_id": "child", "parent_entity_id": parent}
    ]}])
    with pytest.raises(ValueError, match="母公司不存在|存在环"):
        normalizer.resolve_entity("子公司")


def _builder():
    path = Path(__file__).resolve().parents[2] / "supporting_docs" / "build_hierarchy_v2.py"
    spec = importlib.util.spec_from_file_location("hierarchy_builder_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_builder_default_preserves_reviewed_additions_and_ids(tmp_path, monkeypatch):
    builder = _builder()
    target = tmp_path / "current.json"
    current = normalizer._load_hierarchy_v2()
    target.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
    before = target.read_bytes()
    monkeypatch.setattr(builder, "OUTPUT_FILE", target)
    builder.main([])
    assert target.read_bytes() == before
    assert normalizer.resolve_entity("中电国际").group_id
    assert normalizer.resolve_entity("三峡发展（马）公司").group_id


def test_builder_cannot_bootstrap_over_current_master(tmp_path, monkeypatch):
    builder = _builder()
    target = tmp_path / "current.json"
    target.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(builder, "OUTPUT_FILE", target)
    with pytest.raises(ValueError, match="禁止覆盖"):
        builder.main(["--bootstrap-output", str(target)])
    assert target.read_text(encoding="utf-8") == "{}"


def test_current_cwe_group_link_is_not_historical_three_gorges_alias():
    result = normalizer.resolve_entity("中水对外")
    assert result.entity_name == "中国水利电力对外有限公司"
    assert result.implementing == "中国交通建设集团有限公司"
    assert result.entity_id != result.group_id


def test_group_membership_can_inherit_through_group_parent_chain(monkeypatch):
    groups = [
        {"canonical_name_cn": "已确认中企", "entity_id": "root", "is_chinese": True},
        {"canonical_name_cn": "境外控股集团", "entity_id": "middle", "is_chinese": None,
         "parent_entity_id": "root", "subsidiaries": [
             {"name_cn": "境外第三层公司", "entity_id": "leaf"}
         ]},
    ]
    _use_groups(monkeypatch, groups)
    result = normalizer.resolve_entity("境外第三层公司")
    assert result.verification_status == normalizer.VERIFIED_CHINESE
    assert result.parent_entity_id is None
    assert result.group_id == "middle"
