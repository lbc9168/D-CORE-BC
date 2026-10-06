"""Offline HTML fixtures: failed pages never become completed checkpoints."""
import sqlite3
from types import SimpleNamespace

import pytest
import requests
from s01a_news_crawler.tools import news_crawler_tool as crawler


def response(html):
    return SimpleNamespace(text=html, apparent_encoding="utf-8", encoding="utf-8")


def listing(ids):
    return "<html>" + "".join(f'<li class="list"><div class="pull-left">2026-09-23</div><a href="/article/{i}">article {i}</a></li>' for i in ids) + "</html>"


def article():
    return response('<div class="list_top"><h3>风电项目</h3><div class="pull-left">发布时间：2026-09-23 12:00:00</div></div><div class="detail-content">中国企业参与风电项目</div>')


def run(tool, tmp_path):
    return tool._run(source_url="https://example.test/news",start_date="2026-09-23",end_date="2026-09-23",keywords="风电",output_dir=str(tmp_path / "raw"),raw_news_db_path=str(tmp_path / "raw.db"),save_excel=False)


def test_request_retries_exhaustion_is_visible(monkeypatch):
    calls = []
    def timeout(*a, **kw):
        calls.append(1)
        raise requests.exceptions.Timeout("offline")
    monkeypatch.setattr(crawler.requests,"get",timeout)
    monkeypatch.setattr(crawler.time,"sleep",lambda *a: None)
    with pytest.raises(RuntimeError, match="重试耗尽"):
        crawler.NewsCrawlerTool()._request_with_retry("https://example.test/page")
    assert len(calls) == crawler.MAX_RETRIES


def test_failed_list_keeps_page_and_never_skips_forward(tmp_path, monkeypatch):
    tool = crawler.NewsCrawlerTool()
    calls = []
    monkeypatch.setattr(tool,"_request_with_retry",lambda url, **kw: calls.append(url))
    with pytest.raises(RuntimeError, match="列表页请求失败"):
        run(tool,tmp_path)
    with sqlite3.connect(tmp_path / "raw.db") as conn:
        assert conn.execute("SELECT last_page FROM crawler_checkpoints").fetchone() == (1,)
        assert conn.execute("SELECT COUNT(*) FROM raw_news").fetchone() == (0,)
    assert len(calls) == 1


def test_detail_failure_resume_passes_five_duplicate_urls(tmp_path, monkeypatch):
    monkeypatch.setattr(crawler.time,"sleep",lambda *a: None)
    tool = crawler.NewsCrawlerTool()
    def first_request(url, **kw):
        if "?page=" in url:
            return response(listing(range(1,7)))
        if url.endswith("/6"):
            return None
        return article()
    monkeypatch.setattr(tool,"_request_with_retry",first_request)
    with pytest.raises(RuntimeError, match="详情页请求失败"):
        run(tool,tmp_path)
    with sqlite3.connect(tmp_path / "raw.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_news").fetchone() == (5,)
        assert conn.execute("SELECT last_page FROM crawler_checkpoints").fetchone() == (1,)
    calls = []
    def retry(url, **kw):
        calls.append(url)
        if "?page=1" in url:
            return response(listing(range(1,7)))
        if "?page=" in url:
            return response("<html></html>")
        return article()
    monkeypatch.setattr(tool,"_request_with_retry",retry)
    assert "采集完成" in run(tool,tmp_path)
    with sqlite3.connect(tmp_path / "raw.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_news").fetchone() == (6,)
    assert "https://example.test/article/6" in calls
    assert not any(url.endswith(f"/{i}") for url in calls for i in range(1, 6))


def test_overlap_scan_reuses_content_and_reaches_gap(tmp_path, monkeypatch):
    monkeypatch.setattr(crawler.time, "sleep", lambda *a: None)
    with crawler._init_raw_news_db(str(tmp_path / "raw.db")) as conn:
        conn.executemany(
            "INSERT INTO raw_news(url,content,publish_date,passed_keyword_filter) VALUES (?,?,?,1)",
            [(f"https://example.test/article/{i}", "已保存风电全文", "2026-09-23") for i in range(1, 6)],
        )
    tool = crawler.NewsCrawlerTool()
    calls = []
    def request(url, **kw):
        calls.append(url)
        if "?page=1" in url:
            return response(listing(range(1, 7)))
        if "?page=" in url:
            return response("<html></html>")
        return article()
    monkeypatch.setattr(tool, "_request_with_retry", request)
    tool._run(source_url="https://example.test/news", start_date="2026-09-23",
              end_date="2026-09-23", keywords="风电", output_dir=str(tmp_path / "raw"),
              raw_news_db_path=str(tmp_path / "raw.db"), save_excel=False, stop_on_existing=False)
    assert calls == ["https://example.test/news?page=1", "https://example.test/article/6",
                     "https://example.test/news?page=2"]
    with sqlite3.connect(tmp_path / "raw.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_news").fetchone() == (6,)


def test_database_write_failure_propagates(tmp_path, monkeypatch):
    monkeypatch.setattr(crawler.time,"sleep",lambda *a: None)
    real_init = crawler._init_raw_news_db
    def broken(path):
        conn = real_init(path)
        conn.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON raw_news BEGIN SELECT RAISE(ABORT,'forced write failure'); END")
        conn.commit()
        return conn
    monkeypatch.setattr(crawler,"_init_raw_news_db",broken)
    tool = crawler.NewsCrawlerTool()
    monkeypatch.setattr(tool,"_request_with_retry",lambda url, **kw: response(listing([1])) if "?page=" in url else article())
    with pytest.raises(sqlite3.Error, match="forced write failure"):
        run(tool,tmp_path)
    with sqlite3.connect(tmp_path / "raw.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_news").fetchone() == (0,)
        assert conn.execute("SELECT last_page FROM crawler_checkpoints").fetchone() == (1,)


def test_reuses_readonly_fulltext_cache_through_normal_crawler(tmp_path, monkeypatch):
    monkeypatch.setattr(crawler.time, "sleep", lambda *a: None)
    cache = tmp_path / "cached.db"
    with crawler._init_raw_news_db(str(cache)) as conn:
        conn.execute("INSERT INTO raw_news(url,title,author,publish_date,content) VALUES (?,?,?,?,?)",
                     ("https://example.test/article/1", "缓存风电", "author", "2026-09-23", "中国企业风电全文"))
    import hashlib
    before = hashlib.sha256(cache.read_bytes()).hexdigest()
    tool = crawler.NewsCrawlerTool()
    calls = []
    def request(url, **kw):
        calls.append(url)
        assert "?page=" in url, "cached article must not be downloaded"
        return response(listing([1]) if "?page=1" in url else "<html></html>")
    monkeypatch.setattr(tool, "_request_with_retry", request)
    tool._run(source_url="https://example.test/news", start_date="2026-09-23", end_date="2026-09-23",
              keywords="风电", output_dir=str(tmp_path / "raw"), raw_news_db_path=str(tmp_path / "raw.db"),
              save_excel=False, stop_on_existing=False, reuse_news_db_path=str(cache))
    with sqlite3.connect(tmp_path / "raw.db") as conn:
        assert conn.execute("SELECT title,content FROM raw_news").fetchone() == ("缓存风电", "中国企业风电全文")
    assert hashlib.sha256(cache.read_bytes()).hexdigest() == before


def test_http_403_is_not_empty_success(monkeypatch):
    result = requests.Response()
    result.status_code = 403
    result.url = "https://example.test/blocked"
    monkeypatch.setattr(crawler.requests,"get",lambda *a,**kw: result)
    with pytest.raises(RuntimeError, match="403"):
        crawler.NewsCrawlerTool()._request_with_retry(result.url)


@pytest.mark.parametrize("status", [429, 503])
def test_transient_http_retries_and_respects_retry_after(monkeypatch,status):
    failed = requests.Response()
    failed.status_code = status
    failed.url = "https://example.test/retry"
    failed.headers["Retry-After"] = "12"
    success = requests.Response()
    success.status_code = 200
    responses = iter([failed,success])
    sleeps = []
    monkeypatch.setattr(crawler.requests,"get",lambda *a,**kw: next(responses))
    monkeypatch.setattr(crawler.time,"sleep",sleeps.append)
    assert crawler.NewsCrawlerTool()._request_with_retry(failed.url) is success
    assert len(sleeps) == 1 and sleeps[0] >= 12
