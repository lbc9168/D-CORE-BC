"""recheck：第二遍带原文 AI 复核编排器——从 review_queue 高+中驱动，复用 critic/apply/run_review 闭包。"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Callable, Optional

_root = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from review import apply, critic, queue  # noqa: E402
from review.export_queue import export_pending  # noqa: E402
from review.run_review import (  # noqa: E402  复用闭包工厂 + 默认校验器（不重复造）
    _make_update_field, _make_exclude_field, _default_value_validator, _UPDATABLE,
)
from eval.article_store import get_article  # noqa: E402

logger = logging.getLogger(__name__)

_HI_MID = ("高", "中")


def load_recheck_candidates(review_db: str, projects_db: str) -> list[dict]:
    """读 review_queue 中 severity∈{高,中} 的行，按 canonical_id(=extracted_projects.id) 聚合为候选。

    每候选：{row_id, source_link, suspicious_with_prior:[{field,prior_reason,prior_suggested,
    prior_conf,prior_evidence,current_value}], full_project:{...}}。
    """
    rconn = sqlite3.connect(str(review_db))
    rconn.row_factory = sqlite3.Row
    try:
        qrows = rconn.execute(
            "SELECT canonical_id, source_link, reason_type, field, suggested_value, "
            "critic_confidence, evidence FROM review_queue WHERE severity IN (?, ?)",
            _HI_MID,
        ).fetchall()
    finally:
        rconn.close()

    pconn = sqlite3.connect(str(projects_db))
    pconn.row_factory = sqlite3.Row
    try:
        pcols = {r[1] for r in pconn.execute("PRAGMA table_info(extracted_projects)")}
        prows = {r["id"]: r for r in pconn.execute("SELECT * FROM extracted_projects")}
    finally:
        pconn.close()

    agg: dict[int, dict] = {}
    for q in qrows:
        cid = q["canonical_id"]
        if not (cid and str(cid).isdigit()):
            continue
        rid = int(cid)
        pr = prows.get(rid)
        if pr is None:
            logger.warning("recheck 候选 row_id=%s 在 extracted_projects 找不到，跳过", rid)
            continue
        fld = q["field"]
        current_value = pr[fld] if (fld and fld in pcols) else None
        prior = {
            "field": fld, "prior_reason": q["reason_type"], "prior_suggested": q["suggested_value"],
            "prior_conf": q["critic_confidence"], "prior_evidence": q["evidence"],
            "current_value": current_value,
        }
        if rid not in agg:
            agg[rid] = {
                "row_id": rid, "source_link": q["source_link"] or (pr["source_link"] if "source_link" in pcols else ""),
                "suspicious_with_prior": [],
                "full_project": {
                    "project_name": pr["project_name"] if "project_name" in pcols else "",
                    "category": pr["category"] if "category" in pcols else "",
                    "capacity_mw": pr["capacity_mw"] if "capacity_mw" in pcols else "",
                    "involvement_type": pr["involvement_type"] if "involvement_type" in pcols else "",
                    "implementing_entity": pr["implementing_entity"] if "implementing_entity" in pcols else "",
                    "source_link": pr["source_link"] if "source_link" in pcols else "",
                },
            }
        agg[rid]["suspicious_with_prior"].append(prior)
    return list(agg.values())


_REPORTS_DIR = pathlib.Path(__file__).resolve().parent / "reports"


def run_recheck(
    projects_db: str,
    review_db: str,
    *,
    raw_news_db: str,
    llm,
    critic_fn: Optional[Callable] = None,
    article_fn: Optional[Callable] = None,
    dry_run: bool = False,
    conf_threshold: float = 0.8,
    concurrency: int = 1,
    value_validator: Optional[Callable] = None,
    residual_csv: str = "",
    ts: Optional[str] = None,
) -> dict:
    """编排第二遍复核：load_candidates→取原文→recheck_project→apply.route 分层→残留独立 queue→CSV。

    残留写**独立** recheck_queue.db（projects_db + '.recheck_queue.db'），不污染原 review_db。
    注入 seam：critic_fn(article,project,suspicious_with_prior,llm)、article_fn(url,db_path=)。
    并发模型同 run_review：Phase 2 取原文+critic 走线程池（仅 I/O/LLM），Phase 3 库写串行主线程（sqlite 线程安全）。
    """
    import shutil
    _ts = ts or datetime.now().strftime("%Y%m%d_%H%M%S")

    # 改库前备份（与 run_review 一致）
    bak = f"{projects_db}.bak_recheck_{_ts}"
    try:
        shutil.copy2(projects_db, bak)
        logger.info("已备份 projects_db → %s", bak)
    except OSError as e:
        logger.error("备份失败 %s→%s: %s", projects_db, bak, e); raise

    # 残留 queue（独立库，幂等建表；每次运行清空避免叠加）
    resid_db = f"{projects_db}.recheck_queue.db"
    if pathlib.Path(resid_db).exists():
        pathlib.Path(resid_db).unlink()
    queue.init_queue(resid_db)

    candidates = load_recheck_candidates(review_db, projects_db)
    n = {"cand": len(candidates), "auto_corrected": 0, "auto_excluded": 0,
         "queued": 0, "critic_failed": 0, "no_article": 0, "update_failed": 0}
    reasons: dict[str, int] = {}
    would_change: list[dict] = []
    validator = value_validator if value_validator is not None else _default_value_validator
    _critic = critic_fn if critic_fn is not None else critic.recheck_project
    _article = article_fn if article_fn is not None else get_article

    # ── Phase 2：取原文 + recheck critic（仅读 + LLM，无库写 → 可并发）─────────────
    def _fetch_and_critic(cand: dict) -> dict:
        """单候选：取原文→recheck critic；产出带 outcome 的结果（已知错误吞掉并标记，不中断池）。"""
        rid, link = cand["row_id"], cand["source_link"]
        try:
            article = _article(link, db_path=raw_news_db)
        except sqlite3.Error as e:
            logger.error("取原文失败 row_id=%d: %s", rid, e)
            return {**cand, "outcome": "no_article"}
        if article is None:
            logger.warning("找不到原文 row_id=%d，跳过", rid)
            return {**cand, "outcome": "no_article"}
        try:
            verdicts = _critic(article=article, project=cand["full_project"],
                               suspicious_with_prior=cand["suspicious_with_prior"], llm=llm)
        except Exception as e:  # 逐条隔离：任何 LLM/解析/网络异常都不连累整批（worker 边界，已 logging 不静默）
            logger.error("recheck critic 失败 row_id=%d: %s: %s", rid, type(e).__name__, e)
            return {**cand, "outcome": "critic_failed"}
        return {**cand, "outcome": "ok", "verdicts": verdicts}

    results: list[Optional[dict]] = [None] * len(candidates)
    workers = max(1, int(concurrency))
    if workers == 1 or len(candidates) <= 1:
        for i, cand in enumerate(candidates):
            results[i] = _fetch_and_critic(cand)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_fetch_and_critic, cand) for cand in candidates]
            for i, fut in enumerate(futs):
                results[i] = fut.result()  # 已知错误已在 worker 内标记 outcome

    # ── Phase 3：分流 + 库写（串行、主线程，sqlite 写线程安全）────────────────────
    wconn = sqlite3.connect(str(projects_db))
    try:
        for res in results:
            if res is None:
                continue
            if res["outcome"] == "no_article":
                n["no_article"] += 1; continue
            if res["outcome"] == "critic_failed":
                n["critic_failed"] += 1; continue

            rid, link = res["row_id"], res["source_link"]
            # 白名单预过滤（同 run_review）：非法字段 verdict 丢弃
            allowed = [v for v in res["verdicts"]
                       if v.verdict in ("ok", "exclude") or v.field == "status" or v.field in _UPDATABLE]

            failed = [0]
            update_field = _make_update_field(rid, wconn, dry_run, would_change, failed)
            exclude_field = _make_exclude_field(rid, wconn, dry_run, would_change, failed)
            res2 = apply.route(
                canonical_id=str(rid), source_link=link, verdicts=allowed,
                review_db=resid_db, update_field=update_field, conf_threshold=conf_threshold,
                value_validator=validator, exclude_fn=exclude_field,
            )
            n["auto_corrected"] += res2["auto_corrected"]
            n["auto_excluded"] += res2.get("auto_excluded", 0)
            n["queued"] += res2["queued"]
            n["update_failed"] += failed[0]
            for rt in res2.get("reasons", []):
                reasons[rt] = reasons.get(rt, 0) + 1
    finally:
        wconn.close()

    # 残留 → CSV（join projects_db 补原值）
    if residual_csv:
        try:
            export_pending(resid_db, projects_db, residual_csv)
        except (OSError, sqlite3.Error) as e:
            logger.error("写残留 CSV 失败 %s: %s", residual_csv, e)

    report = {
        "n_candidates": n["cand"], "n_auto_corrected": n["auto_corrected"],
        "n_auto_excluded": n["auto_excluded"], "n_queued": n["queued"],
        "n_critic_failed": n["critic_failed"], "n_no_article": n["no_article"],
        "n_update_failed": n["update_failed"], "reasons": reasons, "dry_run": dry_run,
    }
    if dry_run and would_change:
        report["would_change"] = would_change
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (path := _REPORTS_DIR / f"recheck_{_ts}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("run_recheck 完成 %s → %s", report, path)
    return report


def _build_llm(*, provider: str, model: str, _factory: Optional[Callable] = None):
    """按 provider/model 经统一 client 工厂建 LLM（_factory 为测试注入 seam）。"""
    from s01b_project_extractor.llm.client import LLMClient, _config_for_provider
    cfg = _config_for_provider(provider)
    if model:
        cfg.model = model
    factory = _factory or (lambda c: LLMClient(c))
    return factory(cfg)


def main() -> None:
    """CLI：dry-run 默认安全；--apply 才真改库。残留导出 CSV。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(str(_root / "review" / "recheck.log"), encoding="utf-8")])
    ap = argparse.ArgumentParser(description="第二遍带原文 AI 复核（severity 高+中）")
    ap.add_argument("--projects-db", default=str(_root / "s01b_project_extractor" / "data" / "projects.db"))
    ap.add_argument("--review-db", default=str(_root / "s01b_project_extractor" / "data" / "review.db"))
    ap.add_argument("--raw-news-db", default=str(_root / "s01a_news_crawler" / "data" / "raw_news.db"))
    ap.add_argument("--residual-csv", default=str(_root / "review" / "data" / "recheck_residual.csv"))
    ap.add_argument("--apply", action="store_true", help="真改库（默认 dry-run 不改）")
    ap.add_argument("--llm-provider", default="deepseek")
    ap.add_argument("--llm-model", default="")
    ap.add_argument("--concurrency", type=int, default=1,
                    help="取原文+critic 并发数（仅 LLM/读并发，库写仍串行）；deepseek v4-pro 建议 300-400")
    args = ap.parse_args()

    for p in (args.projects_db, args.review_db, args.raw_news_db):
        if not pathlib.Path(p).exists():
            print(f"路径不存在: {p}"); sys.exit(1)

    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=True))
    llm = _build_llm(provider=args.llm_provider, model=args.llm_model)

    rep = run_recheck(args.projects_db, args.review_db, raw_news_db=args.raw_news_db, llm=llm,
                      dry_run=not args.apply, concurrency=args.concurrency, residual_csv=args.residual_csv)
    print(f"\n=== run_recheck {'APPLY' if args.apply else 'DRY-RUN'} ===\n"
          f"  candidates    : {rep['n_candidates']}\n"
          f"  auto_corrected: {rep['n_auto_corrected']}\n"
          f"  auto_excluded : {rep['n_auto_excluded']}\n"
          f"  queued(残留)  : {rep['n_queued']}\n"
          f"  critic_failed : {rep['n_critic_failed']}\n"
          f"  no_article    : {rep['n_no_article']}\n"
          f"  残留 CSV      : {args.residual_csv}")


if __name__ == "__main__":
    main()
