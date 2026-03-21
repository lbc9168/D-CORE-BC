import os
import random
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Type

import pandas as pd
import requests
from bs4 import BeautifulSoup, Tag
from crewai.tools import BaseTool
from pydantic import BaseModel, Field


_S01A_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_RAW_NEWS_DB = str(_S01A_ROOT / "data" / "raw_news.db")

# 请求参数
REQUEST_TIMEOUT = 20       # 单次请求超时（秒），适当放宽
MAX_RETRIES = 4            # 最大重试次数
RETRY_BASE_DELAY = 3       # 指数退避基础等待秒数（×2^attempt）
SLEEP_MIN = 1.0            # 翻页/详情页间隔最小秒数
SLEEP_MAX = 3.0            # 翻页/详情页间隔最大秒数（加 jitter）
LIST_PAGE_MAX_ERRORS = 5   # 列表页连续失败多少次后放弃

# User-Agent 轮换列表（模拟不同浏览器/系统，降低爬虫特征识别率）
_USER_AGENTS = [
    # Chrome - Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    # Chrome - macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    # Firefox - Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    # Firefox - macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.2; rv:122.0) Gecko/20100101 Firefox/122.0",
    # Edge - Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
]


def _init_raw_news_db(db_path: str) -> sqlite3.Connection:
    """初始化原始新闻备案数据库（跨批次持久化）。"""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS raw_news (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            url                   TEXT UNIQUE,
            title                 TEXT,
            content               TEXT,
            author                TEXT,
            publish_date          TEXT,
            crawl_time            TEXT,
            passed_keyword_filter INTEGER DEFAULT 0,
            batch_id              TEXT
        )
    """)
    # 断点续爬：记录每个 batch 最后成功爬完的页码
    conn.execute("""
        CREATE TABLE IF NOT EXISTS crawler_checkpoints (
            batch_id   TEXT PRIMARY KEY,
            last_page  INTEGER,
            updated_at TEXT
        )
    """)
    conn.commit()
    return conn


def _save_checkpoint(conn: sqlite3.Connection, batch_id: str, page: int) -> None:
    conn.execute(
        """INSERT INTO crawler_checkpoints (batch_id, last_page, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(batch_id) DO UPDATE SET last_page=excluded.last_page, updated_at=excluded.updated_at""",
        (batch_id, page, datetime.now().isoformat()),
    )
    conn.commit()


def _load_checkpoint(conn: sqlite3.Connection, batch_id: str) -> Optional[int]:
    row = conn.execute(
        "SELECT last_page FROM crawler_checkpoints WHERE batch_id = ?", (batch_id,)
    ).fetchone()
    return row[0] if row else None


class NewsCrawlerInput(BaseModel):
    source_url: str = Field(
        ...,
        description="新闻列表页基础 URL，不含分页参数。例：https://news.goalfore.cn/topstories/project",
    )
    start_date: str = Field(..., description="抓取起始日期，格式 YYYY-MM-DD")
    end_date: str = Field(..., description="抓取截止日期，格式 YYYY-MM-DD")
    keywords: str = Field(
        ...,
        description="关键词列表，用英文逗号分隔，文章正文包含任意一个即保留。例：能源,电力,MW,GW",
    )
    start_page: int = Field(
        default=1,
        description="起始页码（默认 1；恢复中断任务时填入断点页码）",
    )
    output_dir: str = Field(
        default="data/raw",
        description="输出 Excel 文件的保存目录（相对于运行目录）",
    )
    raw_news_db_path: str = Field(
        default=_DEFAULT_RAW_NEWS_DB,
        description="原始新闻备案数据库路径（SQLite，跨批次持久化，URL 唯一去重）",
    )


class NewsCrawlerTool(BaseTool):
    name: str = "news_crawler_tool"
    description: str = (
        "从指定新闻网站按日期范围抓取新闻，并按关键词初步过滤后保存为 SQLite 文件。"
        "返回保存的文件完整路径和抓取数量摘要。"
    )
    args_schema: Type[BaseModel] = NewsCrawlerInput

    # ── 内部工具方法 ──────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {"User-Agent": random.choice(_USER_AGENTS)}

    def _request_with_retry(self, url: str, label: str = "") -> Optional[requests.Response]:
        """带指数退避重试的 GET 请求。失败 MAX_RETRIES 次后返回 None。"""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = requests.get(url, headers=self._headers(), timeout=REQUEST_TIMEOUT)
                return resp
            except requests.exceptions.Timeout:
                wait = RETRY_BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 2)
                print(f"    [超时{attempt}/{MAX_RETRIES}] {label or url[:60]} — {wait:.1f}s 后重试...")
                if attempt < MAX_RETRIES:
                    time.sleep(wait)
            except requests.exceptions.ConnectionError as e:
                wait = RETRY_BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 2)
                print(f"    [连接错误{attempt}/{MAX_RETRIES}] {e} — {wait:.1f}s 后重试...")
                if attempt < MAX_RETRIES:
                    time.sleep(wait)
            except Exception as e:
                print(f"    [请求异常] {label or url[:60]}: {e}")
                return None
        print(f"    [放弃] {label or url[:60]} 已失败 {MAX_RETRIES} 次")
        return None

    def _get_article_details(self, url: str) -> tuple:
        """抓取详情页，返回 (title, author, publish_time, content)"""
        resp = self._request_with_retry(url, label="详情页")
        if resp is None:
            return "NA", "NA", "NA", "NA"
        try:
            resp.encoding = resp.apparent_encoding
            soup = BeautifulSoup(resp.text, "html.parser")

            title_tag = soup.select_one("div.list_top h3")
            title = title_tag.get_text(strip=True) if title_tag else "未找到标题"

            content_div = soup.find("div", class_="detail-content")
            content = (
                content_div.get_text(separator="\n", strip=True)
                if content_div
                else "NA"
            )

            author, publish_time = "NA", "NA"
            list_top_div = soup.find("div", class_="list_top")
            if isinstance(list_top_div, Tag):
                for candidate in list_top_div.find_all("div", class_="pull-left"):
                    text = candidate.get_text(strip=True)
                    if "首页" in text or "走出去" in text:
                        continue
                    if re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", text):
                        date_match = re.search(
                            r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", text
                        )
                        if date_match:
                            publish_time = date_match.group()
                        author = (
                            text.replace("发布时间：", "")
                            .replace(publish_time, "")
                            .replace("来源：", "")
                            .strip()
                        )
                        break

            return title, author, publish_time, content

        except Exception as e:
            print(f"    [详情页解析错误] {url}: {e}")
            return "NA", "NA", "NA", "NA"

    # ── 主执行逻辑 ────────────────────────────────────────────────────────────

    def _run(
        self,
        source_url: str,
        start_date: str,
        end_date: str,
        keywords: str,
        start_page: int = 1,
        output_dir: str = "data/raw",
        raw_news_db_path: str = _DEFAULT_RAW_NEWS_DB,
        save_excel: bool = True,
    ) -> str:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        keyword_list = [k.strip() for k in keywords.split(",") if k.strip()]

        os.makedirs(output_dir, exist_ok=True)

        # 初始化原始新闻备案 DB（跨批次持久化）
        raw_db = _init_raw_news_db(raw_news_db_path)
        batch_id = f"{start_date}_{end_date}"

        # 断点续爬：优先使用 DB 中记录的断点页，其次使用参数 start_page
        saved_page = _load_checkpoint(raw_db, batch_id)
        if saved_page and saved_page > start_page:
            page = saved_page
            print(f">>> 发现断点记录，从第 {page} 页继续抓取")
        else:
            page = start_page

        all_data = []
        stop_crawling = False
        consecutive_old_count = 0
        consecutive_list_errors = 0
        last_valid_date = None

        print(f">>> 任务启动：爬取 {start_date} 至 {end_date} 的新闻，起始页 {page}")
        print(f">>> 关键词过滤：{keyword_list}")

        while not stop_crawling:
            list_url = f"{source_url}?page={page}"
            print(f"\n正在扫描第 {page} 页...")

            resp = self._request_with_retry(list_url, label=f"列表页 p{page}")

            if resp is None:
                consecutive_list_errors += 1
                print(f"  [列表页失败] 连续失败 {consecutive_list_errors}/{LIST_PAGE_MAX_ERRORS} 次，跳过本页")
                if consecutive_list_errors >= LIST_PAGE_MAX_ERRORS:
                    print(f">>> 列表页连续失败 {LIST_PAGE_MAX_ERRORS} 次，终止任务。")
                    break
                page += 1
                time.sleep(random.uniform(SLEEP_MIN * 2, SLEEP_MAX * 2))
                continue

            consecutive_list_errors = 0  # 成功则重置计数

            try:
                soup = BeautifulSoup(resp.text, "html.parser")
                list_items = soup.find_all("li", class_="list")
            except Exception as e:
                print(f"  [列表页解析错误] {e}，跳过本页")
                page += 1
                time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))
                continue

            if not list_items:
                print("未发现更多条目，停止爬取。")
                break

            for item in list_items:
                if not isinstance(item, Tag):
                    continue
                meta_div = item.find("div", class_="pull-left")
                if not meta_div:
                    continue

                date_match = re.search(
                    r"\d{4}-\d{2}-\d{2}", meta_div.get_text(strip=True)
                )
                if not date_match:
                    continue

                item_date = datetime.strptime(date_match.group(), "%Y-%m-%d")

                if item_date > end_dt:
                    consecutive_old_count = 0
                    continue

                if item_date < start_dt:
                    consecutive_old_count += 1
                    print(
                        f"  [跳过] 日期过早: {item_date.date()} "
                        f"(连续第{consecutive_old_count}条)"
                    )
                    if consecutive_old_count >= 5:
                        print(">>> 连续检测到多条旧新闻，停止任务。")
                        stop_crawling = True
                        break
                    continue

                consecutive_old_count = 0

                title_tag = item.find("a", href=True)
                if not isinstance(title_tag, Tag):
                    continue

                link = str(title_tag["href"])
                if not link.startswith("http"):
                    from urllib.parse import urlparse
                    parsed = urlparse(source_url)
                    link = f"{parsed.scheme}://{parsed.netloc}{link}"

                print(f"  [读取中] {title_tag.get_text(strip=True)[:40]} ({item_date.date()})")

                d_title, d_author, d_time, d_content = self._get_article_details(link)

                if not d_content or d_content == "NA":
                    continue

                is_relevant = any(
                    k.upper() in d_content.upper() for k in keyword_list
                )

                # 写入原始新闻备案 DB（URL 已存在则跳过，不覆盖）
                try:
                    raw_db.execute(
                        """INSERT OR IGNORE INTO raw_news
                           (url, title, content, author, publish_date, crawl_time,
                            passed_keyword_filter, batch_id)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            link, d_title, d_content, d_author, d_time,
                            datetime.now().isoformat(),
                            1 if is_relevant else 0,
                            batch_id,
                        ),
                    )
                    raw_db.commit()
                except Exception:
                    pass  # DB 写入失败不影响主流程

                if is_relevant:
                    if d_time == "NA" and last_valid_date:
                        d_time = last_valid_date
                    elif d_time != "NA":
                        last_valid_date = d_time

                    print("    -> [保留] 包含关键词")
                    all_data.append(
                        {
                            "发布日期": d_time,
                            "文章标题": d_title,
                            "正文内容": d_content,
                            "文章作者": d_author,
                            "原文链接": link,
                        }
                    )
                else:
                    print("    -> [丢弃] 正文不含关键词")

                time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

            if not stop_crawling:
                # 每页完成后保存断点
                _save_checkpoint(raw_db, batch_id, page)
                page += 1
                time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

        if not all_data:
            raw_db.close()
            return "未抓取到符合条件的新闻。请检查 URL、日期范围和关键词设置。"

        raw_db.close()

        if save_excel:
            file_name = f"Energy_News_Raw_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
            file_path = os.path.join(output_dir, file_name)
            pd.DataFrame(all_data).to_excel(file_path, index=False)
            return (
                f"采集完成。共抓取 {len(all_data)} 条符合条件的新闻。\n"
                f"文件已保存至：{os.path.abspath(file_path)}"
            )
        else:
            return f"采集完成。共抓取 {len(all_data)} 条符合条件的新闻（已写入 raw_news.db，batch_id={batch_id}）。"
