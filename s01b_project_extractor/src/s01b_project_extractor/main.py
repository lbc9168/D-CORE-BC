#!/usr/bin/env python
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

class BulkExtractionError(RuntimeError):
    """A database/batch failure stops downstream stages; completed work remains resumable."""

    def __init__(self, result: dict):
        self.result = result
        super().__init__(f"S01B 批次失败；详细结果: {result['report_path']}")


def run_bulk_extract(
    max_extract_workers: int = 4,
    raw_news_db_path: str | None = None,
    projects_db_path: str | None = None,
    review_db_path: str | None = None,
    batch_ids: list[str] | None = None,
    raw_ids: list[int] | None = None,
) -> dict:
    """提取指定批次/文章，返回可恢复结果；技术失败不混入业务排除。

    每个批次内部仍并发。批次串行写同一组 SQLite，避免 URL 去重竞态。
    raw_ids 可精确限定隔离重处理范围；成功 URL 重跑自动跳过。
    """
    import json
    import sqlite3
    from s01b_project_extractor.tools.project_extractor_tool import ProjectExtractorTool

    if type(max_extract_workers) is not int or not 1 <= max_extract_workers <= 24:
        raise ValueError("max_extract_workers 必须是 1 到 24 的文章并发数")
    if raw_ids is not None and (not raw_ids or any(type(v) is not int or v <= 0 for v in raw_ids)):
        raise ValueError("raw_ids 必须为非空正整数列表")
    if batch_ids is not None and (not batch_ids or any(not isinstance(v, str) or not v.strip() for v in batch_ids)):
        raise ValueError("batch_ids 必须为非空批次名称列表")
    raw_path = Path(raw_news_db_path or _S01B_ROOT.parent / "s01a_news_crawler/data/raw_news.db").resolve()
    projects_path = Path(projects_db_path or _S01B_ROOT / "data/projects.db").resolve()
    review_path = Path(review_db_path or _S01B_ROOT / "data/review.db").resolve()
    if len({raw_path, projects_path, review_path}) != 3:
        raise ValueError("raw_news、projects、review 数据库路径必须不同")
    if not raw_path.is_file():
        raise FileNotFoundError(f"S01B 输入库不存在: {raw_path}")
    clauses, params = ["passed_keyword_filter=1"], []
    if raw_ids is not None:
        clauses.append("id IN (" + ",".join("?" for _ in raw_ids) + ")")
        params.extend(raw_ids)
    if batch_ids is not None:
        clauses.append("batch_id IN (" + ",".join("?" for _ in batch_ids) + ")")
        params.extend(batch_ids)
    with sqlite3.connect(raw_path.as_uri() + "?mode=ro", uri=True) as conn:
        articles = [{"raw_id": row[0], "url": row[1], "batch_id": row[2]} for row in conn.execute(
            "SELECT id,url,batch_id FROM raw_news WHERE " + " AND ".join(clauses) + " ORDER BY id", params)]
    if raw_ids is not None and set(raw_ids) != {row["raw_id"] for row in articles}:
        raise ValueError("指定 raw_ids 不存在、未通过关键词筛选或不属于指定批次")
    selected_batches = sorted({row["batch_id"] for row in articles}, key=str)
    if any(not isinstance(bid, str) or not bid.strip() for bid in selected_batches):
        raise ValueError("输入文章缺少有效 batch_id，不能以空批次扩展为全库运行")
    log_path = _setup_run_logging()
    result = {"status": "completed", "raw_news_db_path": str(raw_path),
              "projects_db_path": str(projects_path), "review_db_path": str(review_path),
              "articles": articles, "batches": [], "failed_urls": [], "failed_batches": [],
              "log_path": str(log_path)}
    projects_path.parent.mkdir(parents=True, exist_ok=True)
    for batch_index, bid in enumerate(selected_batches):
        tool = ProjectExtractorTool()
        try:
            tool._run_from_db(batch_id=bid, raw_news_db_path=str(raw_path),
                projects_db_path=str(projects_path), review_db_path=str(review_path),
                country_db_path=str(_S01B_ROOT / "WorldCountryList.json"), raw_ids=raw_ids,
                max_workers=max_extract_workers, backup_existing=batch_index == 0)
            batch_result = tool.last_result
            if batch_result.get("status") not in {"completed", "partial"}:
                raise RuntimeError(f"S01B 批次缺少有效完成状态: {bid}")
            result["batches"].append(batch_result)
            result["failed_urls"].extend(batch_result.get("failed_urls", []))
            if batch_result["status"] == "partial":
                result["status"] = "partial"
        except (RuntimeError, ValueError, OSError, sqlite3.Error, AttributeError) as exc:
            logger.exception("批次 %s 失败，停止后续批次及下游；成功 URL 保留供重试", bid)
            result["status"] = "failed"
            result["failed_batches"].append({"batch_id": bid, "error": str(exc)})
            break
    result["report_path"] = str(projects_path.parent / f"extraction_run_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json")
    Path(result["report_path"]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("run_bulk_extract status=%s batches=%d failed_articles=%d report=%s",
                result["status"], len(result["batches"]), len(result["failed_urls"]), result["report_path"])
    if result["status"] == "failed":
        raise BulkExtractionError(result)
    return result
