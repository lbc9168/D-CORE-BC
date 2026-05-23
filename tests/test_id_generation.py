"""s02a 编号生成：数值排序，跨 9999/99 边界不产生重复。"""
import sqlite3

from s02a_duplicate_merger.tools.duplicate_merger_tool import (
    _gen_canonical_id, _gen_event_id, SCHEMA_SQL,
)


def _db():
    """构造内存库并建表，返回连接。"""
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA_SQL)
    return c


def test_canonical_id_numeric_order_past_9999():
    c = _db()
    for n in (9, 10, 9999, 10000):  # 含跨 9999 边界
        c.execute("INSERT INTO canonical_projects(id, category) VALUES (?, '光伏')",
                  (f"IND-PV-{n:04d}",))
    # 字符串 DESC 会误把 'IND-PV-9999' 当最大 → 重复生成 10000；正确应为 10001
    assert _gen_canonical_id(c, "IND", "PV") == "IND-PV-10001"


def test_canonical_id_first_of_kind():
    c = _db()
    assert _gen_canonical_id(c, "BRA", "WD") == "BRA-WD-0001"


def test_event_id_numeric_order_past_99():
    c = _db()
    c.execute("INSERT INTO canonical_projects(id, category) VALUES ('IND-PV-0001','光伏')")
    for n in (9, 10, 99, 100):
        c.execute("INSERT INTO project_events(id, canonical_id) VALUES (?, 'IND-PV-0001')",
                  (f"IND-PV-0001-{n:02d}",))
    assert _gen_event_id(c, "IND-PV-0001") == "IND-PV-0001-101"


def test_event_id_first_of_kind():
    c = _db()
    c.execute("INSERT INTO canonical_projects(id, category) VALUES ('IND-PV-0001','光伏')")
    assert _gen_event_id(c, "IND-PV-0001") == "IND-PV-0001-01"
