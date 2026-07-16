# main.py：CLI 入口——crawl（发现新 URL → 抓文章页 → 入库 → 健康报告）与 health-report
import argparse
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .article import extract_article
from .config import load_site_configs
from .discover import DISCOVERERS
from .fetcher import Fetcher, SiteBlockedError
from .health import generate_report
from .state import DATA_DIR, baseline_urls, connect, log_run, save_articles, save_baseline, seen_urls

MAX_ARTICLES_PER_SITE = 300  # 熔断：单站单轮最多抓的文章页数


def _setup_logging() -> None:
    """logging 双通道：UTF-8 文件 + 控制台；关键节点 print 有 logging 备份"""
    if hasattr(sys.stdout, "reconfigure"):  # 防非 UTF-8 控制台打印崩溃（s01a GBK 教训）
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    file_h = logging.FileHandler(log_dir / f"crawl_{datetime.now(timezone.utc):%Y%m%d}.log", encoding="utf-8")
    file_h.setFormatter(fmt)
    console_h = logging.StreamHandler()
    console_h.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[file_h, console_h])


def crawl_site(cfg, conn, run_id: str, baseline: bool, dry_run: bool, max_articles: int,
               fetch_baseline: bool = False, days_back: int | None = None) -> dict:
    """抓取单个站点：发现 URL → 与 seen 求差 →（baseline 只记录 / 否则抓文章页入库）；
    fetch_baseline 模式跳过发现，直接补抓库里 baseline 状态的文章页"""
    logger = logging.getLogger(f"crawl.{cfg.name}")
    t0 = time.monotonic()
    fetcher = _make_fetcher(cfg)
    stats = {"errors": 0, "note": ""}
    try:
        _run_site(cfg, conn, run_id, baseline, dry_run, max_articles, fetch_baseline, days_back, fetcher, stats, logger, t0)
    finally:
        fetcher.close()  # 确保 headless 浏览器一定被关闭
    return stats


def _make_fetcher(cfg):
    """按 cfg.render 选择 HTTP 层：http→curl_cffi Fetcher，headless→Playwright HeadlessFetcher"""
    if cfg.render == "headless":
        from .headless import HeadlessFetcher  # 延迟导入：只有 headless 站才需要 playwright
        return HeadlessFetcher(cfg.name, cfg.rate_limit_seconds)
    return Fetcher(cfg.name, cfg.rate_limit_seconds)


def _run_site(cfg, conn, run_id, baseline, dry_run, max_articles, fetch_baseline, days_back, fetcher, stats, logger, t0):
    """站点抓取主流程（fetcher 生命周期由 crawl_site 的 try/finally 管理）"""
    if fetch_baseline:
        new = baseline_urls(conn, cfg.name)
        stats.update({"listing_pages": 0, "empty_pages": 0, "urls_discovered": len(new), "note": "fetch-baseline"})
        logger.info("补抓 baseline 文章 %d 篇", len(new))
    else:
        try:
            if cfg.parser == "sitemap_daily" and days_back:
                urls, pages, empty = DISCOVERERS[cfg.parser](cfg, fetcher, days_back=days_back)
            else:
                urls, pages, empty = DISCOVERERS[cfg.parser](cfg, fetcher)
        except SiteBlockedError:
            stats.update({"errors": 1, "note": "403 blocked", "duration_s": time.monotonic() - t0})
            return
        stats.update({"listing_pages": pages, "empty_pages": empty, "urls_discovered": len(urls)})
        known = seen_urls(conn, cfg.name)
        new = [u for u in urls if u not in known]
        logger.info("发现 %d 个 URL，其中新 %d 个", len(urls), len(new))
    stats["new_urls"] = len(new)

    if dry_run:
        stats["note"] = "dry-run"
        for u in new[:10]:
            logger.info("  [新] %s", u)
    elif baseline or cfg.discover_only:
        added = save_baseline(conn, cfg.name, new)
        stats["note"] = ("baseline: " if baseline else "discover-only: ") + f"{added} URLs 登记"
    else:
        if len(new) > max_articles:
            logger.error("新 URL 数 %d 超过熔断上限 %d，本轮只抓前 %d 个（请检查是否异常）", len(new), max_articles, max_articles)
            stats["note"] = f"circuit-break: {len(new)} > {max_articles}"
            new = new[:max_articles]
        records = []
        for u in new:
            resp = fetcher.get(u, headers={"Referer": str(cfg.listing_urls[0]) if cfg.listing_urls else u})
            if resp is None:
                records.append({"url": u, "status": "error"})
                stats["errors"] += 1
                continue
            fields = extract_article(resp.text, cfg.article, url=u)
            records.append({"url": u, "status": "fetched", **fields})
        if records:
            save_articles(conn, cfg.name, records)
        stats["articles_fetched"] = sum(1 for r in records if r["status"] == "fetched")

    stats["duration_s"] = round(time.monotonic() - t0, 1)
    if not dry_run:
        log_run(conn, run_id, cfg.name, stats)


def main_crawl() -> None:
    """crawl 命令：默认全站增量；--baseline 首跑建水位线；--dry-run 只报告不写库"""
    parser = argparse.ArgumentParser(description="s01a2 多源爬虫")
    parser.add_argument("--site", help="只跑指定站点（config/sites 里的 name）")
    parser.add_argument("--baseline", action="store_true", help="首跑：登记发现的 URL 为水位线，不抓文章页")
    parser.add_argument("--fetch-baseline", action="store_true", help="补抓库中 baseline 状态的文章页（不做发现）")
    parser.add_argument("--days-back", type=int, default=None, help="sitemap_daily 站点回看天数（默认 8）")
    parser.add_argument("--dry-run", action="store_true", help="只发现与报告，不写库不抓文章")
    parser.add_argument("--max-articles", type=int, default=MAX_ARTICLES_PER_SITE, help="单站单轮文章页熔断上限")
    args = parser.parse_args()
    if args.baseline and args.fetch_baseline:
        parser.error("--baseline 与 --fetch-baseline 互斥")

    _setup_logging()
    logger = logging.getLogger("crawl")
    configs = load_site_configs(only=args.site)
    conn = connect()
    run_id = f"{datetime.now(timezone.utc):%Y%m%d%H%M}_{uuid.uuid4().hex[:6]}"
    logger.info("运行 %s 开始，站点: %s，baseline=%s dry_run=%s", run_id, [c.name for c in configs], args.baseline, args.dry_run)

    failures = 0
    for cfg in configs:  # 站间隔离：单站崩溃不影响其他站
        try:
            stats = crawl_site(cfg, conn, run_id, args.baseline, args.dry_run, args.max_articles,
                               fetch_baseline=args.fetch_baseline, days_back=args.days_back)
            print(f"[{cfg.name}] 发现 {stats.get('urls_discovered', 0)} / 新 {stats.get('new_urls', 0)}"
                  f" / 入库 {stats.get('articles_fetched', 0)} / 错误 {stats.get('errors', 0)} ({stats.get('note', '')})")
        except Exception as e:  # 站间隔离兜底：具体异常已在下层记录，这里防单站中断全局
            logging.getLogger(f"crawl.{cfg.name}").error("站点抓取失败: %s", e, exc_info=True)
            failures += 1

    alerts = 0
    if not args.dry_run:
        _, alerts = generate_report(conn, DATA_DIR / "reports")
    conn.close()
    if failures or alerts:
        print(f"完成，但有 {failures} 个站点失败 / {alerts} 条健康告警（详见 data/reports 与 logs）")
        sys.exit(1)
    print("完成，无告警")


def main_health() -> None:
    """health-report 命令：仅从现有 fetch_log 重新生成健康报告"""
    _setup_logging()
    conn = connect()
    path, alerts = generate_report(conn, DATA_DIR / "reports")
    conn.close()
    print(f"报告: {path}（告警 {alerts} 条）")
    sys.exit(1 if alerts else 0)
