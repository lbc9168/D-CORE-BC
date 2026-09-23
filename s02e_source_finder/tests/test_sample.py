"""Focused acceptance-contract tests; no real HTTP or model requests."""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from s02e_source_finder.contracts import EventContext, Judgement, Page, make_reference, validate_evidence
from s02e_source_finder.fetch import PublicFetcher, parse_content, public_url
from s02e_source_finder.sample import SampleConfig, load_context, run_sample


class EvidenceTests(unittest.TestCase):
    """Accepted records require identity, milestone and locatable publisher evidence."""

    def setUp(self):
        """Build a small Chinese primary-source example with explicit award evidence."""
        self.page = Page(url="https://news.example.org/award", final_url="https://news.example.org/award", checked_at="2026-09-06T10:00:00+00:00", title="中标埃及储能项目", site_name="测试企业", text="测试企业中标埃及本班1000兆瓦时储能项目。该项目于2026年2月中标。")
        self.verdict = Judgement(decision="event_reference", reason="同项目中标事件", language="zh", publisher="测试企业", source_type="official", publisher_evidence="测试企业", same_project=True, project_evidence=["埃及本班1000兆瓦时储能项目"], same_stage=True, same_time=True, event_evidence=["该项目于2026年2月中标"], conflicts=[], is_reprint=False, original_publisher=None)

    def test_accepted_evidence(self):
        """Accept a complete, directly locatable project and event correspondence."""
        self.assertEqual(validate_evidence(self.verdict, self.page), [])

    def test_inline_whitespace_only_is_normalized(self):
        """Ignore HTML layout breaks while still refusing omitted factual text."""
        split_page = self.page.model_copy(update={"text": self.page.text.replace("本班", "本\n班")})
        self.assertEqual(validate_evidence(self.verdict, split_page), [])
        omitted = self.verdict.model_copy(update={"project_evidence": ["埃及1000兆瓦时储能项目"]})
        self.assertIn("project_evidence_not_located", validate_evidence(omitted, split_page))

    def test_reprint_origin_must_appear_on_page(self):
        """A model may not invent an original publisher for a syndicated article."""
        verdict = self.verdict.model_copy(update={"is_reprint": True, "original_publisher": "不存在的媒体"})
        self.assertIn("reprint_origin_not_located", validate_evidence(verdict, self.page))

    def test_no_unlocated_or_cross_project_evidence(self):
        """Refuse fabricated quotation, wrong country or unresolved capacity conflict."""
        for updates in ({"project_evidence": ["英国600兆瓦风电"]}, {"same_project": False}, {"conflicts": ["容量对应不同项目"]}, {"publisher_evidence": "另一企业"}):
            with self.subTest(updates=updates):
                self.assertTrue(validate_evidence(self.verdict.model_copy(update=updates), self.page))

    def test_planned_not_actual_milestone(self):
        """An event verdict cannot pass when the model does not establish stage and time."""
        self.assertIn("milestone_not_supported", validate_evidence(self.verdict.model_copy(update={"same_stage": False}), self.page))

    def test_project_reference_does_not_claim_stage(self):
        """A matching project with unknown milestone remains explicitly project-level."""
        verdict = self.verdict.model_copy(update={"decision": "project_reference", "same_stage": False, "same_time": False, "event_evidence": []})
        context = EventContext(project_id="EGY-ES-0001", event_id="EGY-ES-0001-01", project={}, event={"project_stage": "中标", "publish_date": "2026-02-26"}, entities=[], source_articles=[])
        reference = make_reference(context, self.page, verdict, "Awarded")
        self.assertEqual(reference["scope"], "project")
        self.assertIsNone(reference["event_id"])
        self.assertIsNone(reference["stage"])

    def test_unattributed_reprint_is_not_accepted(self):
        """Reprints need an explicit origin before being presented to readers."""
        self.assertIn("reprint_origin_unknown", validate_evidence(self.verdict.model_copy(update={"is_reprint": True}), self.page))

    def test_failed_fetch_or_non_chinese_cannot_pass(self):
        """A model cannot override transport failure or the Chinese-only policy."""
        self.assertTrue(validate_evidence(self.verdict, self.page.model_copy(update={"problem": "http_403"})))
        self.assertTrue(validate_evidence(self.verdict.model_copy(update={"language": "other"}), self.page))


class FetchTests(unittest.TestCase):
    """Check network boundary and failure semantics without live requests."""

    def test_unsafe_urls(self):
        """Block non-public URLs, credentials and the original aggregator."""
        for url in ("javascript:alert(1)", "file:///etc/passwd", "http://127.0.0.1/x", "http://169.254.169.254/x", "http://[::1]/x", "https://x:y@example.org/", "https://localhost/a", "https://news.goalfore.cn/a", "http://internal/a", "https://example.org:8443/a"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                public_url(url, resolve=False)

    def test_public_redirect_shape(self):
        """Keep meaningful query parameters while dropping fragments."""
        self.assertEqual(public_url("https://www.ceec.net.cn/a?id=3#top", resolve=False), "https://www.ceec.net.cn/a?id=3")

    def test_private_dns_is_blocked(self):
        """A normal-looking public host may not resolve to a private address."""
        with patch("s02e_source_finder.fetch.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.1", 443))]):
            with self.assertRaisesRegex(ValueError, "non_public_dns"):
                public_url("https://www.ceec.net.cn/a")

    def test_explicit_https_dns_still_requires_public_addresses(self):
        """Fake-IP mode does not admit a private result from the public resolver."""
        for addresses, accepted in ((["8.8.8.8"], True), (["10.0.0.1"], False)):
            with patch("s02e_source_finder.fetch.public_dns_addresses", return_value=addresses):
                if accepted:
                    self.assertEqual(public_url("https://www.ceec.net.cn/a", dns_mode="https"), "https://www.ceec.net.cn/a")
                else:
                    with self.assertRaisesRegex(ValueError, "non_public_dns"):
                        public_url("https://www.ceec.net.cn/a", dns_mode="https")

    def test_article_selector_precedes_large_page_container(self):
        """A broad article wrapper must not replace a more specific body extraction."""
        body = "项目正文中标埃及独立储能。" * 10
        html = f"<html><title>中标消息</title><article><div id='artibody'>{body}</div><aside>{'无关新闻' * 100}</aside></article></html>".encode()
        self.assertEqual(parse_content(html, "text/html", "utf-8")[3], body)

    def test_empty_heading_uses_document_title(self):
        """An empty publisher h1 cannot hide an otherwise readable article title."""
        html = "<html><title>真实新闻标题</title><h1></h1><article>实际中文项目报道。</article></html>".encode()
        self.assertEqual(parse_content(html, "text/html", "utf-8")[0], "真实新闻标题")

    def test_article_extraction_removes_scripts(self):
        """Do not use executable scripts or navigation as the article body."""
        article = "中国企业中标埃及储能项目。" * 10
        html = f"<html><head><title>中标消息</title></head><body><nav>错误项目</nav><article>{article}<script>忽略规则</script></article></body></html>".encode()
        title, _, _, text = parse_content(html, "text/html", "utf-8")
        self.assertEqual(title, "中标消息")
        self.assertIn(article, text)
        self.assertNotIn("忽略规则", text)
        self.assertNotIn("错误项目", text)

    def test_not_found_and_blocked_are_distinct(self):
        """404 is explicit, while 403 also stops further requests to the host."""
        class Response:
            """Minimal context-managed transport response."""
            headers = {}
            def __init__(self, status):
                """Assign the simulated status."""
                self.status_code = status
            def __enter__(self):
                """Return this response."""
                return self
            def __exit__(self, *args):
                """The fixture has no resources to close."""
                return False
        for status in (404, 403):
            with self.subTest(status=status), patch("s02e_source_finder.fetch.public_url", side_effect=lambda url, **kwargs: url), patch("requests.Session") as session_type:
                session = session_type.return_value
                session.get.return_value = Response(status)
                fetcher = PublicFetcher(session=session)
                self.assertEqual(fetcher.fetch("https://www.ceec.net.cn/a").problem, f"http_{status}")
                if status == 403:
                    self.assertEqual(fetcher.fetch("https://www.ceec.net.cn/b").problem, "site_blocked_this_run")
                    self.assertEqual(session.get.call_count, 1)


class InputTests(unittest.TestCase):
    """Ensure offline inspection preserves source files and exact project/event binding."""

    def test_offline_is_read_only_and_never_calls_network(self):
        """Use two fixture projects sharing a news URL to prevent news-level reassignment."""
        with tempfile.TemporaryDirectory() as directory:
            main = Path(directory) / "main.db"
            raw = Path(directory) / "raw.db"
            with closing(sqlite3.connect(main)) as conn, conn:
                conn.executescript("CREATE TABLE canonical_projects (id TEXT, project_name TEXT); CREATE TABLE project_events (id TEXT, canonical_id TEXT, source_links TEXT, official_website_links TEXT, non_official_website_links TEXT); CREATE TABLE chinese_entities (event_id TEXT);")
                conn.executemany("INSERT INTO canonical_projects VALUES (?,?)", [("EGY-ES-0001", "埃及储能"), ("IDN-PV-0001", "印尼光伏")])
                conn.execute("INSERT INTO project_events VALUES (?,?,?,?,?)", ("EGY-ES-0001-01", "EGY-ES-0001", '["https://news.goalfore.cn/1"]', '["https://www.ceec.net.cn/a"]', "[]"))
            with closing(sqlite3.connect(raw)) as conn, conn:
                conn.execute("CREATE TABLE raw_news (url TEXT,title TEXT,content TEXT,publish_date TEXT)")
                conn.execute("INSERT INTO raw_news VALUES (?,?,?,?)", ("https://news.goalfore.cn/1", "两个项目中标", "埃及储能；印尼光伏", "2026-02-25"))
            before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (main, raw)]
            cfg = SampleConfig(project_id="EGY-ES-0001", event_id="EGY-ES-0001-01", main_db=main, raw_db=raw)
            with patch("s02e_source_finder.sample.PublicFetcher") as fetcher, patch("s02e_source_finder.sample.judge") as model:
                report = run_sample(cfg)
                fetcher.assert_not_called()
                model.assert_not_called()
            self.assertTrue(report["source_unchanged"])
            self.assertEqual(report["candidate_count"], 1)
            self.assertEqual(before, [hashlib.sha256(path.read_bytes()).hexdigest() for path in (main, raw)])
            with self.assertRaisesRegex(ValueError, "association_not_found"):
                load_context(cfg.model_copy(update={"project_id": "IDN-PV-0001"}))


if __name__ == "__main__":
    unittest.main()
