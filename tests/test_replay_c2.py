"""replay_c2_would_change：确定性回放 would_change 到 extracted_projects 的纯 DB 行为。"""
import json
import sqlite3
import sys
from pathlib import Path

_root = next(p for p in Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
_oneoff = _root / "scripts" / "oneoff"
if str(_oneoff) not in sys.path:
    sys.path.insert(0, str(_oneoff))

import replay_c2_would_change as rp


def _make_db(tmp_path):
    """建一个含必要列的 extracted_projects，5 行 id=10..14。"""
    db = tmp_path / "projects.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE extracted_projects ("
        "id INTEGER PRIMARY KEY, category TEXT, capacity_mw REAL, capacity_mw_raw TEXT, "
        "involvement_type TEXT, project_name TEXT, "
        "excluded INTEGER DEFAULT 0, exclude_source TEXT, exclude_reason TEXT)"
    )
    conn.executemany(
        "INSERT INTO extracted_projects (id, category, capacity_mw, involvement_type, project_name) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (10, "光伏", 100.0, "EPC总承包", "A项目"),
            (11, "光伏", 200.0, "设备供应商", "B项目"),
            (12, "风电", 300.0, "EPC总承包", "C项目"),
            (13, "石油", 0.0, "投资方", "D油田"),
            (14, "光伏", None, "EPC总承包", "E项目"),
        ],
    )
    conn.commit()
    conn.close()
    return str(db)


def _wc():
    return [
        {"id": 13, "field": "excluded", "new_value": 1, "reason": "油气非可再生"},
        {"id": 10, "field": "involvement_type", "new_value": "设计/施工总包"},
        {"id": 14, "field": "capacity_mw", "new_value": 1500.0},
    ]


def test_replay_applies_all_three_kinds(tmp_path):
    """回放后：13 被排除、10 改参与方式、14 写容量(含 raw)；未涉及的行不变。"""
    db = _make_db(tmp_path)
    counts = rp.replay(db, _wc(), dry_run=False)
    assert counts["excluded"] == 1
    assert counts["involvement_type"] == 1
    assert counts["capacity_mw"] == 1

    conn = sqlite3.connect(db)
    r13 = conn.execute("SELECT excluded, exclude_source, exclude_reason FROM extracted_projects WHERE id=13").fetchone()
    assert r13 == (1, "review", "油气非可再生")
    assert conn.execute("SELECT involvement_type FROM extracted_projects WHERE id=10").fetchone()[0] == "设计/施工总包"
    r14 = conn.execute("SELECT capacity_mw, capacity_mw_raw FROM extracted_projects WHERE id=14").fetchone()
    assert r14[0] == 1500.0 and r14[1] == "1500.0"
    # 未涉及行不变
    assert conn.execute("SELECT involvement_type FROM extracted_projects WHERE id=11").fetchone()[0] == "设备供应商"
    assert conn.execute("SELECT excluded FROM extracted_projects WHERE id=12").fetchone()[0] == 0
    conn.close()


def test_dry_run_writes_nothing(tmp_path):
    """dry_run=True：返回计数正确，但库零改动。"""
    db = _make_db(tmp_path)
    counts = rp.replay(db, _wc(), dry_run=True)
    assert counts["excluded"] == 1 and counts["involvement_type"] == 1 and counts["capacity_mw"] == 1
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT excluded FROM extracted_projects WHERE id=13").fetchone()[0] == 0
    assert conn.execute("SELECT involvement_type FROM extracted_projects WHERE id=10").fetchone()[0] == "EPC总承包"
    conn.close()


def test_idempotent_rerun(tmp_path):
    """重复回放同一 would_change → 结果一致（SET 到固定值，无副作用）。"""
    db = _make_db(tmp_path)
    rp.replay(db, _wc(), dry_run=False)
    rp.replay(db, _wc(), dry_run=False)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT excluded FROM extracted_projects WHERE id=13").fetchone()[0] == 1
    assert conn.execute("SELECT involvement_type FROM extracted_projects WHERE id=10").fetchone()[0] == "设计/施工总包"
    conn.close()


def test_missing_id_and_non_whitelist_field_skipped(tmp_path):
    """库中不存在的 id 计 missing_id；白名单外字段计 skipped；都不影响其它行。"""
    db = _make_db(tmp_path)
    wc = [
        {"id": 999, "field": "excluded", "new_value": 1, "reason": "x"},   # id 不存在
        {"id": 11, "field": "country_iso", "new_value": "PAK"},            # 白名单外
        {"id": 10, "field": "involvement_type", "new_value": "设计/施工总包"},
    ]
    counts = rp.replay(db, wc, dry_run=False)
    assert counts["missing_id"] == 1
    assert counts["skipped"] == 1
    assert counts["involvement_type"] == 1
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT involvement_type FROM extracted_projects WHERE id=10").fetchone()[0] == "设计/施工总包"
    conn.close()


def test_load_would_change_rejects_non_dryrun(tmp_path):
    """非 dry-run 报告（无 would_change）应被拒绝。"""
    import pytest
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"dry_run": False, "n_candidates": 5}), encoding="utf-8")
    with pytest.raises(ValueError):
        rp.load_would_change(str(p))
