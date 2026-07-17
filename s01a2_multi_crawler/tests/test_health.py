# 健康报告测试：0 发现才告警，"有空页但已追平"不误报
from s01a2_multi_crawler.health import generate_report
from s01a2_multi_crawler.state import connect, log_run


def test_no_false_alert_when_caught_up(tmp_path):
    """站点已追平（0 新增）但有空页（如 sitemap 某日未生成），只要发现到 URL 就不告警"""
    conn = connect(tmp_path / "t.db")
    log_run(conn, "r0", "yidaiyilu",  # 历史上有过新增（否则触发"从未新增"另一路径）
            {"urls_discovered": 100, "new_urls": 100, "empty_pages": 0, "errors": 0, "articles_fetched": 0})
    log_run(conn, "r1", "yidaiyilu",
            {"urls_discovered": 118, "new_urls": 0, "empty_pages": 3, "errors": 0, "articles_fetched": 0})
    _, alerts = generate_report(conn, tmp_path / "reports")
    assert alerts == 0


def test_alert_when_zero_discovered(tmp_path):
    """整站发现 0 个 URL = 真·改版/失效信号，必须告警"""
    conn = connect(tmp_path / "t.db")
    log_run(conn, "r1", "somesite",
            {"urls_discovered": 0, "new_urls": 0, "empty_pages": 1, "errors": 0, "articles_fetched": 0})
    _, alerts = generate_report(conn, tmp_path / "reports")
    assert alerts >= 1
