# health.py：从 fetch_log 生成健康报告（markdown），异常站点高亮并返回告警数
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

STALE_DAYS = 14  # 连续零新增告警阈值


def generate_report(conn: sqlite3.Connection, out_dir: Path) -> tuple[Path, int]:
    """生成 health_report_{date}.md；返回 (报告路径, 告警条数)"""
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    alerts: list[str] = []
    lines = [f"# 爬虫健康报告 {today}", ""]

    sites = [r[0] for r in conn.execute("SELECT DISTINCT site FROM fetch_log ORDER BY site")]
    lines.append("| 站点 | 最近一次运行 | 新URL | 抓取文章 | 错误 | 空列表页 | 距上次有新增(天) |")
    lines.append("|---|---|---|---|---|---|---|")
    for site in sites:
        last = conn.execute(
            "SELECT ts, new_urls, articles_fetched, errors, empty_pages, urls_discovered FROM fetch_log"
            " WHERE site = ? ORDER BY id DESC LIMIT 1",
            (site,),
        ).fetchone()
        last_new = conn.execute(
            "SELECT MAX(ts) FROM fetch_log WHERE site = ? AND new_urls > 0", (site,)
        ).fetchone()[0]
        days_since_new = "-"
        if last_new:
            delta = datetime.now(timezone.utc) - datetime.fromisoformat(last_new)
            days_since_new = str(delta.days)
            if delta.days >= STALE_DAYS:
                alerts.append(f"{site}: 连续 {delta.days} 天无新增（改版或被封?）")
        else:
            alerts.append(f"{site}: 从未有过新增")
        ts, new_urls, fetched, errors, empty, discovered = last
        if errors and errors > 0:
            alerts.append(f"{site}: 最近一次运行有 {errors} 个错误")
        # 真·改版/失效信号 = 整站发现 0 个 URL；"有空页但仍发现到 URL"（如 sitemap 站部分日未生成、或已追平）不算
        if (discovered or 0) == 0:
            alerts.append(f"{site}: 本轮发现 0 个 URL（选择器失效/改版/被封信号）")
        lines.append(f"| {site} | {ts} | {new_urls} | {fetched} | {errors} | {empty} | {days_since_new} |")

    lines.append("")
    if alerts:
        lines.append("## ⚠️ 告警")
        lines.extend(f"- {a}" for a in alerts)
    else:
        lines.append("## ✅ 无告警")

    path = out_dir / f"health_report_{today}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("健康报告已生成: %s（告警 %d 条）", path, len(alerts))
    return path, len(alerts)
