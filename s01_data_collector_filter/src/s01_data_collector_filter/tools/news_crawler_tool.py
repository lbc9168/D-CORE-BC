import os
import re
import time
from datetime import datetime
from typing import Type

import pandas as pd
import requests
from bs4 import BeautifulSoup
from crewai.tools import BaseTool
from pydantic import BaseModel, Field


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
    output_dir: str = Field(
        default="data/raw",
        description="输出 Excel 文件的保存目录（相对于运行目录）",
    )


class NewsCrawlerTool(BaseTool):
    name: str = "news_crawler_tool"
    description: str = (
        "从指定新闻网站按日期范围抓取新闻，并按关键词初步过滤后保存为 Excel 文件。"
        "返回保存的文件完整路径和抓取数量摘要。"
    )
    args_schema: Type[BaseModel] = NewsCrawlerInput

    # ── 内部工具方法 ──────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }

    def _get_article_details(self, url: str) -> tuple:
        """抓取详情页，返回 (title, author, publish_time, content)"""
        try:
            response = requests.get(url, headers=self._headers(), timeout=10)
            response.encoding = response.apparent_encoding
            soup = BeautifulSoup(response.text, "html.parser")

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
            if list_top_div:
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
            print(f"    [详情页错误] {url}: {e}")
            return "NA", "NA", "NA", "NA"

    # ── 主执行逻辑 ────────────────────────────────────────────────────────────

    def _run(
        self,
        source_url: str,
        start_date: str,
        end_date: str,
        keywords: str,
        output_dir: str = "data/raw",
    ) -> str:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        keyword_list = [k.strip() for k in keywords.split(",") if k.strip()]

        os.makedirs(output_dir, exist_ok=True)

        all_data = []
        page = 1
        stop_crawling = False
        consecutive_old_count = 0
        last_valid_date = None

        print(f">>> 任务启动：爬取 {start_date} 至 {end_date} 的新闻")
        print(f">>> 关键词过滤：{keyword_list}")

        while not stop_crawling:
            list_url = f"{source_url}?page={page}"
            print(f"\n正在扫描第 {page} 页...")

            try:
                res = requests.get(list_url, headers=self._headers(), timeout=10)
                soup = BeautifulSoup(res.text, "html.parser")
                list_items = soup.find_all("li", class_="list")

                if not list_items:
                    print("未发现更多条目，停止爬取。")
                    break

                for item in list_items:
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
                    if not title_tag:
                        continue

                    link = title_tag["href"]
                    if not link.startswith("http"):
                        # 从 source_url 提取域名
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

                    time.sleep(1)

                page += 1
                time.sleep(1)

            except Exception as e:
                print(f"列表页请求错误: {e}")
                break

        if not all_data:
            return "未抓取到符合条件的新闻。请检查 URL、日期范围和关键词设置。"

        file_name = f"Energy_News_Raw_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        file_path = os.path.join(output_dir, file_name)
        pd.DataFrame(all_data).to_excel(file_path, index=False)

        return (
            f"采集完成。共抓取 {len(all_data)} 条符合条件的新闻。\n"
            f"文件已保存至：{os.path.abspath(file_path)}"
        )
