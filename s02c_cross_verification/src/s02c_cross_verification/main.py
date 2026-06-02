#!/usr/bin/env python
"""
s02c_cross_verification — 外部信源交叉验证
从 s02a_duplicate_merger 产出的 SQLite 数据库读取待验证项目，
通过 GDELT 国际新闻库进行向量匹配和 LLM 确认，将结果写回数据库。

用法：
  uv run s02c_cross_verification

默认读取 ../s02a_duplicate_merger/data/db/bri_re_database.db。
如需指定其他数据库路径，可直接修改 run() 中的参数。
"""
from dotenv import load_dotenv

from s02c_cross_verification.tools.cross_verification_tool import CrossVerificationTool

load_dotenv()


def run():
    """直调 CrossVerificationTool._run() 执行完整交叉验证流程。"""
    result = CrossVerificationTool()._run(
        db_path="../s02a_duplicate_merger/data/db/bri_re_database.db",
        cache_dir="data/verification_cache",
        output_dir="data/manual_review",
        gdelt_months=24,
        similarity_threshold=0.65,
        use_news_api=False,
    )
    print(result)
