"""dedup_cache.py — 去重 LLM 判断结果的持久化缓存，支持断点续跑 + 失败重跑。

判断（每对项目是否同一）互相独立，故可分批落盘；键用两个项目的对称内容哈希，跨运行稳定。
"""
import hashlib
import sqlite3
from typing import Dict, List, Optional, Tuple


def _sig(p: dict) -> str:
    """单个项目的稳定内容签名（名/国/类/容量/排序信源）。"""
    links = "|".join(sorted(p.get("source_links") or []))
    return "‖".join([
        str(p.get("project_name", "")), str(p.get("country", "")),
        str(p.get("category", "")), str(p.get("capacity_mw", "")), links,
    ])


def pair_key(a: dict, b: dict) -> str:
    """两个项目的对称内容哈希键（与顺序无关），跨运行稳定。"""
    lo, hi = sorted([_sig(a), _sig(b)])
    return hashlib.sha1(f"{lo}❘{hi}".encode("utf-8")).hexdigest()


def init_cache(db_path: str) -> None:
    """建缓存表（幂等）。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pair_verdicts ("
            "pair_key TEXT PRIMARY KEY, same INTEGER, status TEXT NOT NULL)"
        )
        conn.commit()
    finally:
        conn.close()


def load_cache(db_path: str) -> Dict[str, Tuple[Optional[int], str]]:
    """读全部缓存为 {pair_key: (same, status)}。"""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT pair_key, same, status FROM pair_verdicts").fetchall()
    finally:
        conn.close()
    return {k: (s, st) for k, s, st in rows}


def upsert(db_path: str, items: List[Tuple[str, Optional[int], str]]) -> None:
    """批量写入 (pair_key, same, status)（事务 + executemany，CLAUDE.md 规则3）。"""
    if not items:
        return
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO pair_verdicts (pair_key, same, status) VALUES (?,?,?)",
                items,
            )
    finally:
        conn.close()
