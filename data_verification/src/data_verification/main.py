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
    完整流程：去重入库 → 外部交叉验证（顺序执行）。
    input_dir: 指向 data_collector_filter/data/processed/ 目录
    db_path:   SQLite 数据库输出路径
    """
    inputs = {
        "input_dir":             "../data_collector_filter/data/processed",
        "db_path":               "data/db/bri_re_database.db",
        "cache_dir":             "data/verification_cache",
        "output_dir":            "data/manual_review",
        "gdelt_months":          24,
        "similarity_threshold":  0.65,
        "use_news_api":          False,
    }

    try:
        DataVerification().crew().kickoff(inputs=inputs)
    except Exception as e:
        raise Exception(f"Crew 运行出错: {e}")


def merge_only():
    """
    仅执行去重入库（跳过交叉验证），适合快速写入新数据。
    """
    from data_verification.tools.duplicate_merger_tool import DuplicateMergerTool
    tool = DuplicateMergerTool()
    result = tool._run(
        input_dir="../data_collector_filter/data/processed",
        db_path="data/db/bri_re_database.db",
    )
    print(result)


def verify_only():
    """
    仅执行外部交叉验证（数据库已有数据时使用），适合对已入库数据补充验证。
    """
    from data_verification.tools.cross_verification_tool import CrossVerificationTool
    tool = CrossVerificationTool()
    result = tool._run(
        db_path="data/db/bri_re_database.db",
        cache_dir="data/verification_cache",
        output_dir="data/manual_review",
        gdelt_months=24,
        similarity_threshold=0.65,
        use_news_api=False,
    )
    print(result)
