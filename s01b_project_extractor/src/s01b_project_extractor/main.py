#!/usr/bin/env python
import concurrent.futures
import warnings
from pathlib import Path

from dotenv import load_dotenv

# 确保 .env 在 s01b_project_extractor 目录下被加载
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

_S01B_ROOT = Path(__file__).resolve().parents[2]


def run():
    """运行大规模并发 AI 提取（委托给 run_bulk_extract）。"""
    run_bulk_extract()


# ════════════════════════════════════════════════════════════════
#  大规模并发 AI 提取（从 raw_news.db 批量提取）
# ════════════════════════════════════════════════════════════════

def run_bulk_extract(
    max_extract_workers: int = 4,
    raw_news_db_path: str | None = None,
):
    """
    大规模并发 AI 提取（从 raw_news.db 批量读取，多线程调用 Gemini）。

    读取 raw_news.db 中所有关键词匹配的 batch_id，对每个 batch 并发执行 AI 提取。

    Args:
        max_extract_workers: AI 提取最大并发线程数（默认 4）
        raw_news_db_path:    raw_news.db 路径（默认取 s01a 的输出）
    """
    import sqlite3

    from s01b_project_extractor.tools.project_extractor_tool import ProjectExtractorTool

    if raw_news_db_path is None:
        raw_news_db_path = str(_S01B_ROOT.parent / "s01a_news_crawler" / "data" / "raw_news.db")

    projects_db = str(_S01B_ROOT / "data" / "projects.db")
    review_db   = str(_S01B_ROOT / "data" / "review.db")
    country_db  = str(_S01B_ROOT / "WorldCountryList.json")

    # 查找所有有数据的 batch_id
    try:
        conn = sqlite3.connect(raw_news_db_path)
        batch_ids = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT batch_id FROM raw_news WHERE passed_keyword_filter=1 ORDER BY batch_id"
            ).fetchall()
        ]
        conn.close()
    except Exception as e:
        print(f"错误：读取 raw_news.db 失败: {e}")
        return

    if not batch_ids:
        print("raw_news.db 中无关键词匹配记录，流程结束。")
        return

    print(f"\n{'='*60}")
    print(f"  并发 AI 提取（{len(batch_ids)} 个批次，{max_extract_workers} 并发）")
    print(f"{'='*60}")

    def _extract_one(bid: str) -> str:
        try:
            result = ProjectExtractorTool()._run_from_db(
                batch_id=bid,
                raw_news_db_path=raw_news_db_path,
                projects_db_path=projects_db,
                review_db_path=review_db,
                country_db_path=country_db,
            )
            first_line = result.splitlines()[0] if result else "完成"
            return f"[完成] {bid}: {first_line}"
        except Exception as e:
            return f"[失败] {bid}: {e}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_extract_workers) as executor:
        futures = {executor.submit(_extract_one, bid): bid for bid in batch_ids}
        for future in concurrent.futures.as_completed(futures):
            print(future.result())

    print(f"\n✅ run_bulk_extract 完成。AI 提取 {len(batch_ids)} 个批次。")
