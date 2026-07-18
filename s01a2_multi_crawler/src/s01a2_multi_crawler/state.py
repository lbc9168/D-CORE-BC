# state.py：crawl_state.db 的 schema 与读写（seen 集合、文章入库、运行日志；批量写走事务）
import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DB_PATH = DATA_DIR / "crawl_state.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    url TEXT PRIMARY KEY,
    site TEXT NOT NULL,
    title TEXT,
    publish_date TEXT,
    content_text TEXT,
    content_hash TEXT,
    fetched_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('baseline', 'fetched', 'error'))
);
CREATE INDEX IF NOT EXISTS idx_articles_site ON articles(site);
CREATE TABLE IF NOT EXISTS fetch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    site TEXT NOT NULL,
    ts TEXT NOT NULL,
    listing_pages INTEGER,
    empty_pages INTEGER,
    urls_discovered INTEGER,
    new_urls INTEGER,
    articles_fetched INTEGER,
    errors INTEGER,
    duration_s REAL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS article_filter (
    url TEXT PRIMARY KEY,
    verdict TEXT NOT NULL CHECK (verdict IN ('relevant', 'rejected')),
    stage TEXT NOT NULL CHECK (stage IN ('structural', 'keyword', 'llm')),
    reason TEXT,
    location TEXT,
    chinese_entity TEXT,
    is_project_event INTEGER,
    energy_type TEXT,
    country TEXT,
    model TEXT,
    filtered_at TEXT NOT NULL
);
"""


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """打开状态库（不存在则建 schema）"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    return conn


def seen_urls(conn: sqlite3.Connection, site: str) -> set[str]:
    """返回该站已见 URL 集合"""
    return {r[0] for r in conn.execute("SELECT url FROM articles WHERE site = ?", (site,))}


def baseline_urls(conn: sqlite3.Connection, site: str) -> list[str]:
    """返回该站尚未抓正文的 baseline 状态 URL（--fetch-baseline 用）"""
    return [r[0] for r in conn.execute("SELECT url FROM articles WHERE site = ? AND status = 'baseline'", (site,))]


def save_baseline(conn: sqlite3.Connection, site: str, items: list[dict]) -> int:
    """登记发现条目为 baseline（不抓文章页）：存 url + 列表页标题/日期，返回实际新增数；items: [{url, title?, date?}]"""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [(it["url"], site, it.get("title"), it.get("date"), now) for it in items]
    with conn:  # 单事务批量写
        cur = conn.executemany(
            "INSERT OR IGNORE INTO articles(url, site, title, publish_date, fetched_at, status)"
            " VALUES (?, ?, ?, ?, ?, 'baseline')",
            rows,
        )
    return cur.rowcount


def save_articles(conn: sqlite3.Connection, site: str, records: list[dict]) -> int:
    """批量入库抓好的文章（单事务）；record 需含 url/title/publish_date/content_text/status"""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [
        (
            r["url"],
            site,
            r.get("title"),
            r.get("publish_date"),
            r.get("content_text"),
            hashlib.sha256((r.get("content_text") or "").encode("utf-8")).hexdigest()[:16],
            now,
            r["status"],
        )
        for r in records
    ]
    with conn:
        cur = conn.executemany(
            "INSERT OR REPLACE INTO articles(url, site, title, publish_date, content_text, content_hash, fetched_at, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    return cur.rowcount


def log_run(conn: sqlite3.Connection, run_id: str, site: str, stats: dict) -> None:
    """记录一次站点抓取的运行统计（健康报告的数据源）"""
    with conn:
        conn.execute(
            "INSERT INTO fetch_log(run_id, site, ts, listing_pages, empty_pages, urls_discovered, new_urls,"
            " articles_fetched, errors, duration_s, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                site,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                stats.get("listing_pages", 0),
                stats.get("empty_pages", 0),
                stats.get("urls_discovered", 0),
                stats.get("new_urls", 0),
                stats.get("articles_fetched", 0),
                stats.get("errors", 0),
                stats.get("duration_s", 0.0),
                stats.get("note", ""),
            ),
        )
