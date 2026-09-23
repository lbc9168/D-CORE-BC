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
from eval.metrics import score_article, PROJECT_ENUM_FIELDS, PROJECT_NUMERIC_FIELDS
from eval.predict import flat_prediction

logger = logging.getLogger(__name__)

# eval 目录（用于默认 reports 目录与日志文件定位）
_EVAL_DIR = pathlib.Path(__file__).resolve().parent

# weighted_total 四分量等权；可调。某分量无数据时丢弃并对剩余分量重新归一化。
WEIGHTS = {"status": 0.25, "fields": 0.25, "entity_name": 0.25, "entity_role": 0.25}

# 所有逐字段评分字段（枚举 + 数值），用于初始化累加器与报告。
ALL_FIELDS = PROJECT_ENUM_FIELDS + PROJECT_NUMERIC_FIELDS

# 默认提取模型（与 cache 版本哈希、真实提取器绑定保持一致）
_DEFAULT_MODEL = "gemini-2.5-flash-lite"


def _llm_routing_snapshot() -> dict:
    """返回不含密钥的有效 provider/model 路由，供缓存哈希和评测报告追溯。"""
    from s01b_project_extractor.llm.client import (
        _config_for_provider,
        _parse_stage_models,
        _parse_stage_providers,
    )

    default_provider = os.getenv("S01B_PROVIDER", "gemini").strip().lower()
    provider_overrides = _parse_stage_providers(os.getenv("S01B_STAGE_PROVIDERS", ""))
    model_overrides = _parse_stage_models(os.getenv("S01B_STAGE_MODELS", ""))
    default_cfg = _config_for_provider(default_provider)
    stages = {}
    for stage in sorted(set(provider_overrides) | set(model_overrides)):
        provider = provider_overrides.get(stage, default_provider)
        cfg = _config_for_provider(provider, model_overrides.get(stage, ""))
        stages[stage] = {"provider": cfg.provider, "model": cfg.model}
    return {
        "profile": os.getenv("S01B_EVAL_PROFILE", "").strip(),
        "default": {"provider": default_cfg.provider, "model": default_cfg.model},
        "stages": stages,
        "merge_s34": os.getenv("S01B_MERGE_S34", "").strip(),
        "openai_reasoning_effort": os.getenv("OPENAI_REASONING_EFFORT", "").strip(),
    }


def _default_extractor_version() -> str:
    """由 orchestrator + 各阶段 prompt + 确定性解析层源码 + provider/model 算 sha1[:12]，任一变更即让缓存失效（供应商间缓存隔离）。"""
    from s01b_project_extractor.stages import orchestrator  # 延迟导入

    # provider/model + 分阶段路由 纳入哈希：各供应商/混合配置各自独立缓存、报告可比
    provider_tag = json.dumps(_llm_routing_snapshot(), ensure_ascii=False, sort_keys=True)
    src = inspect.getsource(orchestrator) + (provider_tag or _DEFAULT_MODEL)
    # 各阶段 prompt 模块加入哈希（存在才纳入，便于逐 Phase 增量上线）
    for modname in ("policy_fragments", "s1_relevance", "s2_basic", "s3_finance", "s4_entity", "s5_summary", "s34_entity_finance"):
        try:
            mod = __import__(f"s01b_project_extractor.prompts.{modname}", fromlist=["*"])
            src += inspect.getsource(mod)
        except (ImportError, OSError):
            pass
    # 确定性解析层（capacity_parser + s2_basic stage 后处理 + entity_normalize + s4/s5 stage）纳入哈希：
    # 解析器修复应使旧 eval 缓存失效（否则 parser-only 改动不能触发重跑）
    for modname in ("capacity_parser", "energy_taxonomy", "energy_taxonomy_v2", "stages.s1_relevance", "stages.s2_basic", "stages.entity_normalize",
                    "stages.s4_entity", "stages.s34_entity_finance", "stages.evidence_binding",
                    "stages.entity_response", "stages.capacity_evidence", "stages.orchestrator", "llm.client",
                    "guardrails.developer_check", "stages.s5_summary"):
        try:
            mod = __import__(f"s01b_project_extractor.{modname}", fromlist=["*"])
            src += inspect.getsource(mod)
        except (ImportError, OSError):
            pass
    # 实体登记册数据文件纳入哈希：company_hierarchy_v2.json 决定 entity_normalize 的解析结果，
    # 登记册内容变更（消歧/补录）同样必须使旧 eval 缓存失效
    try:
        from s01b_project_extractor.tools.project_extractor_tool import _SUPPORTING_DOCS
        src += hashlib.sha1(
            (_SUPPORTING_DOCS / "company_hierarchy_v2.json").read_bytes()
        ).hexdigest()
        src += hashlib.sha1(
            (_SUPPORTING_DOCS.parent / "developer_registry.json").read_bytes()
        ).hexdigest()
    except (ImportError, OSError):
        pass  # 与上方模块缺失同口径：文件/模块不可用时跳过，不阻塞 eval
    return hashlib.sha1(src.encode("utf-8")).hexdigest()[:12]


def _build_real_extract_fn():
    """构造调用真实 LLM 的 extract_fn；provider 由 S01B_PROVIDER 决定（gemini 默认/deepseek）；缺 key/model 构造即抛。"""
    from dotenv import find_dotenv, load_dotenv  # 延迟导入
    load_dotenv(find_dotenv(usecwd=True))  # worktree 无独立 .env，向上搜索命中主仓库 .env
    from s01b_project_extractor.llm.client import build_stage_router_from_env  # 延迟导入
    from s01b_project_extractor.stages.orchestrator import extract_staged  # 延迟导入
    # 支持分阶段路由（S01B_STAGE_PROVIDERS，如 s3:deepseek,s4:deepseek,s5:deepseek）；缺 key/model 构造即抛
    router = build_stage_router_from_env()
    return lambda t, c, d: extract_staged(t, c, d, llm=router)


def _ratio(c, t) -> float:
    """安全比率：分母为 0 时返回 0.0，否则四舍五入到 4 位。"""
    return round(c / t, 4) if t else 0.0


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


def _metric_rows(report: dict):
    """从报告里抽出 (label, getter) 列表：status/字段/项目集F1/实体/加权总分。"""
    rows = [
        ("status_accuracy", lambda r: r.get("status_accuracy")),
        ("project_set.f1", lambda r: (r.get("project_set") or {}).get("f1")),
    ]
    for f in ALL_FIELDS:
        rows.append((f, lambda r, f=f: ((r.get("fields") or {}).get(f) or {}).get("accuracy")))
    rows += [
        ("entity.name_f1_mean", lambda r: (r.get("entity") or {}).get("name_f1_mean")),
        ("entity.role_accuracy", lambda r: (r.get("entity") or {}).get("role_accuracy")),
        ("weighted_total", lambda r: r.get("weighted_total")),
    ]
    return rows


def _print_comparison(report: dict, prev: dict | None) -> None:
    """打印本次各指标，并在有上次报告时给出与上次的 ↑/↓/= 对比（人机界面，rule#2 允许）。"""
    print("=" * 60)
    print(f"评测报告 {report['timestamp']}  version={report['extractor_version']}")
    print(f"n_gold={report['n_gold']} n_scored_articles={report['n_scored_articles']} "
          f"n_failed={report['n_failed']} n_skipped={report['n_skipped']}")
    print("-" * 60)

    for label, getter in _metric_rows(report):
        cur = getter(report)
        cur_s = f"{cur:.4f}" if isinstance(cur, (int, float)) else "n/a"
        if prev is None:
            print(f"  {label:<24} {cur_s}")
            continue
        pv = getter(prev)
        if isinstance(cur, (int, float)) and isinstance(pv, (int, float)):
            print(f"  {label:<24} {cur_s}  {_arrow(cur, pv)} (prev {pv:.4f})")
        else:
            print(f"  {label:<24} {cur_s}  (prev n/a)")

    print("=" * 60)
    if prev is None:
        print("no previous report to compare")
    logger.info(
        "评测完成 weighted_total=%.4f status_acc=%.4f name_f1=%.4f n_scored_articles=%d",
        report["weighted_total"], report["status_accuracy"],
        report["entity"]["name_f1_mean"], report["n_scored_articles"],
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
    n_status_correct = 0
    n_scored_articles = 0
    ps_matched = ps_pred = ps_gold = 0
    fld_correct = {f: 0 for f in ALL_FIELDS}
    fld_total = {f: 0 for f in ALL_FIELDS}
    name_f1_sum = 0.0
    name_f1_n = 0
    role_correct = role_matched = 0
    tax_source_tp = tax_source_pred = tax_source_gold = 0
    tax_hits = {
        "storage_status": [0, 0],
        "storage_power_mw": [0, 0],
        "storage_energy_mwh": [0, 0],
        "proposed_category": [0, 0],
    }

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

        pred = flat_prediction(result)        # 文章级对象 {status, projects}
        r = score_article(pred, rec)          # rec 即 gold_loader 加载的文章 dict
        if r["status_correct"]:
            n_status_correct += 1
        if r["scored"]:
            n_scored_articles += 1
            m_, p_, g_ = r["project_set"]
            ps_matched += m_; ps_pred += p_; ps_gold += g_
            for f, (c, t) in r["field_hits"].items():
                fld_correct[f] += c; fld_total[f] += t
            nf1_sum, n_matched, rc, rm = r["entity"]
            name_f1_sum += nf1_sum; name_f1_n += n_matched
            role_correct += rc; role_matched += rm
            taxonomy = r["taxonomy"]
            tp, pred_n, gold_n = taxonomy["generation_sources"]
            tax_source_tp += tp; tax_source_pred += pred_n; tax_source_gold += gold_n
            for key in tax_hits:
                c, t = taxonomy[key]
                tax_hits[key][0] += c; tax_hits[key][1] += t

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    n_eval = n_gold - n_skipped - n_failed  # 实际参与 status 判定的记录数
    status_accuracy = _ratio(n_status_correct, n_eval)
    ps_precision = _ratio(ps_matched, ps_pred)
    ps_recall = _ratio(ps_matched, ps_gold)
    ps_f1 = round(2 * ps_precision * ps_recall / (ps_precision + ps_recall), 4) if (ps_precision + ps_recall) else 0.0
    fields = {f: {"correct": fld_correct[f], "total": fld_total[f], "accuracy": _ratio(fld_correct[f], fld_total[f])}
              for f in ALL_FIELDS}
    name_f1_mean = round(name_f1_sum / name_f1_n, 4) if name_f1_n else 0.0
    role_accuracy = _ratio(role_correct, role_matched)
    tax_source_precision = _ratio(tax_source_tp, tax_source_pred)
    tax_source_recall = _ratio(tax_source_tp, tax_source_gold)
    tax_source_f1 = (
        round(2 * tax_source_precision * tax_source_recall / (tax_source_precision + tax_source_recall), 4)
        if tax_source_precision + tax_source_recall else 0.0
    )
    taxonomy_report = {
        "generation_sources": {
            "true_positive": tax_source_tp,
            "pred_total": tax_source_pred,
            "gold_total": tax_source_gold,
            "precision": tax_source_precision,
            "recall": tax_source_recall,
            "f1": tax_source_f1,
        },
        **{
            key: {"correct": values[0], "total": values[1], "accuracy": _ratio(*values)}
            for key, values in tax_hits.items()
        },
    }

    field_accs = [v["accuracy"] for v in fields.values() if v["total"]]
    components = {
        "status": status_accuracy if n_eval else None,
        "fields": (sum(field_accs) / len(field_accs)) if field_accs else None,
        "entity_name": name_f1_mean if name_f1_n else None,
        "entity_role": role_accuracy if role_matched else None,
    }
    present = {k: v for k, v in components.items() if v is not None}
    wsum = sum(WEIGHTS[k] for k in present)
    weighted_total = round(sum(WEIGHTS[k] * v for k, v in present.items()) / wsum, 4) if wsum else 0.0

    report = {
        "timestamp": timestamp,
        "extractor_version": extractor_version,
        "llm_routing": _llm_routing_snapshot(),
        "n_gold": n_gold,
        "n_failed": n_failed,
        "n_skipped": n_skipped,
        "status_accuracy": status_accuracy,
        "n_scored_articles": n_scored_articles,
        "project_set": {"matched": ps_matched, "pred_total": ps_pred, "gold_total": ps_gold,
                        "precision": ps_precision, "recall": ps_recall, "f1": ps_f1},
        "fields": fields,
        "entity": {"name_f1_mean": name_f1_mean, "role_accuracy": role_accuracy, "role_matched_total": role_matched},
        "taxonomy": taxonomy_report,
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
