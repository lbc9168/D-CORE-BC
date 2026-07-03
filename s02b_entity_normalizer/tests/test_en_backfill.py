"""英文名回填：cache 确定性映射（hit 更新 / miss 只报告 / ascii 跳过 / 多公司串全命中才拼）。"""
import sqlite3
from pathlib import Path
from s02b_entity_normalizer.en_backfill import (
    TARGETS_MAIN, apply_db, plan_db, resolve_en,
)

CACHE = {
    "中国电建": {"en": "Power Construction Corporation of China", "en_abbr": "PowerChina"},
    "阳光电源": {"en": "Sungrow Power Supply Co., Ltd.", "en_abbr": None},
    "坏值公司": {"en": "坏值公司"},  # en 含中文 → 视为 miss
}


def test_resolve_en_statuses():
    assert resolve_en(CACHE, "中国电建") == ("PowerChina", "hit")          # en_abbr 优先（缩写为主口径）
    assert resolve_en(CACHE, "阳光电源") == ("Sungrow Power Supply Co., Ltd.", "hit")
    assert resolve_en(CACHE, "没收录公司") == (None, "miss")
    assert resolve_en(CACHE, "坏值公司") == (None, "miss")                  # 缓存值含中文不算命中
    assert resolve_en(CACHE, "ACWA Power") == (None, "ascii_skip")
    assert resolve_en(CACHE, "Unknown") == (None, "skip")
    assert resolve_en(CACHE, None) == (None, "skip")
    assert resolve_en(CACHE, "中国电建;阳光电源") == ("PowerChina; Sungrow Power Supply Co., Ltd.", "hit_multi")
    assert resolve_en(CACHE, "中国电建;没收录公司") == (None, "partial_miss")
    assert resolve_en(CACHE, "中国电建;ACWA Power") == ("PowerChina; ACWA Power", "hit_multi")


def _mk_main_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE chinese_entities (id INTEGER PRIMARY KEY,
            implementing_entity TEXT, implementing_entity_en TEXT,
            subsidiary_name TEXT, subsidiary_name_en TEXT);
        CREATE TABLE project_events (id INTEGER PRIMARY KEY,
            project_developer TEXT, project_developer_en TEXT);
    """)
    conn.executemany(
        "INSERT INTO chinese_entities(implementing_entity, implementing_entity_en, subsidiary_name, subsidiary_name_en) VALUES (?,?,?,?)",
        [("中国电建", "China Power Construction Something Wrong", "阳光电源", None),
         ("中国电建", "PowerChina", None, None),          # 已一致 → 不更新
         ("没收录公司", "Old Guess", None, None)])         # miss → 不动只报告
    conn.executemany(
        "INSERT INTO project_events(project_developer, project_developer_en) VALUES (?,?)",
        [("中国电建;阳光电源", "whole-string garbage"), ("ACWA Power", "ACWA Power")])
    conn.commit()
    return conn


def test_plan_db(tmp_path):
    db = str(tmp_path / "main.db")
    conn = _mk_main_db(db)
    plan = plan_db(conn, TARGETS_MAIN, CACHE)
    conn.close()
    updates = {(p["table"], p["cn"]): p for p in plan if p["action"] == "update"}
    reports = {(p["table"], p["cn"]): p for p in plan if p["action"] == "report_only"}
    # 中国电建：一行英文错、一行已对 → 值级 update（new_en=PowerChina）
    assert updates[("chinese_entities", "中国电建")]["new_en"] == "PowerChina"
    # 子公司列独立回填
    assert updates[("chinese_entities", "阳光电源")]["new_en"].startswith("Sungrow")
    # 多公司串全命中 → update 且 '; ' 拼接
    assert updates[("project_events", "中国电建;阳光电源")]["new_en"] == "PowerChina; Sungrow Power Supply Co., Ltd."
    # miss → report_only；ascii → 不出现在计划里
    assert ("chinese_entities", "没收录公司") in reports
    assert not any(p["cn"] == "ACWA Power" for p in plan)


def test_apply_db_updates_and_backs_up(tmp_path):
    db = str(tmp_path / "main.db")
    conn = _mk_main_db(db)
    plan = plan_db(conn, TARGETS_MAIN, CACHE)
    conn.close()
    n = apply_db(db, plan)
    assert n >= 3
    conn = sqlite3.connect(db)
    ens = [r[0] for r in conn.execute(
        "SELECT implementing_entity_en FROM chinese_entities WHERE implementing_entity='中国电建'")]
    assert ens == ["PowerChina", "PowerChina"]
    miss_en = conn.execute(
        "SELECT implementing_entity_en FROM chinese_entities WHERE implementing_entity='没收录公司'"
    ).fetchone()[0]
    assert miss_en == "Old Guess", "miss 行绝不改写"
    dev_en = conn.execute(
        "SELECT project_developer_en FROM project_events WHERE project_developer='中国电建;阳光电源'"
    ).fetchone()[0]
    assert dev_en == "PowerChina; Sungrow Power Supply Co., Ltd."
    conn.close()
    assert list(Path(db).parent.glob("main.db.bak.*")), "apply 前必须备份"
