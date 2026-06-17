#!/usr/bin/env python
"""
s02a_duplicate_merger — 数据去重合并与 SQLite 入库
用法：
  uv run s02a_duplicate_merger
"""
import warnings

from dotenv import load_dotenv

from s02a_duplicate_merger.tools.duplicate_merger_tool import DuplicateMergerTool

load_dotenv()
warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")


def run():
    """执行去重合并与入库（直调 tool，无 crewAI）。"""
    DuplicateMergerTool()._run(
        input_db="../s01b_project_extractor/data/projects.db",
        db_path="data/db/bri_re_database.db",
    )
