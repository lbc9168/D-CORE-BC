"""location_normalizer 的纯 DB 读写函数（不触 LLM、不 import genai）。"""
import sqlite3
import sys
from pathlib import Path

_root = next(p for p in Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
_s02b_src = _root / "s02b_entity_normalizer" / "src"
if str(_s02b_src) not in sys.path:
    sys.path.insert(0, str(_s02b_src))

from s02b_entity_normalizer.location_normalizer import (
    _load_locations_by_country, _write_location_norm,
)


def _make_db(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE canonical_projects (id TEXT PRIMARY KEY, country_iso TEXT, location TEXT)")
    conn.executemany(
        "INSERT INTO canonical_projects VALUES (?, ?, ?)",
        [
            ("PAK-WD-0001", "PAK", "达瓦德密"),
            ("PAK-WD-0002", "PAK", "达旺德米"),
            ("PAK-PV-0001", "PAK", "达瓦德密"),
            ("KEN-PV-0001", "KEN", "加里萨"),
            ("KEN-PV-0002", "KEN", ""),
        ],
    )
    conn.commit()
    conn.close()
    return str(db)


def test_load_groups_by_country_and_skips_empty(tmp_path):
    by_country = _load_locations_by_country(_make_db(tmp_path))
    assert sorted(by_country["PAK"]) == [("达旺德米", 1), ("达瓦德密", 2)]
    assert by_country["KEN"] == [("加里萨", 1)]


def test_write_location_norm_only_touches_mapped_rows(tmp_path):
    db = _make_db(tmp_path)
    n = _write_location_norm(db, {"PAK": {"达旺德米": "达瓦德密"}})
    assert n == 1
    conn = sqlite3.connect(db)
    rows = dict(conn.execute("SELECT id, location_norm FROM canonical_projects").fetchall())
    assert rows["PAK-WD-0002"] == "达瓦德密"
    assert rows["PAK-WD-0001"] is None          # 未映射 → 留 NULL，消费方回退 location
    # 原 location 永不覆盖
    assert conn.execute("SELECT location FROM canonical_projects WHERE id='PAK-WD-0002'").fetchone()[0] == "达旺德米"
    # 幂等：重复写不报错（ALTER 已存在列被沉默）
    assert _write_location_norm(db, {"PAK": {"达旺德米": "达瓦德密"}}) == 1
    conn.close()
