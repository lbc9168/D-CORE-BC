"""test_db_backup.py — backup_db 的 WAL 安全快照与内容去重行为。"""
import sqlite3
import time
from pathlib import Path

from s02b_entity_normalizer.db_backup import backup_db


def _make_db(path: str) -> None:
    """建一个含单表单行的测试库。"""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES ('a')")
    conn.commit()
    conn.close()


def test_backup_dedupes_when_content_unchanged(tmp_path):
    """内容未变时第二次备份复用旧文件，不新增。"""
    db = str(tmp_path / "x.db")
    _make_db(db)
    b1 = backup_db(db)
    time.sleep(1.1)  # 确保时间戳文件名不同
    b2 = backup_db(db)
    assert b1 == b2
    assert len(list(Path(db).parent.glob("x.db.bak.*"))) == 1


def test_backup_creates_new_after_change(tmp_path):
    """内容变化后再备份产生新文件。"""
    db = str(tmp_path / "x.db")
    _make_db(db)
    b1 = backup_db(db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO t VALUES ('b')")
    conn.commit()
    conn.close()
    time.sleep(1.1)
    b2 = backup_db(db)
    assert b1 != b2
    assert len(list(Path(db).parent.glob("x.db.bak.*"))) == 2


def test_backup_includes_wal_committed_rows(tmp_path):
    """WAL 模式下已提交未 checkpoint 的行必须进入快照（旧 copy2 实现会丢）。"""
    db = str(tmp_path / "w.db")
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES ('wal-row')")
    conn.commit()  # 提交进 WAL，不 checkpoint、不关连接
    bak = backup_db(db)
    check = sqlite3.connect(bak)
    rows = check.execute("SELECT v FROM t").fetchall()
    check.close()
    conn.close()
    assert rows == [("wal-row",)]
