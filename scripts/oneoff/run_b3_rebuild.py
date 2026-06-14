"""一次性：B3 s02a 去重重建 runner（worktree projects.db → 新建 worktree bri_re_database.db）。

设高并发（S02A_DEDUP_WORKERS）后调 DuplicateMergerTool._run，绝对路径指向 worktree 两库。
主工作区旧库/已发布 canonical 全程不碰。
"""
import os
import sys
from pathlib import Path

# Windows GBK stdout 下，打印含非 GBK 字符（如项目名里的 •）会 UnicodeEncodeError → 强制 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 并发：必须在 import 工具前设好（DEDUP_WORKERS 在模块导入时读取）。
# Windows 临时端口（TIME_WAIT）限制：150 会触发 WinError 10048 端口耗尽 → 降到 32 稳态远低于上限。
os.environ["S02A_DEDUP_WORKERS"] = os.environ.get("S02A_DEDUP_WORKERS", "32")

_root = Path(__file__).resolve().parents[2]  # worktree 根
from dotenv import find_dotenv, load_dotenv
load_dotenv(find_dotenv(usecwd=True))  # 向上命中主仓库 .env，拿 GEMINI_API_KEY

from s02a_duplicate_merger.tools.duplicate_merger_tool import DuplicateMergerTool

INPUT_DB = str(_root / "s01b_project_extractor" / "data" / "projects.db")
DB_PATH = str(_root / "s02a_duplicate_merger" / "data" / "db" / "bri_re_database.db")

print(f"[B3] DEDUP_WORKERS={os.environ['S02A_DEDUP_WORKERS']}")
print(f"[B3] input_db = {INPUT_DB}")
print(f"[B3] db_path  = {DB_PATH}")
result = DuplicateMergerTool()._run(input_db=INPUT_DB, db_path=DB_PATH)
print(result)
print("[B3] DONE")
