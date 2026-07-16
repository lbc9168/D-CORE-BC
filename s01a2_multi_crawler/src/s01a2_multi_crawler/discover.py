# discover.py：三种"列表 → 文章 URL 集合"的发现器（html_list / jpaas / sitemap_daily）
import json
import logging
import re
from datetime import date, timedelta
from urllib.parse import urljoin

from .config import SiteConfig
from .fetcher import Fetcher

logger = logging.getLogger(__name__)


def _extract_article_urls(html: str, base_url: str, pattern: str) -> list[str]:
    """从 HTML 中提取所有 href，按正则筛出文章 URL（相对路径转绝对），保序去重"""
    urls: list[str] = []
    seen: set[str] = set()
    for href in re.findall(r'href="([^"]+)"', html):
        absolute = urljoin(base_url, href.strip())
        if re.search(pattern, absolute) and absolute not in seen:
            seen.add(absolute)
            urls.append(absolute)
    return urls


def discover_html_list(cfg: SiteConfig, fetcher: Fetcher) -> tuple[list[str], int, int]:
    """通用服务端渲染列表：逐列表 URL × 逐页抓取，返回 (文章URL列表, 抓取页数, 空页数)"""
    all_urls: list[str] = []
    pages_fetched = 0
    empty_pages = 0
    for listing in cfg.listing_urls:
        listing = str(listing)
        for n in range(1, cfg.max_pages + 1):
            url = listing if n == 1 or not cfg.page_param else listing + cfg.page_param.format(n=n)
            if n > 1 and not cfg.page_param:
                break  # 无分页配置只抓第1页
            resp = fetcher.get(url, headers={"Referer": listing})
            pages_fetched += 1
            if resp is None:
                empty_pages += 1
                continue
            found = _extract_article_urls(resp.text, url, cfg.article_url_pattern)
            if not found:
                empty_pages += 1
                logger.warning("[%s] 列表页 0 命中（改版信号?）: %s", cfg.name, url)
            all_urls.extend(u for u in found if u not in set(all_urls))
    return all_urls, pages_fetched, empty_pages


# —— 大汉 JPaaS（powerchina 等政企站）：壳页取 queryData → unit API 翻页 ——
_JPAAS_SCRIPT_RE = re.compile(
    r'src="[^"]*unitbuild\.js[^"]*"[^>]*url="([^"]+)"[^>]*queryData="([^"]+)"', re.S
)


def discover_jpaas(cfg: SiteConfig, fetcher: Fetcher) -> tuple[list[str], int, int]:
    """大汉 JPaaS 列表：运行时从壳页提取 queryData（改版自愈），调 unit API 逐页取 HTML 片段"""
    all_urls: list[str] = []
    pages_fetched = 0
    empty_pages = 0
    for shell_url in cfg.listing_urls:
        shell_url = str(shell_url)
        resp = fetcher.get(shell_url)
        pages_fetched += 1
        if resp is None:
            empty_pages += 1
            continue
        m = _JPAAS_SCRIPT_RE.search(resp.text)
        if m is None:
            empty_pages += 1
            logger.error("[%s] 壳页未找到 JPaaS queryData（站点可能改版）: %s", cfg.name, shell_url)
            continue
        api_url = urljoin(shell_url, m.group(1))
        query_data = json.loads(m.group(2).replace("'", '"'))
        query_data.pop("editType", None)  # 实测带 editType 且带 paramJson 时接口报错
        headers = {"Referer": shell_url, "X-Requested-With": "XMLHttpRequest"}
        for n in range(1, cfg.max_pages + 1):
            params = dict(query_data)
            if n > 1:
                params["paramJson"] = json.dumps({"pageNo": n, "pageSize": 10}, separators=(",", ":"))
            api_resp = fetcher.get(api_url, headers=headers, params=params)
            pages_fetched += 1
            if api_resp is None:
                empty_pages += 1
                continue
            try:
                payload = api_resp.json()
            except (json.JSONDecodeError, ValueError) as e:
                logger.error("[%s] unit API 响应非 JSON: %s (%s)", cfg.name, api_url, e)
                empty_pages += 1
                continue
            html = (payload.get("data") or {}).get("html") or ""
            if not payload.get("success") or not html:
                logger.warning("[%s] unit API success=false（第%d页）", cfg.name, n)
                empty_pages += 1
                continue
            found = _extract_article_urls(html, shell_url, cfg.article_url_pattern)
            if not found:
                empty_pages += 1
            all_urls.extend(u for u in found if u not in set(all_urls))
    return all_urls, pages_fetched, empty_pages


def discover_sitemap_daily(cfg: SiteConfig, fetcher: Fetcher, days_back: int = 8) -> tuple[list[str], int, int]:
    """按日 sitemap（yidaiyilu）：拉最近 days_back 天的 /article/YYYY-MM-DD.xml，汇总文章 URL"""
    all_urls: list[str] = []
    pages_fetched = 0
    empty_pages = 0
    index_url = str(cfg.sitemap_index)
    base = index_url.rsplit("/", 1)[0]  # .../websitemap
    today = date.today()
    for offset in range(days_back):
        day = today - timedelta(days=offset)
        url = f"{base}/article/{day.isoformat()}.xml"
        resp = fetcher.get(url)
        pages_fetched += 1
        if resp is None:  # 当天还没生成属正常，404 已由 fetcher 记 warning
            empty_pages += 1
            continue
        locs = re.findall(r"<loc>([^<]+)</loc>", resp.text)
        found = [u.strip() for u in locs if re.search(cfg.article_url_pattern, u)]
        if not found:
            empty_pages += 1
        all_urls.extend(u for u in found if u not in set(all_urls))
    return all_urls, pages_fetched, empty_pages


DISCOVERERS = {
    "html_list": discover_html_list,
    "jpaas": discover_jpaas,
    "sitemap_daily": discover_sitemap_daily,
}
