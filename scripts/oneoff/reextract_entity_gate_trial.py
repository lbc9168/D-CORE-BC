"""Bounded, source-preserving trial of historical entity-gate re-extraction.

Reads explicitly selected raw IDs; uses the existing production provider route.
Only output artifacts are written. Source review decisions are never deleted.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "s01b_project_extractor/src"), str(ROOT)]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ids", type=int, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--run-models", action="store_true")
    args = parser.parse_args()
    if not 1 <= len(set(args.ids)) <= 10 or any(value <= 0 for value in args.ids):
        parser.error("Select 1-10 distinct positive raw IDs")
    out = args.out.resolve()
    if not out.is_relative_to((ROOT / "output").resolve()) or out.exists():
        parser.error("Use a new directory under this workspace's output")
    sources = [ROOT / name for name in (
        "s01a_news_crawler/data/raw_news.db", "s01b_project_extractor/data/projects.db",
        "s01b_project_extractor/data/review.db", "s02a_duplicate_merger/data/db/bri_re_database.db",
    )]
    before = {str(path.relative_to(ROOT)): digest(path) for path in sources}
    conn = sqlite3.connect(sources[0].as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in conn.execute(
            "SELECT id,title,content,publish_date,url FROM raw_news WHERE id IN ("
            + ",".join("?" for _ in args.ids) + ") ORDER BY id", args.ids)]
    finally:
        conn.close()
    if {row["id"] for row in rows} != set(args.ids):
        raise ValueError("Requested source ID is missing")
    out.mkdir(parents=True)
    logging.basicConfig(filename=out / "run.log", level=logging.INFO, encoding="utf-8")
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from s01b_project_extractor.llm.client import (
        build_stage_router_from_env, LLMClient,
    )
    from s01b_project_extractor.stages.orchestrator import extract_staged
    # Do not emit credentials or full configuration objects.
    route_info = build_stage_router_from_env(_client_factory=lambda cfg: {
        "provider": cfg.provider, "model": cfg.model})
    manifest = {"raw_ids": args.ids, "sources_before": before,
                "route": {"default": route_info._default, "stages": route_info._by_stage},
                "max_stage_calls": 24,
                "run_models": args.run_models,
                "code_hashes": {str(path.relative_to(ROOT)): digest(path) for path in
                    (ROOT / "s01b_project_extractor/src/s01b_project_extractor").rglob("*.py")},
                "registry_hashes": {name: digest(ROOT / name) for name in (
                    "developer_registry.json", "supporting_docs/company_hierarchy_v2.json")}}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.run_models:
        print(json.dumps({"articles": len(rows), "source_characters": sum(len(r["content"]) for r in rows),
                          "route": manifest["route"], "max_stage_calls": 24}, ensure_ascii=False))
        return
    def trial_client(cfg):
        cfg.max_retries = 1  # The explicit 24-call budget includes SDK requests.
        return LLMClient(cfg)
    router = build_stage_router_from_env(_client_factory=trial_client)
    calls = []

    class RecordedRouter:
        def complete(self, prompt, *, stage, json_mode=True):
            if len(calls) >= 24:
                raise RuntimeError("Trial call limit reached")
            record = {"stage": stage, "prompt": prompt}
            calls.append(record)
            result = router.complete(prompt, stage=stage, json_mode=json_mode)
            record["result"] = deepcopy(result)  # Stages normalize their returned dicts in place.
            (out / "calls.json").write_text(json.dumps(calls, ensure_ascii=False, indent=2), encoding="utf-8")
            return result

    results = []
    try:
        for row in rows:
            result = extract_staged(row["title"], row["content"], row["publish_date"], llm=RecordedRouter())
            results.append({"source": row, "result": result})
            (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"id": row["id"], "status": result["status"],
                              "projects": len(result.get("projects", []))}, ensure_ascii=False), flush=True)
    finally:
        after = {str(path.relative_to(ROOT)): digest(path) for path in sources}
        (out / "source-verification.json").write_text(json.dumps({"before": before, "after": after,
                "unchanged": before == after, "stage_calls": len(calls)}, indent=2), encoding="utf-8")
        if before != after:
            raise RuntimeError("Production source changed during isolated trial")


if __name__ == "__main__":
    main()
