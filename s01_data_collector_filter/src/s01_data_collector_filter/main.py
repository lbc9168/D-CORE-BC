#!/usr/bin/env python
import asyncio
import calendar
import shutil
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

# 确保 .env 在 data_collector_filter 目录下被加载（GEMINI_API_KEY、MODEL 等）
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from s01_data_collector_filter.crew import DataCollectorFilter

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

_S01_ROOT = Path(__file__).resolve().parents[2]


def archive_old_files():
    """将 data/raw 和 data/processed 中的旧文件移入各自的 Archived/ 子目录。"""
    for data_dir in [_S01_ROOT / "data" / "raw", _S01_ROOT / "data" / "processed"]:
        archived_dir = data_dir / "Archived"
        archived_dir.mkdir(parents=True, exist_ok=True)
        for f in data_dir.iterdir():
            if f.is_file() and f.name != ".gitkeep":
                shutil.move(str(f), str(archived_dir / f.name))


def run():
    """
    运行一带一路可再生能源数据采集与筛选 Crew。
    修改下方 inputs 中的参数以调整采集范围和目录。
    """
    inputs = {
        # ── 爬虫参数 ──
        'source_url': 'https://news.goalfore.cn/topstories/project',
        'start_date': '2026-02-10',
        'end_date': '2026-02-28',
        'keywords': '能源,电力,MW,GW,风电,水电,光伏,储能',
        # ── 输出目录 ──
        'raw_data_dir': 'data/raw',
        'processed_data_dir': 'data/processed',
    }

    archive_old_files()

    try:
        DataCollectorFilter().crew().kickoff(inputs=inputs)
    except Exception as e:
        raise Exception(f"Crew 运行出错: {e}")


def train():
    """训练 Crew（用于优化 Agent 输出质量）"""
    inputs = {
        'source_url': 'https://news.goalfore.cn/topstories/project',
        'start_date': '2024-01-01',
        'end_date': '2024-01-31',
        'keywords': '能源,电力,MW,GW,风电,水电,光伏,储能',
        'raw_data_dir': 'data/raw',
        'processed_data_dir': 'data/processed',
    }
    try:
        DataCollectorFilter().crew().train(
            n_iterations=int(sys.argv[1]),
            filename=sys.argv[2],
            inputs=inputs,
        )
    except Exception as e:
        raise Exception(f"训练出错: {e}")


def replay():
    """从指定任务节点重放 Crew 执行"""
    try:
        DataCollectorFilter().crew().replay(task_id=sys.argv[1])
    except Exception as e:
        raise Exception(f"重放出错: {e}")


def test():
    """测试 Crew 执行并返回结果"""
    inputs = {
        'source_url': 'https://news.goalfore.cn/topstories/project',
        'start_date': '2026-01-01',
        'end_date': '2026-01-07',
        'keywords': '能源,电力,MW,GW,风电,水电,光伏,储能',
        'raw_data_dir': 'data/raw',
        'processed_data_dir': 'data/processed',
    }
    try:
        DataCollectorFilter().crew().test(
            n_iterations=int(sys.argv[1]),
            eval_llm=sys.argv[2],
            inputs=inputs,
        )
    except Exception as e:
        raise Exception(f"测试出错: {e}")


# ════════════════════════════════════════════════════════════════
#  批量并发采集（kickoff_for_each_async）
# ════════════════════════════════════════════════════════════════

def _add_months(d: date, months: int) -> date:
    """为日期加上指定月数，自动处理月末边界（如 1月31日 + 1月 = 2月28日）。"""
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
    将日期范围按月份拆分为多组 inputs，供 kickoff_for_each / kickoff_for_each_async 使用。

    Args:
        start_date:   起始日期，格式 "YYYY-MM-DD"
        end_date:     截止日期（含），格式 "YYYY-MM-DD"
        chunk_months: 每个 chunk 的月份数（默认 1，即按月拆分）
        base_inputs:  覆盖 source_url / keywords / raw_data_dir 等基础参数

    Returns:
        inputs 字典列表，每项对应一个日期段的完整 kickoff inputs
    """
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)

    base: dict = {
        "source_url":         "https://news.goalfore.cn/topstories/project",
        "keywords":           "能源,电力,MW,GW,风电,水电,光伏,储能",
        "raw_data_dir":       "data/raw",
        "processed_data_dir": "data/processed",
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


async def _kickoff_parallel_async(inputs_list: list[dict]):
    """使用 kickoff_for_each_async 并发运行所有批次。"""
    return await DataCollectorFilter().crew().kickoff_for_each_async(inputs=inputs_list)


def run_parallel(
    start_date: str = "2023-01-01",
    end_date: str = "2026-03-08",
    chunk_months: int = 1,
    base_inputs: dict | None = None,
):
    """
    并发批量采集历史数据（适合补录大时间段数据）。

    将 start_date → end_date 按 chunk_months 拆分为多个批次，
    所有批次通过 kickoff_for_each_async 并发执行，每批产出独立的
    带时间戳 Excel 文件，互不干扰。

    Args:
        start_date:   历史补录起始日期，格式 "YYYY-MM-DD"
        end_date:     历史补录截止日期，格式 "YYYY-MM-DD"
        chunk_months: 每批的月份数。值越大 → 批次越少 → API 并发压力越小。
                      如遇 Gemini API 限速（429），请增大此值（如改为 3）。
        base_inputs:  覆盖 source_url / keywords / 目录等基础参数（可选）

    示例：
        run_parallel("2024-01-01", "2025-12-31", chunk_months=1)   # 24批并发
        run_parallel("2023-01-01", "2026-03-08", chunk_months=3)   # ~13批并发
    """
    archive_old_files()

    inputs_list = split_date_range(start_date, end_date, chunk_months, base_inputs)
    total = len(inputs_list)
    print(f"\n共 {total} 个批次，开始并发采集……")
    for i, inp in enumerate(inputs_list, 1):
        print(f"  批次 {i:>2}/{total}: {inp['start_date']} → {inp['end_date']}")

    try:
        results = asyncio.run(_kickoff_parallel_async(inputs_list))
        print(f"\n✅ 所有 {len(results)} 个批次采集完成。")
        return results
    except Exception as e:
        raise Exception(f"并发采集出错: {e}") from e


def run_with_trigger():
    """通过外部触发载荷运行 Crew（供 CI/调度系统调用）"""
    import json

    if len(sys.argv) < 2:
        raise Exception("缺少触发载荷，请以 JSON 字符串作为命令行参数传入。")

    try:
        trigger_payload = json.loads(sys.argv[1])
    except json.JSONDecodeError:
        raise Exception("触发载荷不是合法的 JSON 字符串。")

    inputs = {
        "crewai_trigger_payload": trigger_payload,
        'source_url': trigger_payload.get('source_url', 'https://news.goalfore.cn/topstories/project'),
        'start_date': trigger_payload.get('start_date', '2024-01-01'),
        'end_date': trigger_payload.get('end_date', '2025-09-30'),
        'keywords': trigger_payload.get('keywords', '能源,电力,MW,GW,风电,水电,光伏,储能'),
        'raw_data_dir': trigger_payload.get('raw_data_dir', 'data/raw'),
        'processed_data_dir': trigger_payload.get('processed_data_dir', 'data/processed'),
    }

    try:
        result = DataCollectorFilter().crew().kickoff(inputs=inputs)
        return result
    except Exception as e:
        raise Exception(f"触发运行出错: {e}")
