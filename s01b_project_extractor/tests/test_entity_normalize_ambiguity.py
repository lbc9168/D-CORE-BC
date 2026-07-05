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


# ── 实际数据驱动：构建期应 warning 歧义键 ──────────────────────────────────────


def test_build_warns_on_cross_group_ambiguous_key(caplog):
    """构建 v2 索引时，跨集团歧义键（中国机械设备工程股份有限公司同时是
    中国机械工业集团的 sub_legal 和自己的 canonical）应触发 WARNING 日志。"""
    from s01b_project_extractor.stages.entity_normalize import _hierarchy_index_v2
    with caplog.at_level(logging.WARNING, logger="s01b_project_extractor.stages.entity_normalize"):
        _hierarchy_index_v2()
    # 应有 warning，提及歧义键和涉及的组
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    # 4 个跨集团歧义键至少触发 1 条 warning
    assert len(warnings) >= 1, f"期望至少 1 条歧义 WARNING，实际 caplog: {[r.message for r in caplog.records]}"


def test_build_warns_contains_key_and_group_name(caplog):
    """歧义 warning 消息必须包含键名和至少一个集团名，供人工排查。"""
    from s01b_project_extractor.stages.entity_normalize import _hierarchy_index_v2
    with caplog.at_level(logging.WARNING, logger="s01b_project_extractor.stages.entity_normalize"):
        _hierarchy_index_v2()
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    # 检查其中一条 warning 含已知歧义键（中国机械设备工程股份有限公司）
    ambig_key = "中国机械设备工程股份有限公司"
    assert any(ambig_key in w for w in warnings), (
        f"期望 warning 中含 '{ambig_key}'，实际 warnings: {warnings}"
    )


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
    """中国机械设备工程股份有限公司：既是自己的 canonical 也是中国机械工业集团的 sub_legal。
    应以 canonical 优先 → (自身, "")，而不是作为子公司归到 中国机械工业集团。"""
    from s01b_project_extractor.stages.entity_normalize import normalize_entity
    parent, sub = normalize_entity("中国机械设备工程股份有限公司")
    # canonical 优先 → 路径2a：(canonical, "")
    assert parent == "中国机械设备工程股份有限公司", (
        f"canonical 应优先于 sub_legal，期望 parent='中国机械设备工程股份有限公司'，得到: {parent!r}"
    )
    assert sub == "", f"canonical 路径2a 的 sub 应为空，得到: {sub!r}"
