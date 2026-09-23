"""company_lookup 三层查表引擎测试。"""
import json
import pytest
from s02b_entity_normalizer.company_lookup import build_index, lookup

FIXTURE_REGISTRY = {
    "_meta": {"version": 1, "generated": "2026-01-01", "source": "test"},
    "companies": [
        {"canonical_cn": "阿布扎比未来能源公司", "canonical_en": "Masdar",
         "aliases": ["Masdar", "马斯达尔"], "is_chinese": False, "parent_cn": None, "source": "t"},
        {"canonical_cn": "Recurrent Energy", "canonical_en": "Recurrent Energy",
         "aliases": [], "is_chinese": True, "parent_cn": "阿特斯阳光电力集团股份有限公司", "source": "t"},
    ],
}
FIXTURE_HIERARCHY = {
    "schema_version": 2, "generated_on": "2026-01-01", "notes": "", "groups": [
        {"canonical_name_cn": "中国电力建设集团有限公司", "is_central_soe": True,
         "aliases": ["中国电建"], "subsidiaries": [
            {"name_cn": "中国电建集团海外投资有限公司",
             "legal_full_name_cn": "中国电建集团海外投资有限公司",
             "aliases": ["中国能源海外投资有限公司", "电建海投"]}]},
    ],
}


@pytest.fixture()
def index(tmp_path):
    rp = tmp_path / "reg.json"; rp.write_text(json.dumps(FIXTURE_REGISTRY, ensure_ascii=False), encoding="utf-8")
    hp = tmp_path / "hier.json"; hp.write_text(json.dumps(FIXTURE_HIERARCHY, ensure_ascii=False), encoding="utf-8")
    return build_index(str(rp), str(hp))


def test_registry_exact_and_alias_cross_language(index):
    for name in ["阿布扎比未来能源公司", "Masdar", "马斯达尔"]:
        r = lookup(name, index)
        assert (r.canonical_cn, r.canonical_en, r.source) == ("阿布扎比未来能源公司", "Masdar", "registry")


def test_hierarchy_alias_maps_to_subsidiary_not_parent(index):
    r = lookup("中国能源海外投资有限公司", index)
    assert r.canonical_cn == "中国电建集团海外投资有限公司"   # 讹变→规范子公司名
    assert r.canonical_cn != "中国电力建设集团有限公司"        # 绝不上卷母公司
    assert r.source == "hierarchy" and r.canonical_en is None  # hierarchy 无 EN，交缓存


def test_hierarchy_group_alias_maps_to_group_itself(index):
    r = lookup("中国电建", index)
    assert r.canonical_cn == "中国电力建设集团有限公司" and r.source == "hierarchy"


def test_passthrough_unknown(index):
    r = lookup("某某不存在公司", index)
    assert r.matched is False and r.canonical_cn == "某某不存在公司" and r.source == "passthrough"


def test_ascii_part_hits_registry_case_sensitive_exact(index):
    assert lookup("Recurrent Energy", index).canonical_en == "Recurrent Energy"


def test_alias_conflict_raises(tmp_path):
    bad = json.loads(json.dumps(FIXTURE_REGISTRY, ensure_ascii=False))
    bad["companies"].append({"canonical_cn": "另一家", "canonical_en": "X",
                             "aliases": ["Masdar"], "is_chinese": False, "parent_cn": None, "source": "t"})
    rp = tmp_path / "bad.json"; rp.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        build_index(str(rp), None)


def test_real_files_anchor():
    """真实文件锚点：Masdar 在册（Task1 bootstrap）；电建海投讹变在 hierarchy 可解。

    真实 hierarchy 里该讹变的实际挂法：groups[中国电力建设集团有限公司].subsidiaries 中
    有一条 name_cn="中国能源海外投资有限公司"（讹变本身就是条目名，非 aliases 字段），
    其 legal_full_name_cn="中国电建集团海外投资有限公司" 才是该子公司的规范名。
    """
    idx = build_index()  # 缺省路径=根目录真实文件
    assert lookup("Masdar", idx).canonical_cn == "阿布扎比未来能源公司"
    r = lookup("中国能源海外投资有限公司", idx)
    assert r.matched and "海外投资" in r.canonical_cn
    assert r.canonical_cn == "中国电建集团海外投资有限公司"
    assert r.canonical_cn != "中国电力建设集团有限公司"  # 绝不上卷母公司


def test_group_only_alias_does_not_promote_developer_to_parent(tmp_path):
    """A confirmed group link is not evidence that the group itself is developer."""
    import json
    from s02b_entity_normalizer.company_lookup import build_index, lookup
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"companies": []}), encoding="utf-8")
    hierarchy = tmp_path / "hierarchy.json"
    hierarchy.write_text(json.dumps({"groups": [{
        "canonical_name_cn": "中国集团有限公司",
        "aliases": ["海外品牌"], "group_only_aliases": ["海外品牌"],
        "subsidiaries": [{"name_cn": "子公司简称", "legal_full_name_cn": "海外子公司有限公司",
                          "aliases": ["Overseas Child"]}],
    }]}), encoding="utf-8")
    index = build_index(str(registry), str(hierarchy))
    result = lookup("海外品牌", index)
    assert not result.matched and result.canonical_cn == "海外品牌"
    assert lookup("子公司简称", index).canonical_cn == "海外子公司有限公司"
    assert lookup("overseas child", index).canonical_cn == "海外子公司有限公司"


def test_conflicting_legal_names_are_not_first_seen_wins(tmp_path):
    import json
    from s02b_entity_normalizer.company_lookup import build_index, lookup
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"companies": []}), encoding="utf-8")
    hierarchy = tmp_path / "hierarchy.json"
    hierarchy.write_text(json.dumps({"groups": [
        {"canonical_name_cn": "甲集团", "aliases": ["共用简称"]},
        {"canonical_name_cn": "乙集团", "aliases": ["共用简称"]},
    ]}), encoding="utf-8")
    result = lookup("共用简称", build_index(str(registry), str(hierarchy)))
    assert not result.matched and result.source == "ambiguous"


def test_verified_names_do_not_turn_generic_departments_into_developers():
    index = build_index()
    for name in ("核电集团", "上海三菱", "中央研究院", "金融集团", "石油工程建设公司", "贵州工程公司"):
        result = lookup(name, index)
        assert not result.matched and result.canonical_cn == name
    assert lookup("电气风电", index).canonical_cn == "上海电气风电集团股份有限公司"
    assert lookup("Recurrent Energy", index).canonical_cn == "Recurrent Energy"
    assert lookup("Recurrent Energy, LLC", index).canonical_cn == "Recurrent Energy, LLC"
