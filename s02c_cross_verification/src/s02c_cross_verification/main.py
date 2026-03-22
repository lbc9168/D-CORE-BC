#!/usr/bin/env python
"""
s02c_cross_verification — 外部信源交叉验证
从 s02a_duplicate_merger 产出的 SQLite 数据库读取待验证项目，
通过 GDELT 国际新闻库进行向量匹配和 LLM 确认，将结果写回数据库。

用法：
  crewai run          # 等同于 run_crew
  uv run s02c_cross_verification
  uv run run_crew

默认读取 ../s02a_duplicate_merger/data/db/bri_re_database.db。
如需指定其他数据库路径，可直接修改 inputs["db_path"]。
"""
import warnings

from dotenv import load_dotenv

from s02c_cross_verification.crew import CrossVerification

load_dotenv()
warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")


def run():
    """
    执行外部交叉验证（完整流程）。
    db_path: 由 duplicate_merger 子项目生成的 SQLite 数据库路径
             若已直接进入「数据库整理」阶段，则此步骤可跳过。
    """
    inputs = {
        "db_path":              "../s02a_duplicate_merger/data/db/bri_re_database.db",
        "cache_dir":            "data/verification_cache",
        "output_dir":           "data/manual_review",
        "gdelt_months":         24,
        "similarity_threshold": 0.65,
        "use_news_api":         False,
    }
    try:
        CrossVerification().crew().kickoff(inputs=inputs)
    except Exception as e:
        raise Exception(f"Crew error: {e}") from e
