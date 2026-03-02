#!/usr/bin/env python
import sys
import warnings
from pathlib import Path

from dotenv import load_dotenv

# 确保 .env 在 data_collector_filter 目录下被加载（GEMINI_API_KEY、MODEL 等）
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from data_collector_filter.crew import DataCollectorFilter

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")


def run():
    """
    运行一带一路可再生能源数据采集与筛选 Crew。
    修改下方 inputs 中的参数以调整采集范围和目录。
    """
    inputs = {
        # ── 爬虫参数 ──
        'source_url': 'https://news.goalfore.cn/topstories/project',
        'start_date': '2026-02-20',
        'end_date': '2026-02-28',
        'keywords': '能源,电力,MW,GW,风电,水电,光伏,储能',
        # ── 输出目录 ──
        'raw_data_dir': 'data/raw',
        'processed_data_dir': 'data/processed',
    }

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
