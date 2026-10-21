"""v2 索引构建期歧义检测测试（TDD #2）。

验证：
  - 同一键被多组声明 → logging.WARNING 含键名和组名
  - 去尾缀碰撞（不同变体命中不同层）→ logging.WARNING
  - 构建期优先级规则：canonical > sub > alias（跨集团歧义时使用显式优先级）
"""
import logging
import pytest


def _clear_cache():
    """清 v2 索引 lru_cache + 去尾缀预构索引，隔离测试。"""
    from s01b_project_extractor.stages import entity_normalize
    entity_normalize._hierarchy_index_v2.cache_clear()
    entity_normalize._stripped_index_cache = {}


@pytest.fixture(autouse=True)
def clear_cache_fixture():
    """每个测试前后都清缓存。"""
    _clear_cache()
    yield
    _clear_cache()


# ── 歧义检测逻辑（合成数据固化——真实登记册跨集团键已在数据侧清零，见下一测试）──


def _synthetic_ambiguous_groups() -> list[dict]:
    """构造跨集团歧义：'乙股份有限公司' 既是自己的 canonical，又被甲集团登记为 sub_legal。"""
    return [
        {"canonical_name_cn": "甲集团测试公司", "aliases": [], "subsidiaries": [
            {"name_cn": "乙股份简称", "legal_full_name_cn": "乙股份有限公司"},
        ]},
        {"canonical_name_cn": "乙股份有限公司", "aliases": [], "subsidiaries": []},
    ]


def test_build_warns_on_cross_group_ambiguous_key(caplog):
    """构建索引时，同一键被两个集团声明（canonical vs 他组 sub_legal）应触发 WARNING。
    （2026-07-06 终审 C2 起真实登记册已无此类键——build_hierarchy_v2.ADDITION_SUBS_TO_DROP
    数据侧消歧；此处用合成数据固化检测逻辑本身。）"""
    from s01b_project_extractor.stages.entity_normalize import _build_index_v2
    with caplog.at_level(logging.WARNING, logger="s01b_project_extractor.stages.entity_normalize"):
        index, _stripped = _build_index_v2(_synthetic_ambiguous_groups())
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("乙股份有限公司" in w for w in warnings), (
        f"期望跨集团歧义键触发 WARNING，实际: {warnings}"
    )
    # Contradictory group membership needs review, irrespective of key priority.
    assert "乙股份有限公司" not in index


def test_build_warns_contains_key_and_group_name(caplog):
    """歧义 warning 消息必须包含键名和冲突集团名，供人工排查。"""
    from s01b_project_extractor.stages.entity_normalize import _build_index_v2
    with caplog.at_level(logging.WARNING, logger="s01b_project_extractor.stages.entity_normalize"):
        _build_index_v2(_synthetic_ambiguous_groups())
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("乙股份有限公司" in w and "甲集团测试公司" in w for w in warnings), (
        f"期望 warning 同时含键名与冲突组名，实际 warnings: {warnings}"
    )


def test_real_registry_has_no_cross_group_ambiguity(caplog):
    """真实登记册构建索引不得再有跨集团歧义键 WARNING（2026-07-06 终审 C2 数据侧已清零）。"""
    from s01b_project_extractor.stages.entity_normalize import _hierarchy_index_v2
    with caplog.at_level(logging.WARNING, logger="s01b_project_extractor.stages.entity_normalize"):
        _hierarchy_index_v2()
    cross_group = [
        r.message for r in caplog.records
        if r.levelno == logging.WARNING and "名称歧义键" in r.message
    ]
    assert cross_group == [], f"真实登记册仍有跨集团歧义键: {cross_group}"


# ── 去尾缀预构索引（性能+碰撞检测）──────────────────────────────────────────────


def test_stripped_index_built_at_construct_time(caplog):
    """_hierarchy_index_v2 构建时应同时构建去尾缀索引（不在 normalize_entity 调用时线性扫描）。"""
    from s01b_project_extractor.stages import entity_normalize
    with caplog.at_level(logging.INFO, logger="s01b_project_extractor.stages.entity_normalize"):
        entity_normalize._hierarchy_index_v2()
    # 测试去尾缀索引可访问（函数返回后，_stripped_index 模块级变量应已填充）
    # 通过调用 normalize_entity 测试性能路径是否走预构索引（无线性扫描）
    from s01b_project_extractor.stages.entity_normalize import normalize_entity
    # 去尾缀路径：中国能源建设集团（去尾缀后 = 中国能源建设集团）命中 canonical
    parent, sub = normalize_entity("中国能源建设集团")
    assert parent == "中国能源建设集团有限公司"
    # sub 应为 verbatim（路径2c）
    assert sub == "中国能源建设集团"


def test_stripped_index_warns_on_collision(caplog):
    """哈尔滨电气/中国建筑：不同尾缀变体（canonical vs sub_name_cn）剥缀后碰撞，应触发 WARNING。"""
    from s01b_project_extractor.stages.entity_normalize import _hierarchy_index_v2
    with caplog.at_level(logging.WARNING, logger="s01b_project_extractor.stages.entity_normalize"):
        _hierarchy_index_v2()
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    # 哈尔滨电气：'哈尔滨电气集团有限公司'(canonical) vs '哈尔滨电气股份有限公司'(sub_name_cn)
    # 剥缀后均为 "哈尔滨电气"，应 warning
    assert any("哈尔滨电气" in w for w in warnings), (
        f"期望去尾缀碰撞 warning 含 '哈尔滨电气'，实际 warnings: {warnings}"
    )


# ── 优先级规则：canonical > sub/alias（跨集团歧义时用确定性优先级）──────────────


def test_canonical_priority_over_sub_legal():
    """中国机械设备工程股份有限公司：V4 已按股权层级挂到国机集团下。
    因此它不再作为独立 canonical 返回，而是 normalizer 的 (集团, 子公司)。"""
    from s01b_project_extractor.stages.entity_normalize import normalize_entity
    parent, sub = normalize_entity("中国机械设备工程股份有限公司")
    assert parent == "中国机械工业集团有限公司", (
        f"V4 中 CMEC 应归一到国机集团，得到: {parent!r}"
    )
    assert sub == "中国机械设备工程股份有限公司", (
        f"CMEC 应作为具体子公司保留，得到: {sub!r}"
    )
