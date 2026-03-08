#!/usr/bin/env python
"""
duplicate_merger — 数据去重合并与 SQLite 入库
用法：
  crewai run          # 等同于 run_crew
  uv run duplicate_merger
  uv run run_crew
"""
import warnings

from dotenv import load_dotenv

from duplicate_merger.crew import DuplicateMerger

load_dotenv()
warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")


def run():
    """
    执行去重合并与入库（完整流程）。
    input_dir: data_collector_filter 产出的 Excel 目录
    db_path:   SQLite 数据库输出路径（同目录下的 cross_verification 子项目可读取此文件）
    """
    inputs = {
        "input_dir": "../data_collector_filter/data/processed",
        "db_path":   "data/db/bri_re_database.db",
    }
    try:
        DuplicateMerger().crew().kickoff(inputs=inputs)
    except Exception as e:
        raise Exception(f"Crew error: {e}") from e
