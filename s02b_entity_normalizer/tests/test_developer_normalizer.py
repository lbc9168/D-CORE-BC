"""developer 归一：拆串/垃圾值统一/保守重建/计划与落库（LLM 环节不在单测内，注入映射测确定性部分）。"""
import sqlite3
from pathlib import Path
from s02b_entity_normalizer.developer_normalizer import (
    apply_value_changes, plan_value_changes, rebuild, run, split_developer, unify_junk,
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
    assert "中国电建;阳光电源" in vals, "未列入 pairs 的行必须原样保留"
    assert list(Path(db).parent.glob("t.db.bak.*")), "apply 前必须备份"


def test_apply_empty_pairs_no_backup(tmp_path):
    db = str(tmp_path / "t.db")
    _mk_db(db).close()
    assert apply_value_changes(db, []) == 0
    assert not list(Path(db).parent.glob("t.db.bak.*")), "无变更不产生备份"


def test_bare_run_is_preview_no_write_no_backup(tmp_path):
    """裸跑 run(db_path=...) 默认等同 preview：不写库、不备份、不需要 GEMINI_API_KEY。"""
    db = str(tmp_path / "t.db")
    conn = _mk_db(db)
    conn.close()
    # 不设 GEMINI_API_KEY，preview 路径应直接返回不调 LLM
    run(db_path=db)  # apply 默认 False → preview 路径
    assert not list(Path(db).parent.glob("t.db.bak.*")), "preview 不产生备份"
    conn = sqlite3.connect(db)
    vals = {r[0] for r in conn.execute(
        "SELECT project_developer FROM project_events WHERE project_developer IS NOT NULL"
    ).fetchall()}
    conn.close()
    # 原值未改写：业主、中国电建 依然是原值（未归一）
    assert "业主" in vals, "preview 不改写库"
    assert "中国电建" in vals, "preview 不改写库"


def test_filter_candidates_conservative():
    """保守预过滤：联合体/连字符串（多主体载体）不得参与合并。"""
    from s02b_entity_normalizer.developer_normalizer import filter_candidates_conservative
    cands = [
        {"name_a": "中国电建", "name_b": "中国电建-国电电力联合体"},
        {"name_a": "中国电建与上海电建组成的联合体", "name_b": "中国电建"},
        {"name_a": "中国电力建设集团有限公司", "name_b": "中国电建"},
    ]
    out = filter_candidates_conservative(cands)
    assert len(out) == 1
    assert out[0]["name_a"] == "中国电力建设集团有限公司"


def test_dev_confirm_prompt_conservative_clauses():
    """业主专用确认提示词：同一法人变体才合并、母子公司不合并的条款必须在。"""
    from s02b_entity_normalizer.developer_normalizer import _build_dev_confirm_prompt
    p = _build_dev_confirm_prompt([{"id": 0, "name_a": "甲", "count_a": 1,
                                    "name_b": "乙", "count_b": 1, "signals": "s"}])
    assert "同一家法人" in p
    assert "母公司与子公司" in p and "不合并" in p
    assert "联合体" in p
