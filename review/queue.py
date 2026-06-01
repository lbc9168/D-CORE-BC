"""review_queue：E.1/E.2 统一复核清单（review.db 新表 review_queue）。"""
import logging
import sqlite3

logger = logging.getLogger(__name__)

_REASON_TYPES = ("unnamed_entity", "low_confidence_field", "status_challenge", "exclude_suggested")


def init_queue(db_path) -> None:
    """建 review_queue 表（幂等）。"""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS review_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_id TEXT, source_link TEXT,
                reason_type TEXT NOT NULL, field TEXT,
                current_value TEXT, suggested_value TEXT,
                critic_confidence REAL, evidence TEXT,
                state TEXT NOT NULL DEFAULT 'pending', created_at TEXT
            )"""
        )
        conn.commit()
    finally:
        conn.close()


def add_item(db_path, *, canonical_id, source_link, reason_type, field, current_value,
             suggested_value, critic_confidence, evidence) -> None:
    """插入一条复核项；reason_type 必须合法，否则抛错（入口校验）。"""
    if reason_type not in _REASON_TYPES:
        raise ValueError(f"非法 reason_type={reason_type!r}，合法={_REASON_TYPES}")
    # 低频路径：per-call 连接可接受
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """INSERT INTO review_queue
               (canonical_id, source_link, reason_type, field, current_value,
                suggested_value, critic_confidence, evidence, state, created_at)
               VALUES (?,?,?,?,?,?,?,?, 'pending', datetime('now'))""",
            (canonical_id, source_link, reason_type, field, current_value,
             suggested_value, critic_confidence, evidence),
        )
        conn.commit()
    finally:
        conn.close()


def pending(db_path) -> list[dict]:
    """读所有 state='pending' 的复核项为 dict 列表。"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM review_queue WHERE state='pending' ORDER BY id")]
    finally:
        conn.close()
    return rows
