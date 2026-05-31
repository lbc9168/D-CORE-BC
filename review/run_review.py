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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Callable, Optional

from pydantic import BaseModel, Field, field_validator, ValidationError

# ── 仓库根上溯（含 bri_enums.py）────────────────────────────────────────────
_root = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import bri_enums  # noqa: E402
import bri_parse  # noqa: E402
from review import apply, critic, queue, signals  # noqa: E402
from eval.article_store import get_article  # noqa: E402

logger = logging.getLogger(__name__)


# ── 自动纠正建议值的合法性校验：非法值不自动写库，改入人工清单 ─────────────────
def _default_value_validator(field: str, value) -> bool:
    """自动改写前校验 critic 建议值是否合法（防把 LLM 杜撰的非枚举/非数值写进库）。

    - category：一律返回 False（类别错常与"该不该排除"耦合，统一转人工，不自动改）。
    - involvement_type：必须是 bri_enums 合法参与方式，且非占位 "Unknown"。
    - capacity_mw：必须能被 bri_parse 解析为正数。
    - project_name：自由文本，非空即可。
    - 其它未知字段：False（不自动改）。
    """
    if field == "category":
        return False
    if field == "involvement_type":
        return value in set(bri_enums.valid_involvement()) and value != "Unknown"
    if field == "capacity_mw":
        num = bri_parse.parse_number(value)
        return num is not None and num > 0
    if field == "project_name":
        return bool(str(value or "").strip())
    return False

# ── 字段白名单：field→硬编码列名，SQL 插值只用 dict value（防注入 + 防未来笔误）────
# 键：LLM verdict 允许使用的字段名；值：数据库中对应的列名字面量（绝不来自运行时字符串）
_UPDATABLE: dict[str, str] = {
    "category": "category",
    "capacity_mw": "capacity_mw",
    "involvement_type": "involvement_type",
    "project_name": "project_name",
}

# ── 报告目录 ─────────────────────────────────────────────────────────────────
_REPORTS_DIR = pathlib.Path(__file__).resolve().parent / "reports"


# ── update_field 工厂（模块级，避免循环内重复编译）──────────────────────────

def _make_update_field(rid, wconn, _dry_run, _would_change, _failed_counter):
    """工厂函数，为每条记录生成独立的 update_field 闭包（避免循环变量捕获问题）。

    _failed_counter 是一个单元素列表 [int]，用于将 UPDATE 失败数写回外层累加器。
    rid, wconn, _dry_run, _would_change, _failed_counter 均在工厂调用时绑定，
    不受后续循环迭代影响。
    """
    def update_field(canonical_id, field, new_value):
        """执行（或模拟）单字段 UPDATE；dry_run 时只记录不写库。

        SQL 列名从硬编码字典 _UPDATABLE 取值（defense-in-depth：即使 field 已过白名单，
        插入 SQL 的仍是字面常量，而非运行时 LLM 字符串）。
        """
        # 二次白名单防御（apply 直接调 update_field；field 已是白名单过滤后）
        if field not in _UPDATABLE:
            logger.warning("update_field 二次防御：跳过不可改字段 %s", field)
            return
        # 取硬编码列名字面量——绝不将 LLM 字符串直接拼入 SQL
        col = _UPDATABLE[field]
        if _dry_run:
            msg = f"[DRY_RUN] 会将 id={rid} {col} 改为 {new_value!r}"
            logger.info(msg)
            _would_change.append({"id": rid, "field": col, "new_value": new_value})
            return
        try:
            with wconn:
                wconn.execute(
                    f"UPDATE extracted_projects SET {col}=? WHERE id=?",
                    (new_value, rid),
                )
            logger.info("已更新 id=%d %s → %r", rid, col, new_value)
        except sqlite3.Error as e:
            logger.error("UPDATE 失败 id=%d field=%s: %s", rid, col, e)
            _failed_counter[0] += 1

    return update_field


# ── Pydantic 入口参数校验 ────────────────────────────────────────────────────

class ReviewArgs(BaseModel):
    """CLI/函数入口参数校验：路径必须存在，阈值∈[0,1]。"""
    projects_db: str
    raw_news_db: str
    review_db: str
    dry_run: bool = False
    conf_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    llm_provider: str = "deepseek"
    concurrency: int = Field(default=1, ge=1)

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
    concurrency: int = 1,
    article_fn: Optional[Callable] = None,
    value_validator: Optional[Callable] = None,
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

    # 读所有行（兼容尚未迁移 is_unnamed_placeholder 列的旧库：缺列时以 0 兜底）
    conn = sqlite3.connect(str(projects_db))
    conn.row_factory = sqlite3.Row
    try:
        existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(extracted_projects)")}
        # 列名来自硬编码字面量 / PRAGMA，不含用户输入 → f-string 安全
        ph_sel = "is_unnamed_placeholder" if "is_unnamed_placeholder" in existing_cols else "0 AS is_unnamed_placeholder"
        rows = conn.execute(
            "SELECT id, project_name, category, capacity_mw, involvement_type, "
            f"implementing_entity, {ph_sel}, source_link "
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
    n_update_failed = 0
    run_reasons: dict[str, int] = {}
    # dry_run 时收集 would-change 记录（用于日志/report）
    would_change: list[dict] = []

    # ── Phase 1：构建候选（串行、无 LLM、无库写）────────────────────────────
    candidates: list[dict] = []
    for row in rows:
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
        candidates.append(
            {
                "row_id": row["id"],
                "source_link": row["source_link"] or "",
                "suspicious": suspicious,
                "full_project": {
                    "project_name": row["project_name"],
                    "category": row["category"],
                    "capacity_mw": row["capacity_mw"],
                    "involvement_type": row["involvement_type"],
                    "implementing_entity": row["implementing_entity"],
                    "source_link": row["source_link"] or "",
                },
            }
        )
    n_candidates = len(candidates)

    # ── Phase 2：取原文 + 批判（仅读 + LLM，无库写 → 可并发）──────────────────
    # 线程池仅承载 I/O/LLM；所有 sqlite 写入留到 Phase 3 主线程串行执行，保证线程安全。
    def _fetch_and_critic(cand: dict) -> dict:
        """单候选：取原文→批判；产出带 outcome 的结果（不抛已知错误，保证池不中断）。"""
        link, rid = cand["source_link"], cand["row_id"]
        try:
            if article_fn is not None:
                article = article_fn(link, db_path=raw_news_db)
            else:
                article = get_article(link, db_path=raw_news_db)
        except sqlite3.Error as e:
            logger.error("取原文失败 link=%s row_id=%d: %s", link, rid, e)
            return {**cand, "outcome": "no_article"}
        if article is None:
            logger.warning("找不到原文 link=%s row_id=%d，跳过 critic", link, rid)
            return {**cand, "outcome": "no_article"}
        try:
            fn = critic_fn if critic_fn is not None else critic.review_project
            verdicts = fn(article=article, project=cand["full_project"],
                          suspicious=cand["suspicious"], llm=llm)
        except (ValidationError, RuntimeError) as e:
            logger.error("critic 失败 link=%s row_id=%d: %s", link, rid, e)
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
                results[i] = fut.result()  # 已知错误已在 worker 内吞掉并标记 outcome

    # ── Phase 3：分流 + 库写（串行、主线程，sqlite 写线程安全）────────────────
    # 自动改写建议值的合法性校验器：默认走 bri_enums/bri_parse 校验（防写非法枚举/数值）
    validator = value_validator if value_validator is not None else _default_value_validator
    write_conn = sqlite3.connect(str(projects_db))
    try:
        for res in results:
            if res is None:
                continue
            if res["outcome"] == "no_article":
                n_no_article += 1
                continue
            if res["outcome"] == "critic_failed":
                n_critic_failed += 1
                continue

            row_id, source_link = res["row_id"], res["source_link"]

            # ── 白名单预过滤：去掉不允许改的字段 verdict ────────────────
            # 白名单外的 "wrong" 高置信 verdict 直接记警告，不让 apply 调 update_field
            allowed_verdicts = []
            for v in res["verdicts"]:
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
            # 用单元素列表作为可变容器，让闭包将失败数写回外层 n_update_failed
            _update_failed_counter = [0]
            update_field = _make_update_field(row_id, write_conn, dry_run, would_change, _update_failed_counter)

            # ── 分流 ─────────────────────────────────────────────────────
            result = apply.route(
                canonical_id=str(row_id),
                source_link=source_link,
                verdicts=allowed_verdicts,
                review_db=review_db,
                update_field=update_field,
                conf_threshold=conf_threshold,
                value_validator=validator,
            )
            n_auto_corrected += result["auto_corrected"]
            n_queued += result["queued"]
            n_update_failed += _update_failed_counter[0]

            # 从 apply.route 返回的 reasons 列表聚合（避免在此重新推导路由逻辑）
            for rtype in result.get("reasons", []):
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
        "n_update_failed": n_update_failed,
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
        "critic_failed=%d no_article=%d update_failed=%d dry_run=%s",
        n_candidates, n_auto_corrected, n_queued, n_critic_failed, n_no_article, n_update_failed, dry_run,
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
    parser.add_argument("--concurrency", type=int, default=1,
                        help="批判调用并发数（仅 LLM/读并发，库写仍串行）；deepseek v4-pro 建议 300-400、v4-flash 2000")
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
            concurrency=args.concurrency,
        )
    except ValidationError as e:
        print(f"参数校验失败:\n{e}")
        sys.exit(1)

    # 加载 .env（worktree 无独立 .env，向上搜索命中主仓库 .env），供批判 LLM 读取凭据
    from dotenv import find_dotenv, load_dotenv  # noqa: PLC0415
    load_dotenv(find_dotenv(usecwd=True))

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
        concurrency=validated.concurrency,
    )

    # 人机摘要（CLI 顶层 print 合规）
    print(
        f"\n=== run_review 完成 ===\n"
        f"  candidates    : {report['n_candidates']}\n"
        f"  auto_corrected: {report['n_auto_corrected']}\n"
        f"  queued        : {report['n_queued']}\n"
        f"  critic_failed : {report['n_critic_failed']}\n"
        f"  no_article    : {report['n_no_article']}\n"
        f"  update_failed : {report['n_update_failed']}\n"
        f"  dry_run       : {report['dry_run']}\n"
        f"  reasons       : {report['reasons']}"
    )


if __name__ == "__main__":
    main()
