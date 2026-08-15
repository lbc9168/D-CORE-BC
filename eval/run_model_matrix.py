"""在固定早期 Gold 子集上运行可复现的 Gemini、OpenAI Nano 与 DeepSeek 模型组合评测。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import random
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime

from dotenv import find_dotenv, load_dotenv

from eval import gold_loader
from eval.archived_gold import build_archived_development_set
from eval.article_store import cache_get, cache_key
from eval.metrics import (
    PROJECT_ENUM_FIELDS,
    PROJECT_NUMERIC_FIELDS,
    _name_sim,
    cmp_enum,
    cmp_numeric,
    score_entities,
)
from eval.predict import flat_prediction
from eval.run_eval import run_eval


_ROOT = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
_DEFAULT_GOLD = _ROOT / "eval" / "gold_set.jsonl"
_DEFAULT_RUNS = _ROOT / "eval" / "model_matrix_runs"
_DEEPSEEK_STAGES = "s3:deepseek,s4:deepseek,s5:deepseek,s34:deepseek"
_POLICY_STABLE_FIELDS = ("project_stage", "financing_model", "capacity_mw", "total_investment_usd")


@dataclass(frozen=True)
class ModelProfile:
    """描述一组不含密钥的默认模型、阶段 provider 和阶段模型覆盖。"""

    name: str
    default_provider: str
    default_model: str
    stage_providers: str = _DEEPSEEK_STAGES
    stage_models: str = ""
    openai_reasoning_effort: str = ""


PROFILES = {
    profile.name: profile
    for profile in (
        ModelProfile("baseline-gemini25-deepseek", "gemini", "gemini-2.5-flash-lite"),
        ModelProfile("gemini35-deepseek", "gemini", "gemini-3.5-flash-lite"),
        ModelProfile("gpt5nano-deepseek", "openai", "gpt-5-nano"),
        ModelProfile("gpt54nano-deepseek", "openai", "gpt-5.4-nano"),
        ModelProfile(
            "gemini35s1-gemini25s2-gpt54nano",
            "gemini",
            "gemini-2.5-flash-lite",
            stage_providers="s34:openai,s5:openai",
            stage_models="s1:gemini-3.5-flash-lite,s34:gpt-5.4-nano,s5:gpt-5.4-nano",
        ),
        ModelProfile(
            "mixed-nanos-deepseek",
            "openai",
            "gpt-5-nano",
            stage_providers="s3:deepseek,s4:deepseek,s34:deepseek,s5:openai",
            stage_models="s2:gpt-5.4-nano,s5:gpt-5.4-nano",
        ),
    )
}


def _discover_raw_news_db() -> pathlib.Path:
    """优先返回 worktree 数据库，否则定位同一主仓库中的 raw_news.db。"""
    candidates = [_ROOT / "s01a_news_crawler" / "data" / "raw_news.db"]
    if len(_ROOT.parents) >= 3:
        candidates.append(_ROOT.parents[2] / "s01a_news_crawler" / "data" / "raw_news.db")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def select_stratified_sample(records: list[dict], sample_size: int, seed: int) -> list[dict]:
    """固定随机种子按 status 分层抽样，并保证存在时覆盖 valid/irrelevant/no_chinese。"""
    if sample_size <= 0 or sample_size >= len(records):
        return list(records)
    rng = random.Random(seed)
    groups: dict[str, list[dict]] = {}
    for record in records:
        groups.setdefault(record["status"], []).append(record)

    chosen: list[dict] = []
    reserved_statuses = [status for status in ("irrelevant", "no_chinese") if groups.get(status)]
    for status in reserved_statuses:
        if len(chosen) < sample_size:
            chosen.append(rng.choice(groups[status]))

    valid_slots = sample_size - len(chosen)
    valid_pool = groups.get("valid", [])
    chosen.extend(rng.sample(valid_pool, min(valid_slots, len(valid_pool))))

    if len(chosen) < sample_size:
        selected_links = {record["source_link"] for record in chosen}
        remaining = [record for record in records if record["source_link"] not in selected_links]
        chosen.extend(rng.sample(remaining, sample_size - len(chosen)))
    return sorted(chosen, key=lambda record: record["source_link"])


def _write_jsonl(path: pathlib.Path, records: list[dict]) -> None:
    """以 UTF-8 JSONL 保存本轮固定样本，不修改源 Gold。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def article_locked_diagnostics(records: list[dict], cache_dir: pathlib.Path, extractor_version: str) -> dict:
    """对一文一项目样本按 URL 强制对齐，隔离旧项目名阈值对字段分数的干扰。"""
    fields = PROJECT_ENUM_FIELDS + PROJECT_NUMERIC_FIELDS
    correct = {field: 0 for field in fields}
    total = {field: 0 for field in fields}
    eligible = paired = 0
    name_scores: list[float] = []
    entity_f1: list[float] = []
    role_correct = role_total = 0

    for gold in records:
        if gold.get("status") != "valid" or len(gold.get("projects") or []) != 1:
            continue
        eligible += 1
        raw = cache_get(cache_key(gold["source_link"], extractor_version), cache_dir)
        if raw is None:
            continue
        pred = flat_prediction(raw)
        if pred.get("status") != "valid" or len(pred.get("projects") or []) != 1:
            continue
        paired += 1
        predicted_project = pred["projects"][0]
        gold_project = gold["projects"][0]
        name_scores.append(_name_sim(predicted_project.get("project_name"), gold_project.get("project_name")))
        for field in PROJECT_ENUM_FIELDS:
            total[field] += 1
            correct[field] += int(cmp_enum(field, predicted_project.get(field), gold_project.get(field)))
        for field in PROJECT_NUMERIC_FIELDS:
            total[field] += 1
            correct[field] += int(cmp_numeric(predicted_project.get(field), gold_project.get(field)))
        _precision, _recall, f1, roles_ok, roles_n = score_entities(
            predicted_project.get("entities") or [], gold_project.get("entities") or []
        )
        entity_f1.append(f1)
        role_correct += roles_ok
        role_total += roles_n

    stable_correct = sum(correct[field] for field in _POLICY_STABLE_FIELDS)
    stable_total = sum(total[field] for field in _POLICY_STABLE_FIELDS)
    return {
        "eligible_single_project_articles": eligible,
        "paired_single_project_articles": paired,
        "pair_recall": round(paired / eligible, 4) if eligible else 0.0,
        "project_name_similarity_mean": round(sum(name_scores) / len(name_scores), 4) if name_scores else 0.0,
        "fields": {
            field: {
                "correct": correct[field],
                "total": total[field],
                "accuracy": round(correct[field] / total[field], 4) if total[field] else 0.0,
            }
            for field in fields
        },
        "policy_stable_fields": {
            "fields": list(_POLICY_STABLE_FIELDS),
            "correct": stable_correct,
            "total": stable_total,
            "accuracy": round(stable_correct / stable_total, 4) if stable_total else 0.0,
            "note": "排除旧/新 energy taxonomy 最敏感的 category；仍需结合逐字段结果解释。",
        },
        "entity_name_f1_mean": round(sum(entity_f1) / len(entity_f1), 4) if entity_f1 else 0.0,
        "entity_role_accuracy": round(role_correct / role_total, 4) if role_total else 0.0,
    }


@contextmanager
def profile_environment(profile: ModelProfile):
    """临时应用模型组合环境变量，并在单个 profile 完成后完整恢复。"""
    values = {
        "S01B_EVAL_PROFILE": profile.name,
        "S01B_PROVIDER": profile.default_provider,
        "S01B_MODEL": profile.default_model if profile.default_provider == "gemini" else None,
        "OPENAI_MODEL": profile.default_model if profile.default_provider == "openai" else None,
        "OPENAI_REASONING_EFFORT": profile.openai_reasoning_effort,
        "S01B_STAGE_PROVIDERS": profile.stage_providers,
        "S01B_STAGE_MODELS": profile.stage_models or None,
        "S01B_MERGE_S34": "1",
    }
    previous = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run_matrix(
    *,
    gold_path: pathlib.Path,
    raw_news_db: pathlib.Path,
    output_dir: pathlib.Path,
    profiles: list[ModelProfile],
    sample_size: int,
    seed: int,
    use_cache: bool,
) -> dict:
    """运行模型矩阵并写入逐 profile 报告及一个横向摘要。"""
    load_dotenv(find_dotenv(usecwd=True), override=False)
    records = gold_loader.load(gold_path)
    sample = select_stratified_sample(records, sample_size, seed)
    sample_path = output_dir / "sample.jsonl"
    _write_jsonl(sample_path, sample)

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_gold": str(gold_path.resolve()),
        "source_gold_sha256": hashlib.sha256(gold_path.read_bytes()).hexdigest(),
        "sample_path": str(sample_path.resolve()),
        "sample_sha256": hashlib.sha256(sample_path.read_bytes()).hexdigest(),
        "sample_size": len(sample),
        "sample_status_counts": {
            status: sum(record["status"] == status for record in sample)
            for status in sorted({record["status"] for record in sample})
        },
        "seed": seed,
        "raw_news_db": str(raw_news_db.resolve()),
        "profiles": [],
    }

    for profile in profiles:
        started = time.perf_counter()
        print(f"\n### profile={profile.name} sample={len(sample)}")
        entry = {"config": asdict(profile)}
        try:
            with profile_environment(profile):
                report = run_eval(
                    sample_path,
                    raw_news_db=raw_news_db,
                    cache_dir=output_dir / "cache" / profile.name,
                    reports_dir=output_dir / "reports" / profile.name,
                    use_cache=use_cache,
                )
            entry["report"] = report
            entry["article_locked_diagnostics"] = article_locked_diagnostics(
                sample,
                output_dir / "cache" / profile.name,
                report["extractor_version"],
            )
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            print(f"profile failed: {entry['error']}")
        entry["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        summary["profiles"].append(entry)

    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nmatrix summary: {summary_path}")
    return summary


def main() -> None:
    """解析 CLI 参数并运行已登记的低成本模型组合。"""
    parser = argparse.ArgumentParser(description="Run a reproducible S01B model matrix on early Gold")
    parser.add_argument("--gold", type=pathlib.Path, default=_DEFAULT_GOLD)
    parser.add_argument(
        "--archive-review-db",
        type=pathlib.Path,
        default=None,
        help="从暂停封存 review.db 只读生成 confirmed-only development benchmark",
    )
    parser.add_argument("--raw-news-db", type=pathlib.Path, default=_discover_raw_news_db())
    parser.add_argument("--output-dir", type=pathlib.Path, default=None)
    parser.add_argument("--sample-size", type=int, default=8)
    parser.add_argument("--seed", type=int, default=20260814)
    parser.add_argument("--profiles", default=",".join(PROFILES))
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    names = [name.strip() for name in args.profiles.split(",") if name.strip()]
    unknown = [name for name in names if name not in PROFILES]
    if unknown:
        parser.error(f"unknown profiles: {', '.join(unknown)}")
    output_dir = args.output_dir or _DEFAULT_RUNS / datetime.now().strftime("%Y%m%d_%H%M%S")
    gold_path = args.gold
    raw_news_db = args.raw_news_db
    if args.archive_review_db is not None:
        archived = build_archived_development_set(args.archive_review_db, output_dir / "benchmark")
        gold_path = archived["gold_path"]
        raw_news_db = archived["raw_news_path"]
    run_matrix(
        gold_path=gold_path,
        raw_news_db=raw_news_db,
        output_dir=output_dir,
        profiles=[PROFILES[name] for name in names],
        sample_size=args.sample_size,
        seed=args.seed,
        use_cache=not args.no_cache,
    )


if __name__ == "__main__":
    main()
