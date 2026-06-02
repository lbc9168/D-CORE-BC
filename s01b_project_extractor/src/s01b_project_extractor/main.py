#!/usr/bin/env python
import concurrent.futures
import logging
import sys
import warnings
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# 确保 .env 在 s01b_project_extractor 目录下被加载
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

_S01B_ROOT = Path(__file__).resolve().parents[2]

logger = logging.getLogger(__name__)
_RUN_LOG_PATH: Path | None = None  # 本进程的运行日志路径（幂等：只配置一次）


def _setup_run_logging() -> Path:
    """配置运行日志：强制 stdout/stderr UTF-8 + 为 s01b 包级 logger 接上带时间戳的文件 + 控制台两个 handler，返回日志文件路径（幂等）。"""
    # Windows 控制台默认 GBK；提取过程大量中文 print 遇非 GBK 字符会抛 UnicodeEncodeError 中断整轮——强制 UTF-8（errors=replace 兜底）。
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    global _RUN_LOG_PATH
    if _RUN_LOG_PATH is not None:
        return _RUN_LOG_PATH
    log_dir = _S01B_ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"run_extract_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    # 挂在 s01b 包级 logger 上：捕获 main + 各 stage + tool 的批级 guardrail 日志；不向 root 传播以免与 eval/review 的 root 配置重复打印。
    pkg = logging.getLogger("s01b_project_extractor")
    pkg.setLevel(logging.INFO)
    pkg.handlers = [fh, sh]
    pkg.propagate = False
    _RUN_LOG_PATH = log_path
    return log_path


def run():
    """运行大规模并发 AI 提取（委托给 run_bulk_extract）。"""
    run_bulk_extract()


# ════════════════════════════════════════════════════════════════
#  大规模并发 AI 提取（从 raw_news.db 批量提取）
# ════════════════════════════════════════════════════════════════

def run_bulk_extract(
    max_extract_workers: int = 4,
    raw_news_db_path: str | None = None,
):
    """
    大规模并发 AI 提取（从 raw_news.db 批量读取，多线程调用 Gemini）。

    读取 raw_news.db 中所有关键词匹配的 batch_id，对每个 batch 并发执行 AI 提取。

    Args:
        max_extract_workers: AI 提取最大并发线程数（默认 4）
        raw_news_db_path:    raw_news.db 路径（默认取 s01a 的输出）
    """
    import sqlite3

    from s01b_project_extractor.tools.project_extractor_tool import ProjectExtractorTool

    log_path = _setup_run_logging()
    logger.info("run_bulk_extract 启动；运行日志写入 %s", log_path)

    if raw_news_db_path is None:
        raw_news_db_path = str(_S01B_ROOT.parent / "s01a_news_crawler" / "data" / "raw_news.db")

    projects_db = str(_S01B_ROOT / "data" / "projects.db")
    review_db   = str(_S01B_ROOT / "data" / "review.db")
    country_db  = str(_S01B_ROOT / "WorldCountryList.json")

    # 查找所有有数据的 batch_id
    try:
        conn = sqlite3.connect(raw_news_db_path)
        batch_ids = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT batch_id FROM raw_news WHERE passed_keyword_filter=1 ORDER BY batch_id"
            ).fetchall()
        ]
        conn.close()
    except Exception as e:
        logger.error("读取 raw_news.db 失败: %s", e)
        return

    if not batch_ids:
        logger.info("raw_news.db 中无关键词匹配记录，流程结束。")
        return

    logger.info("=" * 60)
    logger.info("并发 AI 提取（%d 个批次，%d 并发）", len(batch_ids), max_extract_workers)
    logger.info("=" * 60)

    def _extract_one(bid: str) -> str:
        try:
            result = ProjectExtractorTool()._run_from_db(
                batch_id=bid,
                raw_news_db_path=raw_news_db_path,
                projects_db_path=projects_db,
                review_db_path=review_db,
                country_db_path=country_db,
            )
            first_line = result.splitlines()[0] if result else "完成"
            return f"[完成] {bid}: {first_line}"
        except Exception as e:
            return f"[失败] {bid}: {e}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_extract_workers) as executor:
        futures = {executor.submit(_extract_one, bid): bid for bid in batch_ids}
        for future in concurrent.futures.as_completed(futures):
            logger.info("%s", future.result())

    logger.info("✅ run_bulk_extract 完成。AI 提取 %d 个批次。运行日志：%s", len(batch_ids), log_path)
