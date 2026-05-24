"""run_eval：在金标准集上跑 s01b 提取器，按字段类型打分、聚合、写报告并与上次报告对比。"""
import argparse
import hashlib
import inspect
import json
import logging
import os
import pathlib
from datetime import datetime

from eval import article_store, gold_loader
from eval.metrics import ENUM_FIELDS, NUMERIC_FIELDS, score_record
from eval.predict import flat_prediction

logger = logging.getLogger(__name__)

# eval 目录（用于默认 reports 目录与日志文件定位）
_EVAL_DIR = pathlib.Path(__file__).resolve().parent

# weighted_total 四分量等权；可调。某分量无数据时丢弃并对剩余分量重新归一化。
WEIGHTS = {"status": 0.25, "enum": 0.25, "numeric": 0.25, "entity": 0.25}

# 默认提取模型（与 cache 版本哈希、真实提取器绑定保持一致）
_DEFAULT_MODEL = "gemini-2.5-flash-lite"


def _default_extractor_version() -> str:
    """由 _build_prompt 源码 + 模型名算 sha1[:12]，prompt 一变即自动让缓存失效。"""
    from s01b_project_extractor.tools.project_extractor_tool import _build_prompt  # 延迟导入：仅默认版本号才需要

    src = inspect.getsource(_build_prompt) + _DEFAULT_MODEL
    return hashlib.sha1(src.encode("utf-8")).hexdigest()[:12]


def _build_real_extract_fn():
    """构造调用真实 Gemini 的 extract_fn；缺 GEMINI_API_KEY 时明确报错。"""
    from google import genai  # 延迟导入：仅真实运行才需要
    from s01b_project_extractor.tools.project_extractor_tool import extract_one  # 延迟导入：仅真实运行才需要

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("未找到 GEMINI_API_KEY 环境变量，无法构建真实提取器（请检查 .env）")
    client = genai.Client(api_key=api_key)
    return lambda t, c, d: extract_one(t, c, d, client=client)


def _mean(values) -> float | None:
    """对非空数值列表求均值，空列表返回 None（供加权时丢弃该分量）。"""
    return sum(values) / len(values) if values else None


def _compute_weighted_total(status_acc, enum_accs, numeric_accs, entity_f1_mean) -> float:
    """按 WEIGHTS 加权四分量；无数据的分量丢弃后对剩余权重重新归一化。"""
    components = {
        "status": status_acc,
        "enum": _mean(enum_accs),
        "numeric": _mean(numeric_accs),
        "entity": entity_f1_mean,
    }
    present = {k: v for k, v in components.items() if v is not None}
    if not present:
        return 0.0
    wsum = sum(WEIGHTS[k] for k in present)
    if wsum == 0:
        return 0.0
    return sum(WEIGHTS[k] * v for k, v in present.items()) / wsum


def _find_prev_report(reports_dir: pathlib.Path, exclude: pathlib.Path):
    """返回 reports_dir 中除 exclude 外最近修改的报告 json（无则 None）。"""
    candidates = [
        p for p in reports_dir.glob("*.json")
        if p.resolve() != exclude.resolve()
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _arrow(cur: float, prev: float) -> str:
    """返回当前值相对上次值的方向箭头：↑ / ↓ / =。"""
    if cur > prev:
        return "↑"
    if cur < prev:
        return "↓"
    return "="


def _print_comparison(report: dict, prev: dict | None) -> None:
    """打印本次各指标，并在有上次报告时给出与上次的 ↑/↓/= 对比（人机界面，rule#2 允许）。"""
    print("=" * 60)
    print(f"评测报告 {report['timestamp']}  version={report['extractor_version']}")
    print(f"n_gold={report['n_gold']} n_scored={report['n_scored']} "
          f"n_failed={report['n_failed']} n_skipped={report['n_skipped']}")
    print("-" * 60)

    def line(label: str, key_path):
        cur = key_path(report)
        if prev is None:
            print(f"  {label:<24} {cur:.4f}")
        else:
            try:
                pv = key_path(prev)
                print(f"  {label:<24} {cur:.4f}  {_arrow(cur, pv)} (prev {pv:.4f})")
            except (KeyError, TypeError):
                print(f"  {label:<24} {cur:.4f}  (prev n/a)")

    line("status_accuracy", lambda r: r["status_accuracy"])
    for f in ENUM_FIELDS + NUMERIC_FIELDS:
        line(f, lambda r, f=f: r["fields"][f]["accuracy"])
    line("entity_f1_mean", lambda r: r["entity_f1_mean"])
    line("weighted_total", lambda r: r["weighted_total"])
    print("=" * 60)
    if prev is None:
        print("no previous report to compare")
    logger.info(
        "评测完成 weighted_total=%.4f status_acc=%.4f entity_f1=%.4f n_scored=%d",
        report["weighted_total"], report["status_accuracy"],
        report["entity_f1_mean"], report["n_scored"],
    )


def run_eval(gold_path, *, extract_fn=None, raw_news_db=None, cache_dir=None,
             reports_dir=None, use_cache=True, extractor_version=None) -> dict:
    """跑完整评测：取原文→(缓存或)提取→扁平化→打分→聚合→写报告→对比上次，返回聚合 dict。"""
    raw_news_db = raw_news_db if raw_news_db is not None else article_store.DEFAULT_RAW_NEWS
    cache_dir = cache_dir if cache_dir is not None else article_store.DEFAULT_CACHE_DIR
    reports_dir = pathlib.Path(reports_dir) if reports_dir is not None else _EVAL_DIR / "reports"
    if extractor_version is None:
        extractor_version = _default_extractor_version()
    if extract_fn is None:
        extract_fn = _build_real_extract_fn()

    gold = gold_loader.load(gold_path)
    n_gold = len(gold)
    n_failed = 0
    n_skipped = 0
    status_correct = 0
    field_correct = {f: 0 for f in ENUM_FIELDS + NUMERIC_FIELDS}
    field_total = {f: 0 for f in ENUM_FIELDS + NUMERIC_FIELDS}
    entity_f1s: list[float] = []
    n_scored = 0

    for rec in gold:
        link = rec["source_link"]
        art = article_store.get_article(link, db_path=raw_news_db)
        if art is None:
            logger.warning("run_eval 跳过：raw_news 库无此原文 url=%s", link)
            n_skipped += 1
            continue

        result = None
        if use_cache:
            key = article_store.cache_key(link, extractor_version)
            cached = article_store.cache_get(key, cache_dir)
            if cached is not None:
                result = cached
        if result is None:
            try:
                result = extract_fn(art["title"], art["content"], art["publish_date"])
            except Exception as e:  # 单篇失败记录上下文后继续，不中断整轮（spec）
                logger.error("run_eval 提取失败 url=%s error=%s", link, e, exc_info=True)
                n_failed += 1
                continue
            if use_cache:
                article_store.cache_put(article_store.cache_key(link, extractor_version), result, cache_dir)

        pred = flat_prediction(result)
        rec_score = score_record(pred, rec["labels"])
        if rec_score["status_correct"]:
            status_correct += 1
        if rec_score["fields"]:  # 仅当 status 正确且 gold==valid 才有字段分
            n_scored += 1
            for f, ok in rec_score["fields"].items():
                field_total[f] += 1
                if ok:
                    field_correct[f] += 1
        if rec_score["entity_prf"] is not None:
            entity_f1s.append(rec_score["entity_prf"][2])

    n_eval = n_gold - n_skipped - n_failed  # 实际参与 status 判定的记录数
    status_accuracy = status_correct / n_eval if n_eval else 0.0
    fields_report = {}
    enum_accs, numeric_accs = [], []
    for f in ENUM_FIELDS + NUMERIC_FIELDS:
        acc = field_correct[f] / field_total[f] if field_total[f] else 0.0
        fields_report[f] = {"correct": field_correct[f], "total": field_total[f], "accuracy": acc}
        if field_total[f]:
            (enum_accs if f in ENUM_FIELDS else numeric_accs).append(acc)
    entity_f1_mean = _mean(entity_f1s)
    weighted_total = _compute_weighted_total(
        status_accuracy if n_eval else None, enum_accs, numeric_accs, entity_f1_mean
    )

    report = {
        "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "extractor_version": extractor_version,
        "n_gold": n_gold,
        "n_scored": n_scored,
        "n_failed": n_failed,
        "n_skipped": n_skipped,
        "status_accuracy": status_accuracy,
        "fields": fields_report,
        "entity_f1_mean": entity_f1_mean if entity_f1_mean is not None else 0.0,
        "weighted_total": weighted_total,
    }

    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / f"{report['timestamp']}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info("run_eval 报告已写入 %s", out_path)

    prev_path = _find_prev_report(reports_dir, exclude=out_path)
    prev = None
    if prev_path is not None:
        try:
            with open(prev_path, "r", encoding="utf-8") as f:
                prev = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("run_eval 读取上次报告失败 path=%s error=%s", prev_path, e)
    _print_comparison(report, prev)
    return report


def _configure_logging() -> None:
    """仅在 CLI 入口配置 logging：同时输出到 eval/run_eval.log 与控制台。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(_EVAL_DIR / "run_eval.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def main() -> None:
    """CLI 入口：解析参数、配置日志、调用 run_eval。"""
    parser = argparse.ArgumentParser(description="s01b 提取器金标准评测")
    parser.add_argument("--gold", default=str(_EVAL_DIR / "gold_set.jsonl"), help="金标准 jsonl 路径")
    parser.add_argument("--raw-news-db", default=None, help="raw_news.db 路径（默认 s01a）")
    parser.add_argument("--cache-dir", default=None, help="提取结果缓存目录")
    parser.add_argument("--reports-dir", default=None, help="报告输出目录")
    parser.add_argument("--no-cache", action="store_true", help="禁用缓存，强制重新提取")
    args = parser.parse_args()

    _configure_logging()
    run_eval(
        args.gold,
        raw_news_db=args.raw_news_db,
        cache_dir=args.cache_dir,
        reports_dir=args.reports_dir,
        use_cache=not args.no_cache,
    )


if __name__ == "__main__":
    main()
