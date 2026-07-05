"""测试 codex_review/schemas.py 中 verdict 合法/非法样例及 JSON round-trip。"""
import json

import pytest
from pydantic import ValidationError

from codex_review.schemas import (
    EventContext,
    ProjectContext,
    T1QueueFile,
    T1QueueItem,
    T1Verdict,
    T2QueueFile,
    T2QueueItem,
    T2Verdict,
    T3QueueFile,
    T3QueueItem,
    T3Verdict,
)

# ──────────────────────────────────────────────
# 辅助工厂
# ──────────────────────────────────────────────


def _event(**kw):
    """构造最小 EventContext。"""
    base = {"event_id": "E001", "project_stage": "construction"}
    base.update(kw)
    return base


def _project(**kw):
    """构造最小 ProjectContext。"""
    base = {
        "canonical_id": "IND-PV-0001",
        "project_name": "测试项目",
        "country": "印度",
        "category": "PV",
        "events": [_event()],
    }
    base.update(kw)
    return base


def _t1_verdict(**kw):
    """构造合法 T1Verdict 基础字段。"""
    base = {
        "pair_id": "p1",
        "same_project": True,
        "confidence": "high",
        "evidence_quote": "两份报告均指向同一 250MW 项目",
        "survivor_id": "IND-PV-0001",
        "reason": "地点、容量、业主完全一致",
    }
    base.update(kw)
    return base


def _t2_verdict(**kw):
    """构造合法 T2Verdict 基础字段。"""
    base = {
        "project_id": "IND-PV-0001",
        "action": "keep",
        "reason": "确为可再生能源项目",
        "evidence_quote": "项目描述明确写明太阳能发电",
    }
    base.update(kw)
    return base


def _t3_verdict(**kw):
    """构造合法 T3Verdict 基础字段（new_group 分类）。"""
    base = {
        "raw_name": "中国电建集团",
        "classification": "new_group",
        "canonical_group_cn": "中国电力建设集团有限公司",
        "source_urls": ["https://example.com/source1"],
        "confidence": "high",
        "notes": "官网确认",
    }
    base.update(kw)
    return base


# ──────────────────────────────────────────────
# T1Verdict 测试
# ──────────────────────────────────────────────


def test_t1_verdict_valid_same_project():
    """合法 T1Verdict：same_project=True，survivor_id 已填。"""
    v = T1Verdict(**_t1_verdict())
    assert v.same_project is True
    assert v.survivor_id == "IND-PV-0001"


def test_t1_verdict_valid_diff_project():
    """合法 T1Verdict：same_project=False，survivor_id=None。"""
    v = T1Verdict(**_t1_verdict(same_project=False, survivor_id=None))
    assert v.same_project is False
    assert v.survivor_id is None


def test_t1_verdict_invalid_same_true_no_survivor():
    """非法：same_project=True 但 survivor_id=None。"""
    with pytest.raises(ValidationError):
        T1Verdict(**_t1_verdict(survivor_id=None))


def test_t1_verdict_invalid_same_false_with_survivor():
    """非法：same_project=False 但 survivor_id 有值。"""
    with pytest.raises(ValidationError):
        T1Verdict(**_t1_verdict(same_project=False, survivor_id="IND-PV-0001"))


def test_t1_verdict_empty_evidence_quote():
    """非法：evidence_quote 为空串。"""
    with pytest.raises(ValidationError):
        T1Verdict(**_t1_verdict(evidence_quote=""))


def test_t1_verdict_whitespace_evidence_quote():
    """非法：evidence_quote 为纯空白。"""
    with pytest.raises(ValidationError):
        T1Verdict(**_t1_verdict(evidence_quote="   "))


def test_t1_verdict_extra_field_rejected():
    """非法：extra 字段注入被 extra=forbid 拦截。"""
    with pytest.raises(ValidationError):
        T1Verdict(**_t1_verdict(unknown_field="injected"))


# ──────────────────────────────────────────────
# T2Verdict 测试
# ──────────────────────────────────────────────


def test_t2_verdict_valid():
    """合法 T2Verdict：action=keep。"""
    v = T2Verdict(**_t2_verdict())
    assert v.action == "keep"


def test_t2_verdict_invalid_action():
    """非法：action 枚举值不存在。"""
    with pytest.raises(ValidationError):
        T2Verdict(**_t2_verdict(action="approve"))


def test_t2_verdict_empty_reason():
    """非法：reason 为空串。"""
    with pytest.raises(ValidationError):
        T2Verdict(**_t2_verdict(reason=""))


def test_t2_verdict_whitespace_evidence():
    """非法：evidence_quote 为纯空白。"""
    with pytest.raises(ValidationError):
        T2Verdict(**_t2_verdict(evidence_quote="  "))


def test_t2_verdict_extra_field_rejected():
    """非法：extra 字段注入。"""
    with pytest.raises(ValidationError):
        T2Verdict(**_t2_verdict(extra_col="x"))


# ──────────────────────────────────────────────
# T3Verdict 测试
# ──────────────────────────────────────────────


def test_t3_verdict_valid_new_group():
    """合法 T3Verdict：new_group 分类，canonical_group_cn 和 source_urls 已填。"""
    v = T3Verdict(**_t3_verdict())
    assert v.classification == "new_group"


def test_t3_verdict_valid_not_chinese():
    """合法 T3Verdict：not_chinese 分类，不强制 canonical_group_cn。"""
    v = T3Verdict(
        raw_name="Adani Green",
        classification="not_chinese",
        source_urls=[],
        confidence="high",
        notes="印度企业",
    )
    assert v.classification == "not_chinese"


def test_t3_verdict_new_group_no_source_urls():
    """非法：new_group 分类但 source_urls 为空列表。"""
    with pytest.raises(ValidationError):
        T3Verdict(**_t3_verdict(source_urls=[]))


def test_t3_verdict_alias_no_canonical_group():
    """非法：alias_of_existing 分类但 canonical_group_cn=None。"""
    with pytest.raises(ValidationError):
        T3Verdict(
            raw_name="电建",
            classification="alias_of_existing",
            canonical_group_cn=None,
            source_urls=["https://example.com"],
            confidence="medium",
            notes="",
        )


def test_t3_verdict_extra_field_rejected():
    """非法：extra 字段注入。"""
    with pytest.raises(ValidationError):
        T3Verdict(**_t3_verdict(injected="bad"))


# ──────────────────────────────────────────────
# QueueFile quarter 格式校验
# ──────────────────────────────────────────────


def test_queue_file_valid_quarter():
    """合法 quarter 格式：2026Q3。"""
    f = T1QueueFile(
        task="T1",
        quarter="2026Q3",
        generated_at="2026-07-06T00:00:00",
        items=[],
    )
    assert f.quarter == "2026Q3"


def test_queue_file_invalid_quarter_q5():
    """非法：2026Q5 超出范围。"""
    with pytest.raises(ValidationError):
        T1QueueFile(task="T1", quarter="2026Q5", generated_at="2026-07-06", items=[])


def test_queue_file_invalid_quarter_short_year():
    """非法：26Q1 年份不足 4 位。"""
    with pytest.raises(ValidationError):
        T2QueueFile(task="T2", quarter="26Q1", generated_at="2026-07-06", items=[])


def test_t3_queue_file_valid_quarter():
    """T3QueueFile 合法 quarter 格式。"""
    f = T3QueueFile(
        task="T3",
        quarter="2025Q4",
        generated_at="2025-10-01",
        items=[],
    )
    assert f.quarter == "2025Q4"


# ──────────────────────────────────────────────
# JSON round-trip 测试
# ──────────────────────────────────────────────


def test_t1_verdict_json_roundtrip():
    """T1Verdict dump 后重新 load 校验通过。"""
    v = T1Verdict(**_t1_verdict())
    dumped = v.model_dump_json()
    v2 = T1Verdict.model_validate_json(dumped)
    assert v2.pair_id == v.pair_id
    assert v2.survivor_id == v.survivor_id


def test_t2_verdict_json_roundtrip():
    """T2Verdict dump 后重新 load 校验通过。"""
    v = T2Verdict(**_t2_verdict())
    dumped = v.model_dump_json()
    v2 = T2Verdict.model_validate_json(dumped)
    assert v2.action == v.action


def test_t3_verdict_json_roundtrip():
    """T3Verdict dump 后重新 load 校验通过。"""
    v = T3Verdict(**_t3_verdict())
    dumped = v.model_dump_json()
    v2 = T3Verdict.model_validate_json(dumped)
    assert v2.canonical_group_cn == v.canonical_group_cn


def test_t1_queue_file_json_roundtrip():
    """T1QueueFile 含 item 的 JSON round-trip。"""
    item = T1QueueItem(
        pair_id="p1",
        similarity=0.92,
        both_sides_visible=True,
        a=ProjectContext(**_project()),
        b=ProjectContext(**_project(canonical_id="IND-PV-0002", project_name="另一项目")),
    )
    f = T1QueueFile(
        task="T1",
        quarter="2026Q3",
        generated_at="2026-07-06T00:00:00",
        items=[item],
    )
    dumped = f.model_dump_json()
    f2 = T1QueueFile.model_validate_json(dumped)
    assert len(f2.items) == 1
    assert f2.items[0].pair_id == "p1"
