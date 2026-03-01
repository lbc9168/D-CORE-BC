#!/usr/bin/env python
import sys
import warnings
from pathlib import Path

from dotenv import load_dotenv

# 加载 .env（先尝试本项目目录，再向上查找）
load_dotenv(Path(__file__).resolve().parents[2] / ".env")
load_dotenv()  # fallback：从 CWD 向上搜索

from data_verification.crew import DataVerification

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")


def run():
    """
    运行一带一路可再生能源数据库去重与入库 Crew。
    input_dir: 指向 data_collector_filter/data/processed/ 目录
    db_path:   SQLite 数据库输出路径
    """
    inputs = {
        "input_dir": "../data_collector_filter/data/processed",
        "db_path": "data/db/bri_re_database.db",
    }

    try:
        DataVerification().crew().kickoff(inputs=inputs)
    except Exception as e:
        raise Exception(f"Crew 运行出错: {e}")
