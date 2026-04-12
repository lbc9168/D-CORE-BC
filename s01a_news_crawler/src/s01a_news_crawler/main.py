#!/usr/bin/env python
import calendar
import sys
import time
import warnings
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

# 确保 .env 在 s01a_news_crawler 目录下被加载
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from s01a_news_crawler.crew import NewsCrawler

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

_S01A_ROOT = Path(__file__).resolve().parents[2]


def run():
    """
    运行新闻采集 Crew（单批次）。
    修改下方 inputs 中的参数以调整采集范围和目录。
    """
    inputs = {
        'source_url': 'https://news.goalfore.cn/topstories/project',
        'start_date': '2026-02-10',
        'end_date': '2026-02-28',
        'keywords': '能源,电力,MW,GW,风电,水电,光伏,储能',
        'raw_data_dir': 'data/raw',
        'raw_news_db_path': str(_S01A_ROOT / 'data' / 'raw_news.db'),
    }

    try:
        NewsCrawler().crew().kickoff(inputs=inputs)
    except Exception as e:
        raise Exception(f"Crew 运行出错: {e}") from e


def run_single(
    start_date: str,
    end_date: str,
    source_url: str = "https://news.goalfore.cn/topstories/project",
    keywords: str = "能源,电力,电站,MW,GW,风电,水电,光伏,储能",
    raw_data_dir: str | None = None,
    raw_news_db_path: str | None = None,
) -> str:
    """
    单批次爬取（直接调用 tool，不经过 LLM agent，参数精确透传）。
    供 pipeline flow.py 调用，避免 agent 误传参数的问题。
    """
    from s01a_news_crawler.tools.news_crawler_tool import NewsCrawlerTool

    return NewsCrawlerTool()._run(
        source_url=source_url,
        start_date=start_date,
        end_date=end_date,
        keywords=keywords,
        output_dir=raw_data_dir or str(_S01A_ROOT / "data" / "raw"),
        raw_news_db_path=raw_news_db_path or str(_S01A_ROOT / "data" / "raw_news.db"),
        save_excel=False,
    )


def train():
    """训练 Crew（用于优化 Agent 输出质量）"""
    inputs = {
        'source_url': 'https://news.goalfore.cn/topstories/project',
        'start_date': '2024-01-01',
        'end_date': '2024-01-31',
        'keywords': '能源,电力,MW,GW,风电,水电,光伏,储能',
        'raw_data_dir': 'data/raw',
        'raw_news_db_path': str(_S01A_ROOT / 'data' / 'raw_news.db'),
    }
    try:
        NewsCrawler().crew().train(
            n_iterations=int(sys.argv[1]),
            filename=sys.argv[2],
            inputs=inputs,
        )
    except Exception as e:
        raise Exception(f"训练出错: {e}") from e


def replay():
    """从指定任务节点重放 Crew 执行"""
    try:
        NewsCrawler().crew().replay(task_id=sys.argv[1])
    except Exception as e:
        raise Exception(f"重放出错: {e}") from e


def test():
    """测试 Crew 执行并返回结果"""
    inputs = {
        'source_url': 'https://news.goalfore.cn/topstories/project',
        'start_date': '2026-01-01',
        'end_date': '2026-01-07',
        'keywords': '能源,电力,MW,GW,风电,水电,光伏,储能',
        'raw_data_dir': 'data/raw',
        'raw_news_db_path': str(_S01A_ROOT / 'data' / 'raw_news.db'),
    }
    try:
        NewsCrawler().crew().test(
            n_iterations=int(sys.argv[1]),
            eval_llm=sys.argv[2],
            inputs=inputs,
        )
    except Exception as e:
        raise Exception(f"测试出错: {e}") from e


# ════════════════════════════════════════════════════════════════
#  大规模历史数据爬取（顺序 + 页码接力）
# ════════════════════════════════════════════════════════════════

def _add_months(d: date, months: int) -> date:
    """为日期加上指定月数，自动处理月末边界。"""
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def split_date_range(
    start_date: str,
    end_date: str,
    chunk_months: int = 1,
    base_inputs: dict | None = None,
) -> list[dict]:
    """
    将日期范围按月份拆分为多组 inputs。

    Args:
        start_date:   起始日期，格式 "YYYY-MM-DD"
        end_date:     截止日期（含），格式 "YYYY-MM-DD"
        chunk_months: 每个 chunk 的月份数（默认 1，即按月拆分）
        base_inputs:  覆盖 source_url / keywords 等基础参数

    Returns:
        inputs 字典列表，每项对应一个日期段的完整 kickoff inputs
    """
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)

    base: dict = {
        "source_url":       "https://news.goalfore.cn/topstories/project",
        "keywords":         "能源,电力,MW,GW,风电,水电,光伏,储能",
        "raw_data_dir":     "data/raw",
        "raw_news_db_path": str(_S01A_ROOT / "data" / "raw_news.db"),
    }
    if base_inputs:
        base.update(base_inputs)

    chunks: list[dict] = []
    current = start
    while current <= end:
        chunk_end = min(_add_months(current, chunk_months) - timedelta(days=1), end)
        chunks.append({
            **base,
            "start_date": current.isoformat(),
            "end_date":   chunk_end.isoformat(),
        })
        current = chunk_end + timedelta(days=1)

    return chunks


def run_bulk_crawl(
    start_date: str = "2018-01-01",
    end_date: str = "2026-03-01",
    chunk_months: int = 1,
    crawl_delay: float = 10.0,
    base_inputs: dict | None = None,
):
    """
    大规模历史数据爬取（顺序 + 页码接力，不做 AI 提取）。

    将 start_date → end_date 按月拆分，倒序逐批爬取目标网站，
    批次间等待 crawl_delay 秒，所有数据写入 raw_news.db。

    Args:
        start_date:   历史补录起始日期，格式 "YYYY-MM-DD"
        end_date:     历史补录截止日期，格式 "YYYY-MM-DD"
        chunk_months: 每批的月份数（默认 1）
        crawl_delay:  批次间等待秒数（默认 10s，避免反爬）
        base_inputs:  覆盖 source_url / keywords 等基础参数（可选）
    """
    from s01a_news_crawler.tools.news_crawler_tool import (
        NewsCrawlerTool,
        _init_raw_news_db,
        _load_checkpoint,
    )

    # 倒序：从最新日期开始爬（page 1 = 最新），每批结束后页码接力给下一批
    chunks = list(reversed(split_date_range(start_date, end_date, chunk_months, base_inputs)))
    total = len(chunks)

    print(f"\n{'='*60}")
    print(f"  顺序爬虫（{total} 个批次，倒序，批次间延迟 {crawl_delay}s）")
    print(f"  日期范围: {chunks[0]['end_date']} → {chunks[-1]['start_date']}")
    print(f"{'='*60}")

    raw_news_db = str(_S01A_ROOT / "data" / "raw_news.db")
    next_start_page = 1  # 第一批从 page 1 开始，后续批次由页码接力更新

    for i, chunk in enumerate(chunks, 1):
        batch_id = f"{chunk['start_date']}_{chunk['end_date']}"
        print(f"\n[{i}/{total}] 爬取 {chunk['start_date']} → {chunk['end_date']}（从第 {next_start_page} 页开始）")
        result = NewsCrawlerTool()._run(
            source_url=chunk["source_url"],
            start_date=chunk["start_date"],
            end_date=chunk["end_date"],
            keywords=chunk["keywords"],
            start_page=next_start_page,
            output_dir=chunk["raw_data_dir"],
            raw_news_db_path=raw_news_db,
            save_excel=False,           # 不写 Excel，数据已存入 raw_news.db
        )
        print(f"  → {result[:120]}")

        # 从 checkpoint DB 读取本批次停在第几页，作为下一批的起始页
        try:
            _conn = _init_raw_news_db(raw_news_db)
            last_page = _load_checkpoint(_conn, batch_id)
            _conn.close()
            if last_page:
                next_start_page = max(1, last_page - 2)  # 回退 2 页防止边界遗漏
                print(f"  页码接力: 下一批从第 {next_start_page} 页开始")
        except Exception:
            pass  # 读取失败则下一批沿用当前 next_start_page

        if i < total:
            print(f"  等待 {crawl_delay}s...")
            time.sleep(crawl_delay)

    print(f"\n✅ run_bulk_crawl 完成。共爬取 {total} 个批次。")
