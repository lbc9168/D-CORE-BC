# 发现器测试：用真实站点页面 fixture 验证 URL 提取与 JPaaS 参数抽取
import re
from pathlib import Path

from s01a2_multi_crawler.config import load_site_configs
from s01a2_multi_crawler.discover import _JPAAS_SCRIPT_RE, _extract_article_links


def _extract_article_urls(html, base_url, pattern):
    """测试辅助：从 _extract_article_links 结果取 URL 列表"""
    return [it["url"] for it in _extract_article_links(html, base_url, pattern)]

FIXTURES = Path(__file__).parent / "fixtures"


def _cfg(name: str):
    return load_site_configs(only=name)[0]


def _read(fname: str) -> str:
    return (FIXTURES / fname).read_text(encoding="utf-8", errors="replace")


def test_extract_powerchina_intl():
    """电建国际列表页：提取到 /show/ 文章绝对 URL"""
    urls = _extract_article_urls(_read("powerchina_intl.html"), "https://www.powerchina-intl.com/list/9.html",
                                 _cfg("powerchina_intl").article_url_pattern)
    assert len(urls) >= 5
    assert all(u.startswith("https://www.powerchina-intl.com/show/") for u in urls)


def test_extract_seetao():
    """见道网频道页：提取到 50+ details 链接"""
    urls = _extract_article_urls(_read("seetao_list19.html"), "https://www.seetao.com/list/19.html",
                                 _cfg("seetao").article_url_pattern)
    assert len(urls) >= 40
    assert all(re.search(r"/details/\d+\.html$", u) for u in urls)


def test_extract_imsilkroad():
    """新华丝路标签页：提取到 /news/p/ 链接"""
    urls = _extract_article_urls(_read("imsilkroad_tag.html"), "https://www.imsilkroad.com/tags/x",
                                 _cfg("imsilkroad").article_url_pattern)
    assert len(urls) >= 20


def test_extract_solarbe():
    """索比国际频道：提取到日期型文章 URL"""
    urls = _extract_article_urls(_read("solarbe.html"), "https://news.solarbe.com/guoji/",
                                 _cfg("solarbe").article_url_pattern)
    assert len(urls) >= 10


def test_extract_yidaiyilu_sitemap():
    """一带一路网按日 sitemap：<loc> 提取 + 文章 pattern 过滤"""
    xml = _read("yidaiyilu_day.xml")
    locs = re.findall(r"<loc>([^<]+)</loc>", xml)
    pat = _cfg("yidaiyilu").article_url_pattern
    matched = [u for u in locs if re.search(pat, u)]
    assert len(matched) >= 15


def test_extract_links_carry_title():
    """列表页提取应带出锚文本标题（discover_only 站免抓正文即得标题）"""
    items = _extract_article_links(_read("seetao_list19.html"), "https://www.seetao.com/list/19.html",
                                   _cfg("seetao").article_url_pattern)
    assert items and all("url" in it and "title" in it for it in items)
    assert any(it["title"] for it in items)  # 至少部分条目有非空标题


def test_jpaas_querydata_extraction():
    """电建壳页：能提取 unit API 地址与 queryData 参数（运行时自愈的关键）"""
    m = _JPAAS_SCRIPT_RE.search(_read("powerchina.html"))
    assert m is not None
    import json
    qd = json.loads(m.group(2).replace("'", '"'))
    assert qd["pageId"] == "7449"
    assert qd["webId"] == "85"
    assert "tplSetId" in qd
