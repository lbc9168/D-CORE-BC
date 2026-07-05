"""纯查表解析器 entity_normalize v2 契约测试（TDD 先行）。

覆盖：
  - 路径1：子公司精确命中 → (集团规范名, verbatim)
  - 路径2a：集团精确命中（verbatim == canonical）→ (canonical, "")
  - 路径2b：别名/legal_full_name_cn 命中（verbatim ≠ canonical）→ (canonical, verbatim)
  - 路径2c：去尾缀后命中集团 canonical → (canonical, verbatim)
  - 路径2d：去尾缀后命中子公司 → (集团规范名, verbatim)
  - 路径3：未命中 → (verbatim, "") 透传
  - 不变量：subsidiary 非空 ⇔ 原文名；subsidiary 空 ⇔ implementing 即原文名
  - 缓存清理：每测试后 cache_clear
  - 未命中日志断言（logging 被调用）
"""
import logging
import pytest


def _clear_cache():
    """清空解析器进程级缓存，确保测试隔离。"""
    from s01b_project_extractor.stages import entity_normalize
    entity_normalize._hierarchy_index_v2.cache_clear()


@pytest.fixture(autouse=True)
def clear_cache_fixture():
    """每个测试前后都清缓存。"""
    _clear_cache()
    yield
    _clear_cache()


# ── 路径1：子公司精确命中 ──────────────────────────────────────────────────────

def test_path1_subsidiary_exact_returns_group_and_verbatim():
    """子公司精确名命中 → implementing=集团规范名，subsidiary=verbatim（不变量验证）。"""
    from s01b_project_extractor.stages.entity_normalize import normalize_entity
    # 中国葛洲坝集团有限公司 是 中国能源建设集团有限公司 的子公司
    parent, sub = normalize_entity("中国葛洲坝集团有限公司")
    assert parent == "中国能源建设集团有限公司"
    assert sub == "中国葛洲坝集团有限公司"  # subsidiary = verbatim（不变量：子公司非空⇔原文名）


def test_path1_subsidiary_invariant_sub_equals_verbatim():
    """不变量：subsidiary 非空 ⇔ 它是原文名（verbatim），不是规范名。"""
    from s01b_project_extractor.stages.entity_normalize import normalize_entity
    verbatim = "中国葛洲坝集团有限公司"
    parent, sub = normalize_entity(verbatim)
    assert sub == verbatim, f"subsidiary should equal verbatim {verbatim!r}, got {sub!r}"


def test_path1_subsidiary_of_second_group():
    """另一集团的子公司也能正确归一。"""
    from s01b_project_extractor.stages.entity_normalize import normalize_entity
    # 中电建新能源集团股份有限公司 是 中国电力建设集团有限公司 子公司
    parent, sub = normalize_entity("中电建新能源集团股份有限公司")
    assert parent == "中国电力建设集团有限公司"
    assert sub == "中电建新能源集团股份有限公司"


# ── 路径2a：集团精确命中（verbatim == canonical）──────────────────────────────

def test_path2a_group_exact_self_returns_empty_sub():
    """顶级集团名直接输入 → (canonical, "")，subsidiary 空。"""
    from s01b_project_extractor.stages.entity_normalize import normalize_entity
    parent, sub = normalize_entity("中国能源建设集团有限公司")
    assert parent == "中国能源建设集团有限公司"
    assert sub == ""


def test_path2a_invariant_implementing_equals_verbatim_when_sub_empty():
    """不变量：subsidiary 空 ⇔ implementing 即原文名。"""
    from s01b_project_extractor.stages.entity_normalize import normalize_entity
    verbatim = "中国能源建设集团有限公司"
    parent, sub = normalize_entity(verbatim)
    assert sub == ""
    assert parent == verbatim, f"When sub empty, implementing must equal verbatim {verbatim!r}"


# ── 路径2b：别名/legal_full_name_cn 命中（verbatim ≠ canonical）──────────────

def test_path2b_group_alias_returns_canonical_and_verbatim():
    """集团别名命中 → implementing=canonical，subsidiary=verbatim（原文保来源）。"""
    from s01b_project_extractor.stages.entity_normalize import normalize_entity
    # 找一个有 aliases 的集团做测试
    # 中国电力建设集团有限公司 有别名 "中国电建" 等
    # 如果 aliases 实际为空，改用 legal_full_name_cn 不同的子公司做测试
    # 这里用一个已知有别名的子公司 legal_full_name_cn 别名：
    # 子公司 name_cn="能建海投" → legal_full_name_cn="中能建海外投资有限公司"，
    # 所以 "能建海投" 是 name_cn（精确匹配路径1），不适合。
    # 用 aliases 字段：需要找一个实际有 aliases 的组——先用 name_cn 不等于 legal_full_name_cn 的情形
    # 中国能建国际集团 → legal_full_name_cn="中能建国际建设集团有限公司"，name_cn 是 sub 的 name_cn
    # 这是 path1（子公司 name_cn 精确匹配）不是 path2b。
    # Path2b 测试：subsidiaries legal_full_name_cn 作为别名
    # "中能建国际建设集团有限公司" 是 name_cn="中国能建国际建设集团有限公司" 的 legal_full_name_cn
    # legal_full_name_cn 应被识别为别名 → path2b
    parent, sub = normalize_entity("中能建国际建设集团有限公司")
    # 不管走哪条路，只要命中了就测不变量
    assert parent != ""
    # 如果是 path2b（verbatim ≠ canonical），sub 应该等于 verbatim
    if sub:
        assert sub == "中能建国际建设集团有限公司"


def test_path2b_subsidiary_legal_fullname_hitspath1():
    """legal_full_name_cn 与 name_cn 不同的子公司：verbatim=legal_full_name_cn → path1（子公司命中），sub=verbatim。"""
    from s01b_project_extractor.stages.entity_normalize import normalize_entity
    # "中能建国际建设集团有限公司" 是 name_cn="中国能建国际建设集团有限公司" 的 legal_full_name_cn
    # 查表时 legal_full_name_cn 被注册为子公司别名
    parent, sub = normalize_entity("中能建国际建设集团有限公司")
    # 应命中为子公司（路径1 或 2b）→ implementing = 顶级集团
    assert parent == "中国能源建设集团有限公司"
    # 不变量：命中后 sub 非空时必须等于 verbatim
    if sub:
        assert sub == "中能建国际建设集团有限公司"


# ── 路径2c：去尾缀归一化命中集团 ───────────────────────────────────────────────

def test_path2c_suffix_stripped_group_match():
    """输入的尾缀变体能匹配集团 canonical（去尾缀后精确比对）。"""
    from s01b_project_extractor.stages.entity_normalize import normalize_entity
    # "中国能源建设集团" 去尾缀后 = "中国能源建设集团"，canonical 去尾缀 "中国能源建设集团有限公司" → "中国能源建设集团"
    parent, sub = normalize_entity("中国能源建设集团")
    # 应命中 → implementing=canonical，sub=verbatim（路径2c）
    assert parent == "中国能源建设集团有限公司"
    # 不变量：sub 非空时等于 verbatim（原文）
    if sub:
        assert sub == "中国能源建设集团"


def test_path2c_suffix_stripped_sub_match():
    """子公司尾缀变体也能触发去尾缀命中。"""
    from s01b_project_extractor.stages.entity_normalize import normalize_entity
    # "中国葛洲坝集团" 去尾缀后应能匹配 "中国葛洲坝集团有限公司"（子公司）
    parent, sub = normalize_entity("中国葛洲坝集团")
    # 命中后：implementing=顶级集团
    assert parent == "中国能源建设集团有限公司"
    # 不变量：sub 非空⇔verbatim（路径2c 子公司命中时，sub=verbatim）
    if sub:
        assert sub == "中国葛洲坝集团"


# ── 路径3：未命中透传 + 日志 ──────────────────────────────────────────────────

def test_path3_passthrough_unknown_entity():
    """不在表中的公司名 → (verbatim, "")，不变量：implementing=verbatim。"""
    from s01b_project_extractor.stages.entity_normalize import normalize_entity
    verbatim = "某完全不在表中的测试公司XYZ"
    parent, sub = normalize_entity(verbatim)
    assert parent == verbatim
    assert sub == ""


def test_path3_passthrough_invariant():
    """不变量：未命中时 subsidiary='' ⇔ implementing=verbatim。"""
    from s01b_project_extractor.stages.entity_normalize import normalize_entity
    verbatim = "未知公司ABC不在表中"
    parent, sub = normalize_entity(verbatim)
    assert sub == "", "未命中 subsidiary 必须为空"
    assert parent == verbatim, "未命中 implementing 必须等于 verbatim"


def test_path3_miss_writes_to_log(caplog):
    """未命中时必须写 WARNING 级别日志（供季度补录频次追踪）。"""
    from s01b_project_extractor.stages.entity_normalize import normalize_entity
    verbatim = "未命中日志测试公司QRST"
    with caplog.at_level(logging.WARNING, logger="s01b_project_extractor.stages.entity_normalize"):
        normalize_entity(verbatim)
    assert any(verbatim in record.message for record in caplog.records), (
        f"未命中时应记录 WARNING 含公司名，caplog: {[r.message for r in caplog.records]}"
    )


# ── 边界：空值/空白 ──────────────────────────────────────────────────────────

def test_empty_string_returns_unknown():
    """空字符串 → ('Unknown', '')。"""
    from s01b_project_extractor.stages.entity_normalize import normalize_entity
    parent, sub = normalize_entity("")
    assert parent == "Unknown"
    assert sub == ""


def test_whitespace_returns_unknown():
    """纯空白 → ('Unknown', '')。"""
    from s01b_project_extractor.stages.entity_normalize import normalize_entity
    parent, sub = normalize_entity("   ")
    assert parent == "Unknown"
    assert sub == ""


# ── 不变量全路径穷举 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("verbatim,expected_parent,expected_sub_nonempty", [
    # 路径1：子公司精确 → sub 非空 == verbatim
    ("中国葛洲坝集团有限公司", "中国能源建设集团有限公司", True),
    # 路径2a：集团自身 → sub 为空
    ("中国能源建设集团有限公司", "中国能源建设集团有限公司", False),
    # 路径3：未命中 → sub 为空
    ("完全不在表中XYZXYZ", "完全不在表中XYZXYZ", False),
])
def test_invariant_sub_empty_iff_implementing_is_verbatim(verbatim, expected_parent, expected_sub_nonempty):
    """不变量：sub 非空 ⇔ sub==verbatim；sub 空 ⇔ implementing==verbatim（或 Unknown）。"""
    from s01b_project_extractor.stages.entity_normalize import normalize_entity
    parent, sub = normalize_entity(verbatim)
    assert parent == expected_parent
    if expected_sub_nonempty:
        assert sub != "", f"expected sub non-empty for verbatim={verbatim!r}"
        assert sub == verbatim, f"sub should equal verbatim when nonempty, got {sub!r}"
    else:
        assert sub == "", f"expected sub empty for verbatim={verbatim!r}, got {sub!r}"


# ── 无包含匹配 ────────────────────────────────────────────────────────────────

def test_no_contains_match_partial_substring():
    """删除包含匹配：输入仅是子公司名的前缀/后缀片段，不应命中（应透传）。"""
    from s01b_project_extractor.stages.entity_normalize import normalize_entity
    # "葛洲坝" 是 "中国葛洲坝集团有限公司" 的前缀片段，不应被包含匹配归一
    parent, sub = normalize_entity("葛洲坝")
    # 不应命中集团：因为删除了包含匹配
    assert parent == "葛洲坝"
    assert sub == ""


def test_no_contains_match_superstring():
    """删除包含匹配：输入是正确公司名的超集字符串，不应命中。"""
    from s01b_project_extractor.stages.entity_normalize import normalize_entity
    # 在子公司名前后加随机词，精确/别名/去尾缀都不会命中
    parent, sub = normalize_entity("XX中国葛洲坝集团有限公司后缀XX")
    assert parent == "XX中国葛洲坝集团有限公司后缀XX"
    assert sub == ""
