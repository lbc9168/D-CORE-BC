"""article_store 测试：原文取用 (get_article) + 提取结果缓存 (cache_*) 的 TDD 行为验证。"""
import sqlite3
import eval.article_store as store


def _make_raw_news_db(db_path, url, title, content, publish_date):
    """在指定路径建立一个含 raw_news 表的临时 sqlite 库并插入一行。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE raw_news ("
            "id INTEGER PRIMARY KEY, url TEXT, title TEXT, content TEXT, "
            "author TEXT, publish_date TEXT, crawl_time TEXT, "
            "passed_keyword_filter INTEGER, batch_id TEXT)"
        )
        conn.execute(
            "INSERT INTO raw_news (url, title, content, publish_date) VALUES (?,?,?,?)",
            (url, title, content, publish_date),
        )
        conn.commit()
    finally:
        conn.close()


def test_get_article_returns_dict(tmp_path):
    db = tmp_path / "raw_news.db"
    _make_raw_news_db(db, "http://x.test/a", "标题A", "正文内容A", "2024-01-02")
    a = store.get_article("http://x.test/a", db_path=str(db))
    assert a == {"title": "标题A", "content": "正文内容A", "publish_date": "2024-01-02"}


def test_get_article_missing_returns_none(tmp_path):
    db = tmp_path / "raw_news.db"
    _make_raw_news_db(db, "http://x.test/a", "标题A", "正文内容A", "2024-01-02")
    assert store.get_article("http://x.test/none", db_path=str(db)) is None


def test_cache_round_trip(tmp_path):
    key = "k1"
    val = {"foo": "bar", "中文": [1, 2, 3]}
    store.cache_put(key, val, cache_dir=tmp_path)
    assert store.cache_get(key, cache_dir=tmp_path) == val


def test_cache_get_missing_returns_none(tmp_path):
    assert store.cache_get("nope", cache_dir=tmp_path) is None


def test_cache_key_stable_and_distinct():
    k1 = store.cache_key("http://x.test/a", "v1")
    k2 = store.cache_key("http://x.test/a", "v1")
    k3 = store.cache_key("http://x.test/a", "v2")
    k4 = store.cache_key("http://x.test/b", "v1")
    assert k1 == k2
    assert k1 != k3
    assert k1 != k4
