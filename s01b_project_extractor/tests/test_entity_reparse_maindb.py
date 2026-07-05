"""TDD 测试：entity_reparse_maindb — 全库重解析回填 CLI 核心纯函数。

覆盖：
  - 源名恢复不变量（subsidiary 非空 → 用 subsidiary；否则用 implementing）
  - 重解析计划生成（新旧值对比、EN 置 NULL 标记）
  - 幂等性（已归位后重跑 plan 应得 0 变更）
  - dry-run 不写库
  - apply 先备份再批量 UPDATE，事后行数不变
  - 两行皆空（implementing+subsidiary 均空）跳过计数
"""
import sqlite3
from pathlib import Path

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_db(tmp_path, rows: list[tuple]) -> str:
    """建内存等价的临时 chinese_entities 表，rows 格式:
    (id, event_id, implementing_entity, implementing_entity_en,
     subsidiary_name, subsidiary_name_en, entity_en_quality, is_central_soe)
    """
    db = str(tmp_path / "test_entities.db")
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE chinese_entities (
            id INTEGER PRIMARY KEY,
            event_id TEXT NOT NULL,
            implementing_entity TEXT,
            implementing_entity_en TEXT,
            subsidiary_name TEXT,
            subsidiary_name_en TEXT,
            entity_en_quality TEXT DEFAULT 'ai_translated',
            involvement_type TEXT,
            subcontract_amount_raw TEXT,
            subcontract_amount REAL,
            is_central_soe INTEGER
        )
    """)
    conn.executemany(
        """INSERT INTO chinese_entities
           (id, event_id, implementing_entity, implementing_entity_en,
            subsidiary_name, subsidiary_name_en, entity_en_quality, is_central_soe)
           VALUES (?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    conn.close()
    return db


# ── Unit tests: 纯函数（不依赖 normalize_entity，用 mock）────────────────────

def test_source_name_recovery_subsidiary_nonempty():
    """subsidiary 非空 → source_name = subsidiary_name。"""
    from s01b_project_extractor.tools.entity_reparse_maindb import _source_name
    row = {"implementing_entity": "中国电力建设集团有限公司", "subsidiary_name": "中国电建集团海外投资有限公司"}
    assert _source_name(row) == "中国电建集团海外投资有限公司"


def test_source_name_recovery_subsidiary_empty():
    """subsidiary 为空 → source_name = implementing_entity。"""
    from s01b_project_extractor.tools.entity_reparse_maindb import _source_name
    row = {"implementing_entity": "中国能建国际集团", "subsidiary_name": ""}
    assert _source_name(row) == "中国能建国际集团"


def test_source_name_recovery_subsidiary_none():
    """subsidiary 为 None → source_name = implementing_entity。"""
    from s01b_project_extractor.tools.entity_reparse_maindb import _source_name
    row = {"implementing_entity": "某公司", "subsidiary_name": None}
    assert _source_name(row) == "某公司"


def test_source_name_both_empty():
    """implementing + subsidiary 均空/None → 返回空串（调用方跳过此行）。"""
    from s01b_project_extractor.tools.entity_reparse_maindb import _source_name
    row = {"implementing_entity": "", "subsidiary_name": None}
    assert _source_name(row) == ""


def test_build_update_plan_change_detected(tmp_path, monkeypatch):
    """能建国际集团 → 解析后 implementing 变为 能源建设集团，应记入变更计划。"""
    from s01b_project_extractor.tools.entity_reparse_maindb import build_update_plan

    # 模拟 normalize_entity：中国能建国际集团 → (中国能源建设集团有限公司, 中国能建国际集团)
    def fake_normalize(name):
        if name == "中国能建国际集团":
            return ("中国能源建设集团有限公司", "中国能建国际集团")
        return (name, "")

    # 模拟 _resolve_is_central_soe
    def fake_soe(entity):
        if "能源建设" in entity:
            return 1
        return 0

    import s01b_project_extractor.tools.entity_reparse_maindb as mod
    monkeypatch.setattr(mod, "_normalize_entity", fake_normalize)
    monkeypatch.setattr(mod, "_resolve_soe", fake_soe)

    db = _make_db(tmp_path, [
        (1, "EVT-001", "中国能建国际集团", "POWERCHINA International Group", "", None, "ai_translated", 0),
    ])

    plan, stats = build_update_plan(db)
    assert len(plan) == 1
    row = plan[0]
    assert row["new_impl"] == "中国能源建设集团有限公司"
    assert row["new_sub"] == "中国能建国际集团"
    assert row["impl_changed"] is True
    assert row["sub_changed"] is True  # 旧 sub 空，新 sub 非空 → sub 有变化
    # implementing 改变 → en 置 NULL
    assert row["impl_en_null"] is True
    # stats
    assert stats["total"] == 1
    assert stats["changed"] == 1
    assert stats["impl_changed"] == 1


def test_build_update_plan_no_change(tmp_path, monkeypatch):
    """已归位的行（normalize 后与当前值完全一致）→ 不进入变更计划。"""
    from s01b_project_extractor.tools.entity_reparse_maindb import build_update_plan

    def fake_normalize(name):
        # 已归位：source_name=中国能建国际集团，回来 (能源建设, 能建国际集团)
        if name == "中国能建国际集团":
            return ("中国能源建设集团有限公司", "中国能建国际集团")
        return (name, "")

    def fake_soe(entity):
        return 1 if "能源建设" in entity else 0

    import s01b_project_extractor.tools.entity_reparse_maindb as mod
    monkeypatch.setattr(mod, "_normalize_entity", fake_normalize)
    monkeypatch.setattr(mod, "_resolve_soe", fake_soe)

    # DB 中已存放"归位后"的值
    db = _make_db(tmp_path, [
        (1, "EVT-001", "中国能源建设集团有限公司", "POWERCHINA", "中国能建国际集团", None, "ai_translated", 1),
    ])

    plan, stats = build_update_plan(db)
    assert len(plan) == 0, "已归位，不应有变更计划"
    assert stats["changed"] == 0


def test_build_update_plan_skips_both_empty(tmp_path, monkeypatch):
    """implementing+subsidiary 均空 → 跳过，计入 skipped_empty。"""
    from s01b_project_extractor.tools.entity_reparse_maindb import build_update_plan

    def fake_normalize(name):
        return (name, "")

    def fake_soe(entity):
        return 0

    import s01b_project_extractor.tools.entity_reparse_maindb as mod
    monkeypatch.setattr(mod, "_normalize_entity", fake_normalize)
    monkeypatch.setattr(mod, "_resolve_soe", fake_soe)

    db = _make_db(tmp_path, [
        (1, "EVT-001", "", "en_val", None, None, "ai_translated", 0),
    ])

    plan, stats = build_update_plan(db)
    assert len(plan) == 0
    assert stats["skipped_empty"] == 1


def test_dry_run_does_not_write(tmp_path, monkeypatch):
    """dry-run 模式不修改库。"""
    from s01b_project_extractor.tools.entity_reparse_maindb import reparse_maindb

    def fake_normalize(name):
        if name == "中国能建国际集团":
            return ("中国能源建设集团有限公司", "中国能建国际集团")
        return (name, "")

    def fake_soe(entity):
        return 1 if "能源建设" in entity else 0

    import s01b_project_extractor.tools.entity_reparse_maindb as mod
    monkeypatch.setattr(mod, "_normalize_entity", fake_normalize)
    monkeypatch.setattr(mod, "_resolve_soe", fake_soe)

    db = _make_db(tmp_path, [
        (1, "EVT-001", "中国能建国际集团", "OLD_EN", "", None, "ai_translated", 0),
    ])

    result = reparse_maindb(db, apply=False)
    assert result["changed"] == 1

    # 库不应改变
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT implementing_entity FROM chinese_entities WHERE id=1").fetchone()
    conn.close()
    assert row[0] == "中国能建国际集团", "dry-run 不应写库"


def test_apply_updates_db_and_creates_backup(tmp_path, monkeypatch):
    """apply 模式先备份再批量 UPDATE，行数不变。"""
    from s01b_project_extractor.tools.entity_reparse_maindb import reparse_maindb

    def fake_normalize(name):
        if name == "中国能建国际集团":
            return ("中国能源建设集团有限公司", "中国能建国际集团")
        return (name, "")

    def fake_soe(entity):
        # 模拟：能源建设和电力建设都是央企
        if "能源建设" in entity or "电力建设" in entity:
            return 1
        return 0

    import s01b_project_extractor.tools.entity_reparse_maindb as mod
    monkeypatch.setattr(mod, "_normalize_entity", fake_normalize)
    monkeypatch.setattr(mod, "_resolve_soe", fake_soe)

    db = _make_db(tmp_path, [
        (1, "EVT-001", "中国能建国际集团", "OLD_EN", "", None, "ai_translated", 0),
        (2, "EVT-002", "中国电力建设集团有限公司", "PowerChina EN", "", None, "verified", 1),
    ])

    result = reparse_maindb(db, apply=True)
    assert result["changed"] == 1
    assert result["bak_path"] is not None
    assert Path(result["bak_path"]).exists()

    # 验证写库
    conn = sqlite3.connect(db)
    r = conn.execute(
        "SELECT implementing_entity, subsidiary_name, implementing_entity_en, is_central_soe FROM chinese_entities WHERE id=1"
    ).fetchone()
    cnt = conn.execute("SELECT COUNT(*) FROM chinese_entities").fetchone()[0]
    conn.close()

    assert r[0] == "中国能源建设集团有限公司", "implementing_entity 应已更新"
    assert r[1] == "中国能建国际集团", "subsidiary_name 应已更新"
    assert r[2] is None, "implementing_entity_en 应置 NULL"
    assert r[3] == 1, "is_central_soe 应更新为 1"
    assert cnt == 2, "行数不变"


def test_idempotent_after_apply(tmp_path, monkeypatch):
    """apply 后再次 dry-run plan 应得 0 变更（幂等）。"""
    from s01b_project_extractor.tools.entity_reparse_maindb import reparse_maindb

    def fake_normalize(name):
        if name == "中国能建国际集团":
            return ("中国能源建设集团有限公司", "中国能建国际集团")
        return (name, "")

    def fake_soe(entity):
        return 1 if "能源建设" in entity else 0

    import s01b_project_extractor.tools.entity_reparse_maindb as mod
    monkeypatch.setattr(mod, "_normalize_entity", fake_normalize)
    monkeypatch.setattr(mod, "_resolve_soe", fake_soe)

    db = _make_db(tmp_path, [
        (1, "EVT-001", "中国能建国际集团", "OLD_EN", "", None, "ai_translated", 0),
    ])

    # 第一次 apply
    reparse_maindb(db, apply=True)

    # 第二次 dry-run：应 0 变更
    result2 = reparse_maindb(db, apply=False)
    assert result2["changed"] == 0, "幂等：apply 后再 dry-run 应 0 变更"


def test_en_null_on_subsidiary_change(tmp_path, monkeypatch):
    """subsidiary_name 变化 → subsidiary_name_en 置 NULL。"""
    from s01b_project_extractor.tools.entity_reparse_maindb import reparse_maindb

    def fake_normalize(name):
        # source_name = subsidiary_name = "旧子公司名" → 解析后 sub 变为同名（但 impl 变了）
        if name == "旧子公司名":
            return ("新母公司", "旧子公司名")
        return (name, "")

    def fake_soe(entity):
        return 0

    import s01b_project_extractor.tools.entity_reparse_maindb as mod
    monkeypatch.setattr(mod, "_normalize_entity", fake_normalize)
    monkeypatch.setattr(mod, "_resolve_soe", fake_soe)

    # subsidiary_name 非空，旧 implementing 与新 implementing 不同
    db = _make_db(tmp_path, [
        (1, "EVT-001", "旧母公司", "OLD_IMPL_EN", "旧子公司名", "OLD_SUB_EN", "ai_translated", 0),
    ])

    reparse_maindb(db, apply=True)

    conn = sqlite3.connect(db)
    r = conn.execute(
        "SELECT implementing_entity, subsidiary_name, implementing_entity_en, subsidiary_name_en FROM chinese_entities WHERE id=1"
    ).fetchone()
    conn.close()

    assert r[0] == "新母公司"
    assert r[1] == "旧子公司名"
    assert r[2] is None, "implementing 变 → implementing_entity_en 置 NULL"
    # subsidiary_name 本身未变（旧="旧子公司名"，新="旧子公司名"，一致）→ en 不动
    # 规则（brief §en列一致性）：CN 未变则 en 不动
    assert r[3] == "OLD_SUB_EN", "subsidiary 未变 → subsidiary_name_en 保持原值"
