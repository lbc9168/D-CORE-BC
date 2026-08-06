"""Run extraction evaluation and publish legacy-compatible plus rich-gold metric reports."""
import argparse
import hashlib
import inspect
import json
import logging
import os
import pathlib
from datetime import datetime

from eval import article_store, gold_loader
from eval.metrics import ALL_FIELDS, FIELD_PRIMARY_METRIC, LEGACY_FIELDS, score_article
from eval.predict import flat_prediction

logger = logging.getLogger(__name__)
_EVAL_DIR = pathlib.Path(__file__).resolve().parent
WEIGHTS = {"status": 0.25, "fields": 0.25, "entity_name": 0.25, "entity_role": 0.25}
_DEFAULT_MODEL = "gemini-2.5-flash-lite"
_STANDARD_SPLITS = ("development", "regression", "challenge")


def _default_extractor_version() -> str:
    """Hash the live extraction implementation and provider choices for cache isolation."""
    from s01b_project_extractor.stages import orchestrator
    provider_tag = (
        os.getenv("S01B_PROVIDER", "gemini") + os.getenv("S01B_MODEL", "")
        + os.getenv("DEEPSEEK_MODEL", "") + os.getenv("DS_MODEL", "")
        + os.getenv("S01B_STAGE_PROVIDERS", "") + os.getenv("S01B_MERGE_S34", "")
    )
    source = inspect.getsource(orchestrator) + (provider_tag or _DEFAULT_MODEL)
    for module_name in ("s1_relevance", "s2_basic", "s3_finance", "s4_entity", "s5_summary", "s34_entity_finance"):
        try:
            module = __import__(f"s01b_project_extractor.prompts.{module_name}", fromlist=["*"])
            source += inspect.getsource(module)
        except (ImportError, OSError):
            pass
    for module_name in ("capacity_parser", "energy_taxonomy", "stages.s2_basic", "stages.entity_normalize",
                        "stages.s4_entity", "stages.s5_summary"):
        try:
            module = __import__(f"s01b_project_extractor.{module_name}", fromlist=["*"])
            source += inspect.getsource(module)
        except (ImportError, OSError):
            pass
    try:
        from s01b_project_extractor.tools.project_extractor_tool import _SUPPORTING_DOCS
        source += hashlib.sha1((_SUPPORTING_DOCS / "company_hierarchy_v2.json").read_bytes()).hexdigest()
    except (ImportError, OSError):
        pass
    return hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]


def _build_real_extract_fn():
    """Build the configured production extractor only when an injected test extractor is absent."""
    from dotenv import find_dotenv, load_dotenv
    from s01b_project_extractor.llm.client import build_stage_router_from_env
    from s01b_project_extractor.stages.orchestrator import extract_staged
    load_dotenv(find_dotenv(usecwd=True))
    router = build_stage_router_from_env()
    return lambda title, content, date: extract_staged(title, content, date, llm=router)


def _ratio(correct, total) -> float:
    """Return a stable four-decimal ratio, falling back to zero for an empty denominator."""
    return round(correct / total, 4) if total else 0.0


def _empty_accumulator() -> dict:
    """Create independent counters for one whole report or deterministic report slice."""
    return {
        "n_gold": 0, "evaluated_count": 0, "n_status_correct": 0, "n_scored_articles": 0,
        "ps_matched": 0, "ps_pred": 0, "ps_gold": 0,
        "field_correct": {field: 0 for field in ALL_FIELDS},
        "field_total": {field: 0 for field in ALL_FIELDS},
        "source_tp": 0, "source_pred": 0, "source_gold": 0,
        "taxonomy_hits": {field: [0, 0] for field in ("storage_status", "storage_power_mw", "storage_energy_mwh", "proposed_category")},
        "name_f1_sum": 0.0, "name_f1_n": 0, "role_correct": 0, "role_matched": 0,
    }


def _register_record(accumulator: dict) -> None:
    """Reserve one gold record in an aggregate before extraction can skip or fail it."""
    accumulator["n_gold"] += 1


def _add_result(accumulator: dict, result: dict) -> None:
    """Add one already-scored article outcome to an aggregate without changing its denominator policy."""
    accumulator["evaluated_count"] += 1
    accumulator["n_status_correct"] += int(result["status_correct"])
    if not result["scored"]:
        return
    accumulator["n_scored_articles"] += 1
    matched, predicted, expected = result["project_set"]
    accumulator["ps_matched"] += matched
    accumulator["ps_pred"] += predicted
    accumulator["ps_gold"] += expected
    for field, (correct, total) in result["field_hits"].items():
        accumulator["field_correct"][field] += correct
        accumulator["field_total"][field] += total
    name_f1_sum, matched_projects, role_correct, role_matched = result["entity"]
    accumulator["name_f1_sum"] += name_f1_sum
    accumulator["name_f1_n"] += matched_projects
    accumulator["role_correct"] += role_correct
    accumulator["role_matched"] += role_matched
    true_positive, predicted_total, gold_total = result["taxonomy"]["generation_sources"]
    accumulator["source_tp"] += true_positive
    accumulator["source_pred"] += predicted_total
    accumulator["source_gold"] += gold_total
    for field, values in accumulator["taxonomy_hits"].items():
        correct, total = result["taxonomy"][field]
        values[0] += correct
        values[1] += total


def _weighted_total(status_accuracy: float, fields: dict, entity: dict, *, field_names: tuple[str, ...]) -> float:
    """Calculate a weighted headline using the explicit registry-primary metric for every field."""
    field_accuracies = [
        fields[field][FIELD_PRIMARY_METRIC[field]]
        for field in field_names
        if fields[field].get("total", fields[field].get("gold_total", 0))
    ]
    components = {
        "status": status_accuracy,
        "fields": sum(field_accuracies) / len(field_accuracies) if field_accuracies else None,
        "entity_name": entity["name_f1_mean"] if entity["matched_projects"] else None,
        "entity_role": entity["role_accuracy"] if entity["role_matched_total"] else None,
    }
    present = {name: value for name, value in components.items() if value is not None}
    weight_sum = sum(WEIGHTS[name] for name in present)
    return round(sum(WEIGHTS[name] * value for name, value in present.items()) / weight_sum, 4) if weight_sum else 0.0


def _summarize(accumulator: dict, *, include_taxonomy: bool = True) -> dict:
    """Materialize a stable aggregate payload from one counter set for reports and slices."""
    precision = _ratio(accumulator["ps_matched"], accumulator["ps_pred"])
    recall = _ratio(accumulator["ps_matched"], accumulator["ps_gold"])
    project_f1 = round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0
    fields = {
        field: {
            "correct": accumulator["field_correct"][field], "total": accumulator["field_total"][field],
            "accuracy": _ratio(accumulator["field_correct"][field], accumulator["field_total"][field]),
            "primary_metric": FIELD_PRIMARY_METRIC[field],
        }
        for field in ALL_FIELDS
    }
    source_precision = _ratio(accumulator["source_tp"], accumulator["source_pred"])
    source_recall = _ratio(accumulator["source_tp"], accumulator["source_gold"])
    source_f1 = (
        round(2 * source_precision * source_recall / (source_precision + source_recall), 4)
        if source_precision + source_recall
        else _ratio(accumulator["field_correct"]["generation_sources"], accumulator["field_total"]["generation_sources"])
    )
    fields["generation_sources"] = {
        "true_positive": accumulator["source_tp"], "pred_total": accumulator["source_pred"],
        "gold_total": accumulator["source_gold"], "precision": source_precision,
        "recall": source_recall, "f1": source_f1,
        "correct": accumulator["field_correct"]["generation_sources"],
        "total": accumulator["field_total"]["generation_sources"],
        "accuracy": _ratio(accumulator["field_correct"]["generation_sources"], accumulator["field_total"]["generation_sources"]),
        "primary_metric": FIELD_PRIMARY_METRIC["generation_sources"],
    }
    entity = {
        "name_f1_mean": round(accumulator["name_f1_sum"] / accumulator["name_f1_n"], 4) if accumulator["name_f1_n"] else 0.0,
        "role_accuracy": _ratio(accumulator["role_correct"], accumulator["role_matched"]),
        "role_matched_total": accumulator["role_matched"], "matched_projects": accumulator["name_f1_n"],
    }
    status_accuracy = _ratio(accumulator["n_status_correct"], accumulator["evaluated_count"])
    summary = {
        "n_gold": accumulator["n_gold"], "status_accuracy": status_accuracy,
        "evaluated_count": accumulator["evaluated_count"],
        "n_scored_articles": accumulator["n_scored_articles"],
        "project_set": {"matched": accumulator["ps_matched"], "pred_total": accumulator["ps_pred"], "gold_total": accumulator["ps_gold"],
                        "precision": precision, "recall": recall, "f1": project_f1},
        "fields": fields, "entity": entity,
        "weighted_total": _weighted_total(status_accuracy, fields, entity, field_names=ALL_FIELDS),
        "legacy_weighted_total": _weighted_total(status_accuracy, fields, entity, field_names=LEGACY_FIELDS),
    }
    if include_taxonomy:
        summary["taxonomy"] = {
            "generation_sources": {"true_positive": accumulator["source_tp"], "pred_total": accumulator["source_pred"], "gold_total": accumulator["source_gold"],
                                   "precision": source_precision, "recall": source_recall, "f1": source_f1},
            **{field: {"correct": values[0], "total": values[1], "accuracy": _ratio(*values)}
               for field, values in accumulator["taxonomy_hits"].items()},
        }
    return summary


def _find_prev_report(reports_dir: pathlib.Path, exclude: pathlib.Path):
    """Find the newest previous JSON report, excluding the one currently being written."""
    candidates = [path for path in reports_dir.glob("*.json") if path.resolve() != exclude.resolve()]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def _metric_rows(report: dict):
    """Expose the stable legacy comparison rows for terminal output and report diffs."""
    rows = [("status_accuracy", lambda item: item.get("status_accuracy")),
            ("project_set.f1", lambda item: (item.get("project_set") or {}).get("f1"))]
    rows.extend(
        (field, lambda item, field=field: ((item.get("fields") or {}).get(field) or {}).get(FIELD_PRIMARY_METRIC[field]))
        for field in ALL_FIELDS
    )
    rows.extend([
        ("entity.name_f1_mean", lambda item: (item.get("entity") or {}).get("name_f1_mean")),
        ("entity.role_accuracy", lambda item: (item.get("entity") or {}).get("role_accuracy")),
        ("weighted_total", lambda item: item.get("weighted_total")),
    ])
    return rows


def _print_comparison(report: dict, previous: dict | None) -> None:
    """Print current legacy headline metrics and a compact comparison with the previous report."""
    print("=" * 60)
    print(f"评测报告 {report['timestamp']}  version={report['extractor_version']}")
    print(f"n_gold={report['n_gold']} n_scored_articles={report['n_scored_articles']} n_failed={report['n_failed']} n_skipped={report['n_skipped']}")
    for label, getter in _metric_rows(report):
        current = getter(report)
        prior = getter(previous) if previous else None
        current_text = f"{current:.4f}" if isinstance(current, (int, float)) else "n/a"
        prior_text = f" (prev {prior:.4f})" if isinstance(prior, (int, float)) else ""
        print(f"  {label:<24} {current_text}{prior_text}")
    print("=" * 60)
    logger.info("评测完成 legacy_weighted_total=%.4f n_scored_articles=%d", report["weighted_total"], report["n_scored_articles"])


def _slice_categories(record: dict) -> list[str]:
    """Return sorted unique gold categories so a multi-project article is deterministic in slices."""
    return sorted({project.get("category") or "unspecified" for project in record.get("projects") or []}) or ["unspecified"]


def _slice_split(record: dict) -> str:
    """Normalize standard split spellings while retaining observed custom split labels verbatim."""
    value = str(record.get("dataset_split") or "unspecified").strip()
    return value.casefold() if value.casefold() in _STANDARD_SPLITS else value or "unspecified"


def _slice_risk_tags(record: dict) -> list[str]:
    """Return sorted unique risk labels, assigning untagged records to the explicit unspecified slice."""
    return sorted(set(record.get("risk_tags") or [])) or ["unspecified"]


def run_eval(gold_path, *, extract_fn=None, raw_news_db=None, cache_dir=None, reports_dir=None,
             use_cache=True, extractor_version=None) -> dict:
    """Extract, score, aggregate, and persist one rich-gold evaluation report without mutating source data."""
    raw_news_db = raw_news_db if raw_news_db is not None else article_store.DEFAULT_RAW_NEWS
    cache_dir = cache_dir if cache_dir is not None else article_store.DEFAULT_CACHE_DIR
    reports_dir = pathlib.Path(reports_dir) if reports_dir is not None else _EVAL_DIR / "reports"
    extractor_version = extractor_version or _default_extractor_version()
    extract_fn = extract_fn or _build_real_extract_fn()
    gold_records = gold_loader.load(gold_path)
    overall, regression, challenge = _empty_accumulator(), _empty_accumulator(), _empty_accumulator()
    split_slices = {split: _empty_accumulator() for split in _STANDARD_SPLITS}
    category_slices, risk_slices = {"unspecified": _empty_accumulator()}, {"unspecified": _empty_accumulator()}
    predeclared = []
    for record in gold_records:
        split, categories, risk_tags = _slice_split(record), _slice_categories(record), _slice_risk_tags(record)
        _register_record(overall)
        _register_record(split_slices.setdefault(split, _empty_accumulator()))
        for category in categories:
            _register_record(category_slices.setdefault(category, _empty_accumulator()))
        for risk_tag in risk_tags:
            _register_record(risk_slices.setdefault(risk_tag, _empty_accumulator()))
        if split == "regression":
            _register_record(regression)
        elif split == "challenge":
            _register_record(challenge)
        predeclared.append((record, split, categories, risk_tags))
    n_failed = n_skipped = 0
    for record, split, categories, risk_tags in predeclared:
        article = article_store.get_article(record["source_link"], db_path=raw_news_db)
        if article is None:
            logger.warning("run_eval 跳过：raw_news 库无此原文 url=%s", record["source_link"])
            n_skipped += 1
            continue
        result = None
        if use_cache:
            result = article_store.cache_get(article_store.cache_key(record["source_link"], extractor_version), cache_dir)
        if result is None:
            try:
                result = extract_fn(article["title"], article["content"], article["publish_date"])
            except Exception as error:
                logger.error("run_eval 提取失败 url=%s error=%s", record["source_link"], error, exc_info=True)
                n_failed += 1
                continue
            if use_cache:
                article_store.cache_put(article_store.cache_key(record["source_link"], extractor_version), result, cache_dir)
        outcome = score_article(flat_prediction(result), record)
        _add_result(overall, outcome)
        _add_result(split_slices[split], outcome)
        for category in categories:
            _add_result(category_slices[category], outcome)
        for risk_tag in risk_tags:
            _add_result(risk_slices[risk_tag], outcome)
        if split == "regression":
            _add_result(regression, outcome)
        elif split == "challenge":
            _add_result(challenge, outcome)
    report = _summarize(overall)
    report.update({
        "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"), "extractor_version": extractor_version,
        "n_gold": len(gold_records), "n_failed": n_failed, "n_skipped": n_skipped,
        "weighted_total": report["legacy_weighted_total"], "weighted_total_label": "legacy",
        "core_metrics": {"representative": _summarize(regression), "regression": _summarize(regression), "challenge": _summarize(challenge)},
        "slices": {
            "split": {key: _summarize(split_slices[key]) for key in sorted(split_slices)},
            "category": {key: _summarize(category_slices[key]) for key in sorted(category_slices)},
            "risk_tag": {key: _summarize(risk_slices[key]) for key in sorted(risk_slices)},
        },
    })
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / f"{report['timestamp']}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    previous = None
    previous_path = _find_prev_report(reports_dir, out_path)
    if previous_path:
        try:
            previous = json.loads(previous_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as error:
            logger.error("run_eval 读取上次报告失败 path=%s error=%s", previous_path, error)
    _print_comparison(report, previous)
    return report


def _configure_logging() -> None:
    """Configure CLI-only file and console logging without affecting imported evaluation tests."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                        handlers=[logging.FileHandler(_EVAL_DIR / "run_eval.log", encoding="utf-8"), logging.StreamHandler()])


def main() -> None:
    """Parse CLI options and launch one cached or uncached evaluation run."""
    parser = argparse.ArgumentParser(description="s01b 提取器金标准评测")
    parser.add_argument("--gold", default=str(_EVAL_DIR / "gold_set.jsonl"))
    parser.add_argument("--raw-news-db", default=None)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--reports-dir", default=None)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()
    _configure_logging()
    run_eval(args.gold, raw_news_db=args.raw_news_db, cache_dir=args.cache_dir, reports_dir=args.reports_dir, use_cache=not args.no_cache)


if __name__ == "__main__":
    main()
