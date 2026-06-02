"""测试 _normalize_record_type：非法/空 record_type 兜底归一为「具体项目」，合法三类原样。"""
from s01b_project_extractor.tools.project_extractor_tool import _normalize_record_type
from s01b_project_extractor.guardrails.format_checks import VALID_RECORD_TYPES


def test_valid_record_types_passthrough():
    """合法三类原样返回。"""
    for rt in ("具体项目", "设备订单", "一般合作"):
        assert _normalize_record_type(rt) == rt


def test_category_leaked_as_record_type_coerced():
    """类别值（如新能源制造业）被误填进 Record Type → 归「具体项目」。"""
    assert _normalize_record_type("新能源制造业") == "具体项目"
    assert _normalize_record_type("输变电建设") == "具体项目"


def test_event_word_as_record_type_coerced():
    """事件词（中标/收购/签约）被误填 → 归「具体项目」。"""
    for ev in ("中标", "收购", "签约", "开工"):
        assert _normalize_record_type(ev) == "具体项目"


def test_empty_or_none_coerced():
    """空/None/空白 → 归「具体项目」（与原有缺省行为一致）。"""
    assert _normalize_record_type("") == "具体项目"
    assert _normalize_record_type(None) == "具体项目"
    assert _normalize_record_type("   ") == "具体项目"


def test_whitespace_trimmed_on_valid():
    """合法值前后空白被裁剪后仍判为合法。"""
    assert _normalize_record_type(" 设备订单 ") == "设备订单"


def test_normalizer_output_always_valid():
    """归一化结果必落在 format_checks 的合法集内（守护两边一致）。"""
    for v in ("具体项目", "设备订单", "一般合作", "新能源制造业", "中标", "", None, "xyz"):
        assert _normalize_record_type(v) in VALID_RECORD_TYPES
