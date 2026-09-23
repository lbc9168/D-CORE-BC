"""Isolated recovery validation: frozen replay or bounded existing-provider live run.

The output directory must be new. Never removes production decisions/import flags.
Raw response text is saved before JSON parsing; all SQLite writes stay under output.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "s01b_project_extractor/src"), str(ROOT / "s02a_duplicate_merger/src")]
SOURCES = [ROOT / value for value in (
    "s01a_news_crawler/data/raw_news.db", "s01b_project_extractor/data/projects.db",
    "s01b_project_extractor/data/review.db", "s02a_duplicate_merger/data/db/bri_re_database.db",
)]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def fingerprints():
    from s01b_project_extractor.tools.entity_intake import _fingerprint
    return {str(path.relative_to(ROOT)): _fingerprint(path) for path in SOURCES}


def persist_isolated(records: list[dict], out: Path) -> dict:
    """Exercise the actual S01B writer and S02A merge without any external calls."""
    import pandas as pd
    from s01b_project_extractor.tools import project_extractor_tool as writer
    from s02a_duplicate_merger.tools.duplicate_merger_tool import (
        _init_db, _load_from_db, _group_into_logical_projects, _write_to_db, _mark_imported,
    )
    def forbid(*args, **kwargs):
        raise AssertionError("No model/translation/cache writes allowed during isolated persistence")
    source, review = out / "projects.db", out / "review.db"
    country_db = writer._load_country_db(writer._DEFAULT_COUNTRY_DB)
    with patch.object(writer, "_resolve_entity_en", lambda name, client: ""), \
         patch.object(writer, "_resolve_developer_en", lambda name, client: name), \
         patch.object(writer, "_save_name_cache", forbid), patch.object(writer.genai, "Client", forbid):
        for item in records:
            if "error" in item:
                continue
            row = item["source"]
            frame = pd.DataFrame([{"原文链接": row["url"], "文章标题": row["title"],
                                   "正文内容": row["content"], "发布日期": row["publish_date"]}])
            with patch.object(writer, "extract_one", lambda *a, **k: deepcopy(item["result"])):
                writer.ProjectExtractorTool()._process_df(
                    frame, batch_id="entity-recovery", ts="20260923", client=None,
                    country_db=country_db, projects_db_path=str(source), review_db_path=str(review), max_workers=1,
                )
    if not source.exists():
        return {"canonical_new": 0, "event_new": 0, "entity_new": 0, "no_extracted_database": True}
    loaded, ids = _load_from_db(str(source))
    projects = _group_into_logical_projects(loaded)
    main = _init_db(str(out / "main.db"))
    try:
        counts = _write_to_db(main, projects)
        _mark_imported(str(source), ids)
        tables = ("canonical_projects", "project_events", "chinese_entities")
        before = {t: main.execute(f'SELECT * FROM "{t}" ORDER BY id').fetchall() for t in tables}
        repeat = _write_to_db(main, projects)
        after = {t: main.execute(f'SELECT * FROM "{t}" ORDER BY id').fetchall() for t in tables}
        result = {"counts": counts, "repeat": repeat, "all_rows_unchanged_on_repeat": before == after,
                  "integrity": main.execute("PRAGMA integrity_check").fetchall(),
                  "foreign_keys": main.execute("PRAGMA foreign_key_check").fetchall()}
        assert before == after and result["foreign_keys"] == []
        assert all(repeat[key] == 0 for key in ("canonical_new", "event_new", "entity_new"))
        with sqlite3.connect(source) as conn:
            conn.row_factory = sqlite3.Row
            write_json(out / "extracted-rows.json", [dict(r) for r in conn.execute("SELECT * FROM extracted_projects ORDER BY id")])
        write_json(out / "pipeline-verification.json", result)
        return result
    finally:
        main.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("offline", "live"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sample-audit", type=Path)
    parser.add_argument("--ids", type=int, nargs="+")
    parser.add_argument("--max-calls", type=int, default=60)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists() or not out.is_relative_to(ROOT / "output"):
        parser.error("Use a new directory beneath this workspace output")
    if not 1 <= args.max_calls <= 60:
        parser.error("At most 60 requests per bounded pilot")
    out.mkdir(parents=True)
    logging.basicConfig(filename=out / "run.log", level=logging.INFO, encoding="utf-8")
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from s01b_project_extractor.llm.client import build_stage_router_from_env, LLMClient
    from s01b_project_extractor.stages.orchestrator import extract_staged
    before = fingerprints()
    route = build_stage_router_from_env(_client_factory=lambda c: {"provider": c.provider, "model": c.model})
    if args.mode == "offline":
        fixtures = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "tests/fixtures/entity_recovery_20260923").glob("*.json"))]
        rows = [f["source"] for f in fixtures]
    else:
        if not args.sample_audit:
            parser.error("Live requires a frozen sample audit with evidence and expectations")
        audit = json.loads(args.sample_audit.read_text(encoding="utf-8"))
        rows = [s["source"] for s in audit["samples"] if not args.ids or s["raw_id"] in args.ids]
        if not 1 <= len(rows) <= 10:
            parser.error("Live sample must contain 1-10 articles")
        write_json(out / "frozen-sample-audit.json", audit)
    manifest = {"mode": args.mode, "raw_ids": [r["id"] for r in rows], "source_before": before,
                "max_requests": args.max_calls, "route": {"default": route._default, "stages": route._by_stage},
                "merge_s34": bool(os.getenv("S01B_MERGE_S34")),
                "code_hashes": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (ROOT / "s01b_project_extractor/src/s01b_project_extractor").rglob("*.py")}}
    write_json(out / "manifest.json", manifest)
    write_json(out / "articles.json", rows)
    calls, records = [], []
    current_id = None

    class RecordedClient(LLMClient):
        def complete(self, prompt, *, stage, json_mode=True):
            self.active_stage = stage
            return super().complete(prompt, stage=stage, json_mode=json_mode)

        def _raw_call(self, prompt, json_mode):
            if len(calls) >= args.max_calls:
                raise RuntimeError("Frozen request limit reached")
            number = len(calls) + 1
            record = {"number": number, "raw_id": current_id, "stage": self.active_stage,
                      "provider": self.cfg.provider, "model": self.cfg.model}
            calls.append(record)
            (out / f"request-{number:03}.txt").write_text(prompt, encoding="utf-8")
            write_json(out / "calls.json", calls)
            response = super()._raw_call(prompt, json_mode)
            # Immutable separate text artifact, before JSON decoding or stage mutation.
            with (out / f"response-{number:03}.txt").open("x", encoding="utf-8") as handle:
                handle.write(response or "")
            record["response_file"] = f"response-{number:03}.txt"
            write_json(out / "calls.json", calls)
            return response
    def factory(cfg):
        cfg.max_retries = 1
        return RecordedClient(cfg)
    router = build_stage_router_from_env(_client_factory=factory) if args.mode == "live" else None
    class FrozenRouter:
        def __init__(self, responses):
            self.responses = responses
        def complete(self, prompt, *, stage, json_mode=True):
            calls.append({"raw_id": current_id, "stage": stage, "mode": "reconstructed_fixture"})
            return deepcopy(self.responses[stage])
    try:
        for row in rows:
            current_id = row["id"]
            llm = router or FrozenRouter(next(f["responses"] for f in fixtures if f["source"]["id"] == current_id))
            try:
                result = extract_staged(row["title"], row["content"], row["publish_date"], llm=llm)
                records.append({"source": row, "result": result})
                print(json.dumps({"id": current_id, "status": result["status"], "projects": len(result.get("projects", []))}), flush=True)
            except (RuntimeError, ValueError, TypeError, KeyError) as exc:
                records.append({"source": row, "error": str(exc), "payload": getattr(exc, "payload", None)})
                logging.exception("Extraction failure raw_id=%s", current_id)
                print(json.dumps({"id": current_id, "error": str(exc)}, ensure_ascii=False), flush=True)
            write_json(out / "results.json", records)
        persist_isolated(records, out)
    finally:
        after = fingerprints()
        write_json(out / "source-verification.json", {"before": before, "after": after, "unchanged": before == after,
                    "actual_requests": len(calls) if args.mode == "live" else 0})
        assert before == after, "Production source changed during isolated validation"


if __name__ == "__main__":
    main()
