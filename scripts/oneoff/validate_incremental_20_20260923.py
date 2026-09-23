"""Bounded live validation of twenty frozen new articles, entirely under output.

Uses normal pipeline stages. Only SDK auditing and mutable cache destinations are
wrapped; no extraction/merge responses are replaced. Repeats can forbid all calls.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), *[str(ROOT / p / "src") for p in (
    "pipeline", "s01a_news_crawler", "s01b_project_extractor", "s02a_duplicate_merger",
    "s02b_entity_normalizer", "s03_data_exporter")]]
from entity_recovery_validation import fingerprints, write_json


def snapshot(path):
    if not path.exists():
        return {}
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {t: conn.execute(f'SELECT * FROM "{t}" ORDER BY id').fetchall()
                for t in ("canonical_projects", "project_events", "chinese_entities", "non_project_records") if t in tables}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--phase", choices=("live", "resume", "repeat"), required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if not out.is_relative_to(ROOT / "output"):
        parser.error("Output must be below workspace output")
    audit = json.loads((out / "frozen-samples.json").read_text(encoding="utf-8"))
    samples = audit["samples"]
    if len(samples) != 20 or len({s["source"]["url"] for s in samples}) != 20:
        parser.error("Exactly twenty distinct frozen articles required")
    with sqlite3.connect(f"file:{(out / 'raw.db').as_posix()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        for sample in samples:
            expected = sample["source"]
            actual = dict(conn.execute("SELECT * FROM raw_news WHERE id=?", (expected["id"],)).fetchone())
            if any(actual[k] != expected[k] for k in ("id", "url", "title", "content", "publish_date")):
                raise RuntimeError(f"Frozen article changed: {expected['id']}")
    phase_dir = out / args.phase
    phase_dir.mkdir(exist_ok=False)
    work = out / "work"
    work.mkdir(exist_ok=True)
    logging.basicConfig(filename=phase_dir / "run.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from google import genai
    from google.genai import models as gemini_models, types
    from openai.resources.chat.completions import Completions
    from s01b_project_extractor.llm.client import LLMClient, build_stage_router_from_env
    from s01b_project_extractor.tools import project_extractor_tool as writer
    from s01b_project_extractor import main as bulk
    from s02b_entity_normalizer import en_backfill, en_translate_residual
    from bri_pipeline.flow import PipelineConfig, run_pipeline

    route = build_stage_router_from_env(_client_factory=lambda c: {"provider": c.provider, "model": c.model})
    safe_route = {"default": route._default, "stages": route._by_stage}
    allowed = {("gemini", "gemini-2.5-flash-lite"), ("deepseek", "deepseek-v4-flash")}
    for value in [safe_route["default"], *safe_route["stages"].values()]:
        if (value["provider"], value["model"]) not in allowed:
            raise RuntimeError(f"Route differs from authorized existing providers: {value}")
    support = work / "supporting_docs"
    support.mkdir(exist_ok=True)
    for name in ("company_name_cache.json", "company_hierarchy.json", "company_hierarchy_v2.json", "soe.md", "residual_translation_cache.json"):
        if not (support / name).exists() and (ROOT / "supporting_docs" / name).exists():
            shutil.copy2(ROOT / "supporting_docs" / name, support / name)
    protected = [ROOT / "supporting_docs" / n for n in ("company_name_cache.json", "residual_translation_cache.json")]
    protected += list((ROOT / "s04_web_visualization/public/data").rglob("*"))
    protected = [p for p in protected if p.is_file()]
    asset_before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    before = fingerprints()
    stable = snapshot(work / "main.db")
    cfg = PipelineConfig(skip_s01a=True, raw_news_db_path=str(out / "raw.db"),
        projects_db_path=str(work / "projects.db"), review_db_path=str(work / "review.db"),
        db_path=str(work / "main.db"), raw_ids=[s["source"]["id"] for s in samples],
        max_extract_workers=2, run_export=True, export_dir=str(work / "export"),
        export_web_dir=str(work / "web/data"), export_cache_path=str(work / "province-cache.json"))
    manifest = {"phase": args.phase, "mode": "live_sdk_calls", "route": safe_route,
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "sample_sha256": hashlib.sha256((out / "frozen-samples.json").read_bytes()).hexdigest(),
        "total_request_limit": 180, "per_article_request_limit": 14,
        "repeat_all_external_calls_forbidden": args.phase == "repeat",
        "authorization": "User explicitly approved a twenty-article live incremental validation with existing services; no production writes.",
        "collection": "Separate live S01A collection before sample freezing, 2026-09-16 through 2026-09-23",
        "merge_s34": os.getenv("S01B_MERGE_S34"), "config": vars(cfg),
        "code_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for module in ("pipeline", "s01a_news_crawler", "s01b_project_extractor", "s02a_duplicate_merger", "s02b_entity_normalizer", "s03_data_exporter")
            for p in (ROOT / module / "src").rglob("*.py")}}
    write_json(phase_dir / "manifest.json", manifest)
    lock, local = threading.Lock(), threading.local()
    calls_path = out / "calls.json"
    calls = json.loads(calls_path.read_text(encoding="utf-8")) if calls_path.exists() else []
    initial_calls = len(calls)
    blocked_calls = []
    responses = out / "responses"
    responses.mkdir(exist_ok=True)
    original_gemini = gemini_models.Models.generate_content
    original_chat = Completions.create
    original_client = genai.Client
    original_complete, original_extract = LLMClient.complete, writer.extract_one
    by_article = {(s["source"]["title"], s["source"]["content"], s["source"]["publish_date"]): s["source"] for s in samples}
    assert len(by_article) == 20

    def recorded(provider, model, request, invoke):
        if (provider, model) not in allowed:
            raise RuntimeError(f"Unapproved provider/model: {provider}/{model}")
        raw_id = getattr(local, "raw_id", None)
        with lock:
            article_calls = sum(c.get("raw_id") == raw_id for c in calls) if raw_id else 0
            if args.phase == "repeat" or len(calls) >= 180 or article_calls >= 14:
                blocked_calls.append({"provider": provider, "model": model, "raw_id": raw_id,
                                      "stage": getattr(local, "stage", "auxiliary")})
                write_json(phase_dir / "blocked-calls.json", blocked_calls)
                raise RuntimeError("Frozen live request limit reached; preserve partial outputs for review")
            number = len(calls) + 1
            record = {"number": number, "phase": args.phase, "raw_id": raw_id,
                      "stage": getattr(local, "stage", "auxiliary"), "provider": provider,
                      "model": model, "status": "started", "started": time.time()}
            calls.append(record)
            write_json(responses / f"request-{number:03}.json", request)
            write_json(calls_path, calls)
        started = time.monotonic()
        try:
            response = invoke()
            write_json(responses / f"response-{number:03}.json", response.model_dump(mode="json"))
            raw_text = response.text if provider == "gemini" else response.choices[0].message.content
            with (responses / f"response-{number:03}.txt").open("x", encoding="utf-8") as f:
                f.write(raw_text or "")
            usage = response.usage_metadata if provider == "gemini" else response.usage
            record.update(status="completed", response_file=f"response-{number:03}.txt",
                          usage=usage.model_dump(mode="json") if usage else None)
            return response
        except Exception as exc:
            # Audit boundary only: record and rethrow every failure, never classify it as no project.
            record.update(status="failed", error_type=type(exc).__name__, error=str(exc))
            raise
        finally:
            with lock:
                record["elapsed_seconds"] = round(time.monotonic() - started, 3)
                write_json(calls_path, calls)

    def gemini_call(self, **kwargs):
        request = {"model": kwargs["model"], "contents": kwargs.get("contents"),
                   "config": str(kwargs.get("config"))}
        return recorded("gemini", kwargs["model"], request, lambda: original_gemini(self, **kwargs))

    def chat_call(self, *args, **kwargs):
        host = str(self._client.base_url)
        if "api.deepseek.com" not in host:
            raise RuntimeError("Unexpected compatible API endpoint; refusing expanded disclosure")
        return recorded("deepseek", kwargs["model"], {k:v for k,v in kwargs.items()},
                        lambda: original_chat(self, *args, **kwargs))

    def bounded_client(*args, **kwargs):
        if "http_options" not in kwargs:
            kwargs["http_options"] = types.HttpOptions(timeout=120000, retry_options=types.HttpRetryOptions(attempts=1))
        return original_client(*args, **kwargs)

    def complete(self, prompt, *, stage, json_mode=True):
        old = getattr(local, "stage", "auxiliary")
        local.stage = stage
        try:
            return original_complete(self, prompt, stage=stage, json_mode=json_mode)
        finally:
            local.stage = old

    def extract(title, content, date, **kwargs):
        row = by_article[(title, content, date)]
        local.raw_id = row["id"]
        try:
            result = original_extract(title, content, date, **kwargs)
            write_json(phase_dir / f"article-{row['id']}.json", {"source": row, "result": deepcopy(result)})
            return result
        finally:
            local.raw_id = None

    started = time.monotonic()
    result = None
    try:
        with ExitStack() as stack:
            for target, name, value in (
                (gemini_models.Models, "generate_content", gemini_call), (Completions, "create", chat_call),
                (genai, "Client", bounded_client), (LLMClient, "complete", complete),
                (bulk, "_RUN_LOG_PATH", phase_dir / "run.log"),
                (writer, "extract_one", extract), (writer, "_SUPPORTING_DOCS", support),
                (en_backfill, "DEFAULT_CACHE", str(support / "company_name_cache.json")),
                (en_translate_residual, "DEFAULT_CACHE", str(support / "company_name_cache.json")),
                (en_translate_residual, "DEFAULT_RESIDUAL_CACHE", str(support / "residual_translation_cache.json"))):
                stack.enter_context(patch.object(target, name, value))
            result = run_pipeline(cfg)
        write_json(phase_dir / "result.json", result)
    finally:
        after = fingerprints()
        asset_after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
        current = snapshot(work / "main.db")
        verification = {"source_unchanged": before == after, "before": before, "after": after,
            "production_assets_unchanged": asset_before == asset_after,
            "actual_requests": len(calls) - initial_calls, "total_requests": len(calls),
            "blocked_call_attempts": len(blocked_calls),
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "core_unchanged": stable == current, "counts": {t:len(rows) for t,rows in current.items()}}
        if (work / "main.db").exists():
            with sqlite3.connect(work / "main.db") as conn:
                verification.update(integrity=conn.execute("PRAGMA integrity_check").fetchall(),
                                    foreign_keys=conn.execute("PRAGMA foreign_key_check").fetchall())
        write_json(phase_dir / "verification.json", verification)
        assert before == after and asset_before == asset_after, "Production source or protected assets changed"
        if args.phase == "repeat":
            assert stable == current, "Core tables changed on repeat"
            assert not blocked_calls, "Repeat still attempted external calls; inspect blocked-calls.json"
        print(json.dumps({"result_status": result.get("status") if result else "failed", **verification}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
