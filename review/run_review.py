"""run_review：review/ pipeline 编排器——从 extracted_projects 读候选、取原文、批判、分流，输出 JSON report。

CLI: python -m review.run_review [--projects-db ...] [--dry-run] ...
函数: run_review(projects_db, raw_news_db, review_db, *, ...) -> dict
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import shutil
import sqlite3
import sys
from datetime import datetime
from typing import Callable, Optional

from pydantic import BaseModel, Field, field_validator, ValidationError

# ── 仓库根上溯（含 bri_enums.py）────────────────────────────────────────────
_root = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from review import apply, critic, queue, signals  # noqa: E402
from eval.article_store import get_article  # noqa: E402

logger = logging.getLogger(__name__)

# ── 字段白名单：只允许 LLM verdict 纠正这些列（防 SQL 注入 + 防误改元数据）────
_UPDATABLE: frozenset[str] = frozenset({"category", "capacity_mw", "involvement_type", "project_name"})

# ── 报告目录 ─────────────────────────────────────────────────────────────────
_REPORTS_DIR = pathlib.Path(__file__).resolve().parent / "reports"


# ── Pydantic 入口参数校验 ────────────────────────────────────────────────────

class ReviewArgs(BaseModel):
    """CLI/函数入口参数校验：路径必须存在，阈值∈[0,1]。"""
    projects_db: str
    raw_news_db: str
    review_db: str
    dry_run: bool = False
    conf_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    llm_provider: str = "deepseek"

    @field_validator("projects_db")
    @classmethod
    def projects_db_must_exist(cls, v: str) -> str:
        """projects_db 路径必须指向已存在的文件。"""
        if not pathlib.Path(v).exists():
            raise ValueError(f"projects_db 不存在: {v}")
        return v


# ── 核心编排函数 ─────────────────────────────────────────────────────────────

def run_review(
    projects_db: str,
    raw_news_db: str,
    review_db: str,
    *,
    critic_fn: Optional[Callable] = None,
    llm=None,
    dry_run: bool = False,
    conf_threshold: float = 0.8,
    article_fn: Optional[Callable] = None,
    ts: Optional[str] = None,
) -> dict:
    """编排 signals→article→critic→apply 复核流水线，返回 report dict。

    注入参数（测试 seam）：
      - critic_fn(article, project, suspicious, llm) -> list[Verdict]  若 None 用 critic.review_project
      - article_fn(url, db_path=None) -> dict|None                     若 None 用 eval.article_store.get_article
      - ts: report 文件名时间戳字符串，若 None 用当前时间
    """
    # 幂等建队列表
    queue.init_queue(review_db)

    # 备份 projects_db（保证真实运行有回退；测试 tmp 同样做，成本极低）
    bak_path = str(projects_db) + f".bak_{ts or datetime.now().strftime('%Y%m%d_%H%M%S')}"
    try:
        shutil.copy2(projects_db, bak_path)
        logger.info("已备份 projects_db → %s", bak_path)
    except OSError as e:
        logger.error("备份 projects_db 失败 src=%s dst=%s: %s", projects_db, bak_path, e)
        raise

    # 读所有行
    conn = sqlite3.connect(str(projects_db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, project_name, category, capacity_mw, involvement_type, "
            "implementing_entity, is_unnamed_placeholder, source_link "
            "FROM extracted_projects"
        ).fetchall()
    except sqlite3.Error as e:
        logger.error("读取 extracted_projects 失败: %s", e)
        conn.close()
        raise
    conn.close()

    # 统计器
    n_candidates = 0
    n_auto_corrected = 0
    n_queued = 0
    n_critic_failed = 0
    n_no_article = 0
    run_reasons: dict[str, int] = {}
    # dry_run 时收集 would-change 记录（用于日志/report）
    would_change: list[dict] = []

    # 打开写库连接（干运行时不写 extracted_projects，但 review.db 的 queue 仍写）
    write_conn = sqlite3.connect(str(projects_db))

    try:
        for row in rows:
            row_id = row["id"]
            source_link = row["source_link"] or ""
            proj_dict = {
                "category": row["category"],
                "capacity_mw": row["capacity_mw"],
                "involvement_type": row["involvement_type"],
                "entities": [
                    {
                        "name": row["implementing_entity"] or "",
                        "is_unnamed_placeholder": bool(row["is_unnamed_placeholder"]),
                    }
                ],
            }

            suspicious = signals.suspicious_fields(proj_dict)
            if not suspicious:
                continue
            n_candidates += 1

            # ── 取原文 ────────────────────────────────────────────────────
            if article_fn is not None:
                article = article_fn(source_link, db_path=raw_news_db)
            else:
                article = get_article(source_link, db_path=raw_news_db)

            if article is None:
                logger.warning("找不到原文 link=%s row_id=%d，跳过 critic", source_link, row_id)
                n_no_article += 1
                continue

            # ── 批判 ─────────────────────────────────────────────────────
            full_project = {
                "project_name": row["project_name"],
                "category": row["category"],
                "capacity_mw": row["capacity_mw"],
                "involvement_type": row["involvement_type"],
                "implementing_entity": row["implementing_entity"],
                "source_link": source_link,
            }
            try:
                if critic_fn is not None:
                    verdicts = critic_fn(
                        article=article,
                        project=full_project,
                        suspicious=suspicious,
                        llm=llm,
                    )
                else:
                    verdicts = critic.review_project(
                        article=article,
                        project=full_project,
                        suspicious=suspicious,
                        llm=llm,
                    )
            except (ValidationError, RuntimeError) as e:
                logger.error("critic 失败 link=%s row_id=%d: %s", source_link, row_id, e)
                n_critic_failed += 1
                continue

            # ── 白名单预过滤：去掉不允许改的字段 verdict ────────────────
            # 白名单外的 "wrong" 高置信 verdict 直接记警告入 queue，不让 apply 调 update_field
            allowed_verdicts = []
            for v in verdicts:
                if v.verdict == "ok":
                    allowed_verdicts.append(v)
                elif v.field == "status":
                    allowed_verdicts.append(v)  # status challenge 走 apply 入 queue
                elif v.field in _UPDATABLE:
                    allowed_verdicts.append(v)
                else:
                    logger.warning(
                        "跳过不可改字段 %s（白名单=%s）row_id=%d", v.field, sorted(_UPDATABLE), row_id
                    )
                    # 不入队也不计 auto_corrected：彻底忽略（字段不合法）

            # ── update_field 闭包 ─────────────────────────────────────────
            # 捕获 write_conn、row_id、dry_run 到闭包内
            _rc = {"auto_c": 0, "upd_failed": 0}

            def _make_update_field(rid, wconn, _dry_run, _would_change):
                """工厂函数，为每条记录生成独立的 update_field 闭包（避免循环变量捕获问题）。"""
                def update_field(canonical_id, field, new_value):
                    """执行（或模拟）单字段 UPDATE；dry_run 时只记录不写库。"""
                    # 二次白名单防御（apply 直接调 update_field；field 已是白名单过滤后）
                    if field not in _UPDATABLE:
                        logger.warning("update_field 二次防御：跳过不可改字段 %s", field)
                        return
                    if _dry_run:
                        msg = f"[DRY_RUN] 会将 id={rid} {field} 改为 {new_value!r}"
                        logger.info(msg)
                        _would_change.append({"id": rid, "field": field, "new_value": new_value})
                        return
                    try:
                        with wconn:
                            wconn.execute(
                                f"UPDATE extracted_projects SET {field}=? WHERE id=?",
                                (new_value, rid),
                            )
                        logger.info("已更新 id=%d %s → %r", rid, field, new_value)
                    except sqlite3.Error as e:
                        logger.error("UPDATE 失败 id=%d field=%s: %s", rid, field, e)
                        _rc["upd_failed"] += 1

                return update_field

            update_field = _make_update_field(row_id, write_conn, dry_run, would_change)

            # ── 分流 ─────────────────────────────────────────────────────
            result = apply.route(
                canonical_id=str(row_id),
                source_link=source_link,
                verdicts=allowed_verdicts,
                review_db=review_db,
                update_field=update_field,
                conf_threshold=conf_threshold,
            )
            n_auto_corrected += result["auto_corrected"]
            n_queued += result["queued"]

            # 本条记录的 queued reasons（用于 report breakdown）
            for v in allowed_verdicts:
                if v.verdict != "ok":
                    is_auto = (
                        v.field != "status"
                        and v.verdict == "wrong"
                        and v.suggested_value
                        and v.confidence >= conf_threshold
                        and v.field in _UPDATABLE
                    )
                    if not is_auto:
                        rtype = "status_challenge" if v.field == "status" else "low_confidence_field"
                        run_reasons[rtype] = run_reasons.get(rtype, 0) + 1

    finally:
        write_conn.close()

    # ── 汇总 report ──────────────────────────────────────────────────────────
    report: dict = {
        "n_candidates": n_candidates,
        "n_auto_corrected": n_auto_corrected,
        "n_queued": n_queued,
        "n_critic_failed": n_critic_failed,
        "n_no_article": n_no_article,
        "reasons": run_reasons,
        "dry_run": dry_run,
    }
    if dry_run and would_change:
        report["would_change"] = would_change

    # ── 写 report JSON ───────────────────────────────────────────────────────
    _ts = ts or datetime.now().strftime("%Y%m%d_%H%M%S")
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = _REPORTS_DIR / f"{_ts}.json"
    try:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("report 已写入 %s", report_path)
    except OSError as e:
        logger.error("写 report 失败 path=%s: %s", report_path, e)

    logger.info(
        "run_review 完成 candidates=%d auto_corrected=%d queued=%d "
        "critic_failed=%d no_article=%d dry_run=%s",
        n_candidates, n_auto_corrected, n_queued, n_critic_failed, n_no_article, dry_run,
    )
    return report


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    """CLI 入口：解析参数、校验、建 LLM client、调 run_review、打印摘要。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                str(_root / "review" / "run_review.log"), encoding="utf-8"
            ),
        ],
    )

    parser = argparse.ArgumentParser(description="review/ pipeline 编排器")
    parser.add_argument("--projects-db", default=str(_root / "s01b_project_extractor" / "data" / "projects.db"))
    parser.add_argument("--raw-news-db", default=str(_root / "s01a_news_crawler" / "data" / "raw_news.db"))
    parser.add_argument("--review-db", default=str(_root / "s01b_project_extractor" / "data" / "review.db"))
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--conf-threshold", type=float, default=0.8)
    parser.add_argument("--llm-provider", default="deepseek")
    parser.add_argument("--llm-model", default="")
    args = parser.parse_args()

    # Pydantic 入口校验
    try:
        validated = ReviewArgs(
            projects_db=args.projects_db,
            raw_news_db=args.raw_news_db,
            review_db=args.review_db,
            dry_run=args.dry_run,
            conf_threshold=args.conf_threshold,
            llm_provider=args.llm_provider,
        )
    except ValidationError as e:
        print(f"参数校验失败:\n{e}")
        sys.exit(1)

    # 构建 LLM client（缺 key 立即 RuntimeError）
    from s01b_project_extractor.llm.client import LLMClient, _config_for_provider  # noqa: PLC0415
    cfg = _config_for_provider(validated.llm_provider)
    if args.llm_model:
        cfg.model = args.llm_model
    llm = LLMClient(cfg)

    report = run_review(
        projects_db=validated.projects_db,
        raw_news_db=validated.raw_news_db,
        review_db=validated.review_db,
        llm=llm,
        dry_run=validated.dry_run,
        conf_threshold=validated.conf_threshold,
    )

    # 人机摘要（CLI 顶层 print 合规）
    print(
        f"\n=== run_review 完成 ===\n"
        f"  candidates    : {report['n_candidates']}\n"
        f"  auto_corrected: {report['n_auto_corrected']}\n"
        f"  queued        : {report['n_queued']}\n"
        f"  critic_failed : {report['n_critic_failed']}\n"
        f"  no_article    : {report['n_no_article']}\n"
        f"  dry_run       : {report['dry_run']}\n"
        f"  reasons       : {report['reasons']}"
    )


if __name__ == "__main__":
    main()
