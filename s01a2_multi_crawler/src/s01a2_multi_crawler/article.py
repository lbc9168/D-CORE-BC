# article.py：文章页统一提取——标题（来源可按站覆盖）、日期（HTML 正则 + URL 兜底）、正文（选择器候选+最大文本块兜底）
import logging
import re

from bs4 import BeautifulSoup

from .config import ArticleRules

logger = logging.getLogger(__name__)


def _normalize_date(groups: tuple) -> str | None:
    """把 (y, m, d) 字符串组规整为 YYYY-MM-DD；越界返回 None"""
    try:
        y, mo, d = (int(g) for g in groups[:3])
    except (ValueError, IndexError):
        return None
    if 2000 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
        return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def extract_article(html: str, rules: ArticleRules, url: str = "") -> dict:
    """从文章页 HTML 提取 {title, publish_date, content_text}；提取失败的字段为 None"""
    soup = BeautifulSoup(html, "html.parser")

    # 标题：title_tag 模式只信 <title>；auto 模式 og:title > h1 > <title>
    title = None
    if rules.title_source == "auto":
        og = soup.find("meta", attrs={"property": "og:title"})
        if og and og.get("content"):
            title = og["content"].strip()
        if not title and soup.h1:
            title = soup.h1.get_text(strip=True)
    if not title and soup.title:
        title = soup.title.get_text(strip=True)
    if title and rules.title_strip:
        title = re.sub(rules.title_strip, "", title).strip() or title

    # 日期：先 HTML 正则，无命中再从 URL 提取
    publish_date = None
    m = re.search(rules.date_pattern, html)
    if m:
        publish_date = _normalize_date(m.groups())
    if publish_date is None and rules.url_date_pattern and url:
        m = re.search(rules.url_date_pattern, url)
        if m:
            publish_date = _normalize_date(m.groups())

    # 正文：依次尝试配置的选择器；全失败则取文本量最大的 div/article
    content_text = None
    for selector in rules.content_selectors:
        node = soup.select_one(selector)
        if node:
            text = node.get_text("\n", strip=True)
            if len(text) >= 50:
                content_text = text
                break
    if content_text is None:
        candidates = soup.find_all(["article", "div"])
        best = max(candidates, key=lambda n: len(n.get_text(strip=True)), default=None) if candidates else None
        if best is not None:
            content_text = best.get_text("\n", strip=True)

    return {"title": title, "publish_date": publish_date, "content_text": content_text}
