# 状态库测试：baseline 幂等、文章入库、seen 集合
from s01a2_multi_crawler.state import connect, save_articles, save_baseline, seen_urls


def _conn(tmp_path):
    return connect(tmp_path / "t.db")


def test_baseline_idempotent(tmp_path):
    """重复 baseline 不产生重复行"""
    conn = _conn(tmp_path)
    assert save_baseline(conn, "s", ["http://a/1", "http://a/2"]) == 2
    assert save_baseline(conn, "s", ["http://a/1", "http://a/2"]) == 0
    assert seen_urls(conn, "s") == {"http://a/1", "http://a/2"}


def test_save_articles_upgrades_baseline(tmp_path):
    """baseline 的 URL 抓到正文后 REPLACE 为 fetched"""
    conn = _conn(tmp_path)
    save_baseline(conn, "s", ["http://a/1"])
    save_articles(conn, "s", [{"url": "http://a/1", "status": "fetched", "title": "t",
                               "publish_date": "2026-07-16", "content_text": "正文" * 30}])
    row = conn.execute("SELECT status, title FROM articles WHERE url='http://a/1'").fetchone()
    assert row == ("fetched", "t")


def test_seen_scoped_by_site(tmp_path):
    """seen 集合按站点隔离"""
    conn = _conn(tmp_path)
    save_baseline(conn, "a", ["http://x/1"])
    assert seen_urls(conn, "b") == set()
