#!/usr/bin/env python
import concurrent.futures
import sys
import warnings
from pathlib import Path

from dotenv import load_dotenv

# 确保 .env 在 s01b_project_extractor 目录下被加载
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from s01b_project_extractor.crew import ProjectExtractor

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

_S01B_ROOT = Path(__file__).resolve().parents[2]


def run():
    """
    运行项目信息提取 Crew（单批次）。
    从 s01a 的 raw_news.db 中读取数据进行 AI 提取。
    """
    inputs = {
        'raw_news_db_path': str(_S01B_ROOT.parent / 's01a_news_crawler' / 'data' / 'raw_news.db'),
        'projects_db_path': str(_S01B_ROOT / 'data' / 'projects.db'),
        'review_db_path':   str(_S01B_ROOT / 'data' / 'review.db'),
    }

    try:
        ProjectExtractor().crew().kickoff(inputs=inputs)
    except Exception as e:
        raise Exception(f"Crew 运行出错: {e}") from e


def train():
    """训练 Crew（用于优化 Agent 输出质量）"""
    inputs = {
        'raw_news_db_path': str(_S01B_ROOT.parent / 's01a_news_crawler' / 'data' / 'raw_news.db'),
        'projects_db_path': str(_S01B_ROOT / 'data' / 'projects.db'),
        'review_db_path':   str(_S01B_ROOT / 'data' / 'review.db'),
    }
    try:
        ProjectExtractor().crew().train(
            n_iterations=int(sys.argv[1]),
            filename=sys.argv[2],
            inputs=inputs,
        )
    except Exception as e:
        raise Exception(f"训练出错: {e}") from e


def replay():
    """从指定任务节点重放 Crew 执行"""
    try:
        ProjectExtractor().crew().replay(task_id=sys.argv[1])
    except Exception as e:
        raise Exception(f"重放出错: {e}") from e


def test():
    """测试 Crew 执行并返回结果"""
    inputs = {
        'raw_news_db_path': str(_S01B_ROOT.parent / 's01a_news_crawler' / 'data' / 'raw_news.db'),
        'projects_db_path': str(_S01B_ROOT / 'data' / 'projects.db'),
        'review_db_path':   str(_S01B_ROOT / 'data' / 'review.db'),
    }
    try:
        ProjectExtractor().crew().test(
            n_iterations=int(sys.argv[1]),
            eval_llm=sys.argv[2],
            inputs=inputs,
        )
    except Exception as e:
        raise Exception(f"测试出错: {e}") from e


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
