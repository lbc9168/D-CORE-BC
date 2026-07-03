"""developer 归一：拆串/垃圾值统一/保守重建/计划与落库（LLM 环节不在单测内，注入映射测确定性部分）。"""
import sqlite3
from pathlib import Path
from s02b_entity_normalizer.developer_normalizer import (
    apply_value_changes, plan_value_changes, rebuild, split_developer, unify_junk,
)


def test_split_developer():
    assert split_developer("A公司;B公司、C公司") == ["A公司", "B公司", "C公司"]
    assert split_developer(" 单一公司 ") == ["单一公司"]
    assert split_developer("") == []
    assert split_developer(None) == []


def test_unify_junk():
    assert unify_junk("业主") == "Unknown"
    assert unify_junk("未知") == "Unknown"
    assert unify_junk("N/A") == "Unknown"
    assert unify_junk("中国电建") is None
    assert unify_junk("当地政府") is None  # 有语义的值不动


def test_rebuild_conservative():
    m = {"中国电建": "中国电力建设集团有限公司"}
    assert rebuild("中国电建;阳光电源", m) == "中国电力建设集团有限公司; 阳光电源"
    # 无部件命中映射 → 原样返回（连分隔符风格都不动）
    assert rebuild("A公司、B公司", {}) == "A公司、B公司"
    assert rebuild("中国电建", m) == "中国电力建设集团有限公司"


def _mk_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE project_events (id INTEGER PRIMARY KEY, project_developer TEXT)")
    conn.executemany("INSERT INTO project_events(project_developer) VALUES (?)", [
        ("中国电建;阳光电源",),
        ("中国电建",),
        ("业主",),
        ("ACWA Power",),
        (None,),
    ])
    conn.commit()
    return conn


def test_plan_value_changes(tmp_path):
    db = str(tmp_path / "t.db")
    conn = _mk_db(db)
    pairs = plan_value_changes(conn, {"中国电建": "中国电力建设集团有限公司"})
    conn.close()
    d = dict(pairs)
    assert d["中国电建;阳光电源"] == "中国电力建设集团有限公司; 阳光电源"
    assert d["中国电建"] == "中国电力建设集团有限公司"
    assert d["业主"] == "Unknown"
    assert "ACWA Power" not in d, "无变化的值不列入计划"


def test_apply_value_changes_with_backup(tmp_path):
    db = str(tmp_path / "t.db")
    _mk_db(db).close()
    n = apply_value_changes(db, [("业主", "Unknown"), ("中国电建", "中国电力建设集团有限公司")])
    assert n == 2
    conn = sqlite3.connect(db)
    vals = {r[0] for r in conn.execute("SELECT project_developer FROM project_events").fetchall()}
    conn.close()
    assert "Unknown" in vals and "中国电力建设集团有限公司" in vals and "业主" not in vals
    assert list(Path(db).parent.glob("t.db.bak.*")), "apply 前必须备份"


def test_apply_empty_pairs_no_backup(tmp_path):
    db = str(tmp_path / "t.db")
    _mk_db(db).close()
    assert apply_value_changes(db, []) == 0
    assert not list(Path(db).parent.glob("t.db.bak.*")), "无变更不产生备份"
