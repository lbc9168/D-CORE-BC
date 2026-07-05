"""测试 codex_review/ledger.py：key 计算、record/save/load、原子性、损坏 JSON。"""
import json

import pytest

from codex_review.ledger import (
    is_settled,
    load_ledger,
    record,
    save_ledger,
    t1_key,
    t2_key,
    t3_key,
)
from codex_review.schemas import ProjectContext


# ──────────────────────────────────────────────
# 辅助工厂
# ──────────────────────────────────────────────


def _make_project(canonical_id: str = "IND-PV-0001") -> ProjectContext:
    """构造最小 ProjectContext，用于 t1_key 测试。"""
    return ProjectContext(
        canonical_id=canonical_id,
        project_name="测试项目",
        country="印度",
        country_iso="IND",
        category="PV",
        events=[{"event_id": "E001", "project_stage": "construction"}],
    )


# ──────────────────────────────────────────────
# key 计算测试
# ──────────────────────────────────────────────


def test_t1_key_symmetry():
    """t1_key(a, b) == t1_key(b, a)：对称性，交换 a/b 结果不变。"""
    a = _make_project("IND-PV-0001")
    b = _make_project("IND-PV-0002")
    assert t1_key(a, b) == t1_key(b, a)


def test_t1_key_same_project_same_key():
    """同一对项目，多次调用 key 一致。"""
    a = _make_project("IND-PV-0001")
    b = _make_project("IND-PV-0002")
    assert t1_key(a, b) == t1_key(a, b)


def test_t2_key_deterministic():
    """相同 project_id + trigger 每次产生相同 key。"""
    k1 = t2_key("IND-PV-0001", "fixed_list")
    k2 = t2_key("IND-PV-0001", "fixed_list")
    assert k1 == k2


def test_t2_key_different_trigger():
    """不同 trigger 产生不同 key。"""
    assert t2_key("IND-PV-0001", "fixed_list") != t2_key(
        "IND-PV-0001", "no_chinese_entity_scan"
    )


def test_t3_key_deterministic():
    """相同 raw_name 每次产生相同 key。"""
    assert t3_key("中国电建集团") == t3_key("中国电建集团")


def test_t3_key_different_names():
    """不同 raw_name 产生不同 key。"""
    assert t3_key("中国电建集团") != t3_key("中国电力建设集团有限公司")


# ──────────────────────────────────────────────
# load_ledger 测试
# ──────────────────────────────────────────────


def test_load_ledger_nonexistent(tmp_path):
    """文件不存在时返回空台账，不抛异常。"""
    p = tmp_path / "ledger.json"
    ledger = load_ledger(p)
    assert ledger["entries"] == {}
    assert ledger["schema_version"] == 1


def test_load_ledger_corrupt_json(tmp_path):
    """损坏 JSON 文件抛 ValueError，不静默重建。"""
    p = tmp_path / "ledger.json"
    p.write_text("{broken json", encoding="utf-8")
    with pytest.raises(ValueError, match="损坏"):
        load_ledger(p)


def test_load_ledger_missing_entries_key(tmp_path):
    """台账缺 'entries' 字段时抛 ValueError。"""
    p = tmp_path / "ledger.json"
    p.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="entries"):
        load_ledger(p)


# ──────────────────────────────────────────────
# record → save → load → is_settled 完整流程
# ──────────────────────────────────────────────


def test_record_save_load_is_settled(tmp_path):
    """record 后 save，重新 load，is_settled 应为 True。"""
    p = tmp_path / "ledger.json"
    ledger = load_ledger(p)
    key = t3_key("中国电建")
    record(
        ledger,
        key,
        task="T3",
        quarter="2026Q3",
        item_id="item-001",
        status="accepted_no_change",
        verdict_digest="abc123",
    )
    save_ledger(p, ledger)

    ledger2 = load_ledger(p)
    assert is_settled(ledger2, key)


def test_unsettled_key_is_false(tmp_path):
    """未 record 的 key，is_settled 应为 False。"""
    p = tmp_path / "ledger.json"
    ledger = load_ledger(p)
    key = t3_key("某个未判定实体")
    assert not is_settled(ledger, key)


def test_record_does_not_affect_other_keys(tmp_path):
    """record 一个 key 后，另一个 key 仍是 unsettled。"""
    p = tmp_path / "ledger.json"
    ledger = load_ledger(p)
    key_a = t2_key("IND-PV-0001", "fixed_list")
    key_b = t2_key("IND-PV-0002", "fixed_list")
    record(
        ledger,
        key_a,
        task="T2",
        quarter="2026Q3",
        item_id="item-a",
        status="applied",
        verdict_digest="deadbeef",
    )
    save_ledger(p, ledger)

    ledger2 = load_ledger(p)
    assert is_settled(ledger2, key_a)
    assert not is_settled(ledger2, key_b)


# ──────────────────────────────────────────────
# 原子写测试
# ──────────────────────────────────────────────


def test_save_atomic_readable(tmp_path):
    """save_ledger 后文件立即可被 load_ledger 正常读取。"""
    p = tmp_path / "ledger.json"
    ledger = {"schema_version": 1, "entries": {"k": {"task": "T1"}}}
    save_ledger(p, ledger)
    loaded = load_ledger(p)
    assert "k" in loaded["entries"]


def test_save_overwrites_existing(tmp_path):
    """多次 save 后，只保留最新状态。"""
    p = tmp_path / "ledger.json"
    ledger = load_ledger(p)
    key = t3_key("实体A")
    record(
        ledger,
        key,
        task="T3",
        quarter="2026Q3",
        item_id="i1",
        status="accepted_no_change",
        verdict_digest="v1",
    )
    save_ledger(p, ledger)

    # 修改状态再次保存
    ledger["entries"][key]["status"] = "applied"
    save_ledger(p, ledger)

    loaded = load_ledger(p)
    assert loaded["entries"][key]["status"] == "applied"


# ──────────────────────────────────────────────
# dict 输入的 t1_key
# ──────────────────────────────────────────────


def test_t1_key_accepts_dict():
    """t1_key 接受 dict 输入（兼容未 parse 的原始数据）。"""
    a = {
        "canonical_id": "IND-PV-0001",
        "project_name": "测试项目",
        "country_iso": "IND",
        "category": "PV",
    }
    b = {
        "canonical_id": "IND-PV-0002",
        "project_name": "另一项目",
        "country_iso": "IND",
        "category": "PV",
    }
    k1 = t1_key(a, b)
    k2 = t1_key(b, a)
    assert k1 == k2
    assert isinstance(k1, str) and len(k1) == 40  # sha1 hex
