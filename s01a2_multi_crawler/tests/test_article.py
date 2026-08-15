# 文章页提取测试：标题优先级、日期规整、正文选择器与兜底
from s01a2_multi_crawler.article import extract_article
from s01a2_multi_crawler.config import ArticleRules

HTML = """
<html><head><title>页面标题 - 网站名</title>
<meta property="og:title" content="OG标题"/></head>
<body><h1>H1标题</h1><span>发布时间：2026年7月15日</span>
<div class="content">这是正文段落，需要超过五十个字符才会被选择器采纳。这是正文段落，需要超过五十个字符才会被选择器采纳。</div>
<div class="sidebar">边栏</div></body></html>
"""


def test_extract_full():
    r = extract_article(HTML, ArticleRules(content_selectors=[".content"]))
    assert r["title"] == "OG标题"
    assert r["publish_date"] == "2026-07-15"
    assert "正文段落" in r["content_text"]


def test_fallback_no_selector():
    """选择器不命中时退回最大文本块"""
    r = extract_article(HTML, ArticleRules(content_selectors=[".nope"]))
    assert r["content_text"] and "正文段落" in r["content_text"]


def test_title_fallback_h1():
    html = HTML.replace('<meta property="og:title" content="OG标题"/>', "")
    r = extract_article(html, ArticleRules())
    assert r["title"] == "H1标题"


def test_title_tag_source_with_strip():
    """title_source=title_tag 时无视 og/h1，只用 <title> 并剥离栏目链后缀"""
    r = extract_article(HTML, ArticleRules(title_source="title_tag", title_strip=r"\s+-\s+.*$"))
    assert r["title"] == "页面标题"


def test_url_date_fallback():
    """HTML 无日期时从 URL 提取（索比场景）"""
    html = "<html><title>t</title><body><div>正文" + "字" * 60 + "</div></body></html>"
    r = extract_article(html, ArticleRules(url_date_pattern=r"/(\d{4})(\d{2})/(\d{2})/"),
                        url="https://news.solarbe.com/202607/15/50025936.html")
    assert r["publish_date"] == "2026-07-15"
