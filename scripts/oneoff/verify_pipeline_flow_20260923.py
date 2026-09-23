"""Exercise the normal pipeline with immutable recorded responses and no network.

First run injects one article failure and one noncritical summary failure. The next
run must process only the failed article; the third must leave core tables intact.
This validates orchestration, not new-model accuracy or live crawler connectivity.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "pipeline/src"), str(ROOT / "s01b_project_extractor/src")]
from replay_entity_recovery_responses import project_key
from entity_recovery_validation import fingerprints, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists() or not out.is_relative_to(ROOT / "output"):
        parser.error("Use a new directory under output")
    out.mkdir(parents=True)
    before = fingerprints()
    original = ROOT / "output/entity-recovery-20260923"
    samples = json.loads((original / "sample-audit.json").read_text(encoding="utf-8"))["samples"]
    by_title = {sample["source"]["title"]: sample["source"] for sample in samples}
    raw = out / "raw.db"
    with sqlite3.connect(raw) as connection:
        connection.execute("CREATE TABLE raw_news(id INTEGER PRIMARY KEY,url TEXT,title TEXT,content TEXT,"
                           "publish_date TEXT,batch_id TEXT,passed_keyword_filter INTEGER)")
        connection.executemany("INSERT INTO raw_news VALUES(?,?,?,?,?,'replay',1)",
            [(r["id"], r["url"], r["title"], r["content"], r["publish_date"]) for r in by_title.values()])
    responses = {}
    for name in ("live", "live-targeted", "live-corrective"):
        folder = original / name
        for call in json.loads((folder / "calls.json").read_text(encoding="utf-8")):
            if not call.get("response_file"):
                continue
            prompt = (folder / f"request-{call['number']:03}.txt").read_text(encoding="utf-8")
            key = (call["raw_id"], call["stage"], project_key(prompt, call["stage"]))
            path = folder / call["response_file"]
            responses[key] = {"text": path.read_text(encoding="utf-8"), "path": str(path),
                              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    from bri_pipeline import flow
    from s01b_project_extractor import main as bulk
    from s01b_project_extractor.tools import project_extractor_tool as writer
    from s01b_project_extractor.stages.orchestrator import extract_staged
    from s01b_project_extractor.llm.client import LLMClient, LLMResponseParseError
    from s02a_duplicate_merger.tools import duplicate_merger_tool as merger

    used, attempts, optional_requests = [], [], []
    phase = ["first"]
    class Replay:
        def __init__(self, raw_id):
            self.raw_id = raw_id
        def complete(self, prompt, *, stage, json_mode=True):
            if self.raw_id == 27311 and stage == "s5":
                raise RuntimeError("Injected noncritical summary service failure")
            key = (self.raw_id, stage, project_key(prompt, stage))
            source = responses[key]
            used.append({"key": key, "response_path": source["path"], "sha256": source["sha256"]})
            try:
                return deepcopy(json.loads(source["text"]))
            except json.JSONDecodeError as exc:
                raise LLMResponseParseError(str(exc), source["text"]) from exc
    def extract(title, content, date, **kwargs):
        row = by_title[title]
        attempts.append({"phase": phase[0], "raw_id": row["id"]})
        if phase[0] == "first" and row["id"] == 27654:
            raise RuntimeError("Injected retryable article service failure")
        return extract_staged(title, content, date, llm=Replay(row["id"]))
    def forbidden(*args, **kwargs):
        raise AssertionError("Network/cache mutation forbidden in offline entry replay")
    class NoNetwork:
        @property
        def models(self):
            return self
        def generate_content(self, **kwargs):
            # Optional location inference may ask for model help. Return no inferred
            # facts; all entity/capacity model responses come from the pinned files.
            if "请从以下项目名称中提取" in str(kwargs.get("contents", "")):
                optional_requests.append("location_inference_empty_fixture")
                return type("Response", (), {"text": '{"results": []}'})()
            forbidden()
    config = flow.PipelineConfig(
        skip_s01a=True, raw_news_db_path=str(raw), projects_db_path=str(out / "projects.db"),
        review_db_path=str(out / "review.db"), db_path=str(out / "main.db"), max_extract_workers=1,
        run_location_normalize=False, run_developer_normalize=False, run_residual_translate=False,
        run_export=True, export_dir=str(out / "export"), export_web_dir=str(out / "web/data"),
        export_allow_model_calls=False,
    )
    def snapshot():
        with sqlite3.connect(out / "main.db") as connection:
            return {table: connection.execute(f'SELECT * FROM "{table}" ORDER BY id').fetchall()
                    for table in ("canonical_projects", "project_events", "chinese_entities")}
    try:
        with patch.dict(os.environ, {"S01B_MERGE_S34": "1", "GEMINI_API_KEY": "offline-replay-unused"}), \
             patch.object(writer, "extract_one", extract), \
             patch.object(writer.genai, "Client", lambda **kwargs: NoNetwork()), \
             patch.object(writer, "_resolve_entity_en", lambda name, client: ""), \
             patch.object(writer, "_resolve_developer_en", lambda name, client: name), \
             patch.object(writer, "_save_name_cache", forbidden), \
             patch.object(LLMClient, "_raw_call", forbidden), \
             patch.object(bulk, "_setup_run_logging", lambda: out / "run.log"):
            first = flow.run_pipeline(config)
            assert first["status"] == "partial"
            assert len(first["extraction"]["failed_urls"]) == 1
            phase[0] = "resume"
            resumed = flow.run_pipeline(config)
            assert resumed["status"] == "completed"
            assert [a["raw_id"] for a in attempts if a["phase"] == "resume"] == [27654]
            stable = snapshot()
            phase[0] = "repeat"
            repeated = flow.run_pipeline(config)
            assert repeated["status"] == "completed"
            assert not [a for a in attempts if a["phase"] == "repeat"]
            assert snapshot() == stable
        with sqlite3.connect(out / "projects.db") as connection:
            gold = connection.execute("SELECT capacity_mw,storage_power_mw,storage_energy_mwh,entity_evidence_json "
                                      "FROM extracted_projects WHERE source_link=?", (by_title[next(t for t,r in by_title.items() if r['id']==27311)]["url"],)).fetchone()
            assert gold[:3] == (9.0, None, None)
            assert json.loads(gold[3])["summary_warning"]
        with sqlite3.connect(out / "main.db") as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchall()
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            assert integrity == [("ok",)] and not foreign_keys
        write_json(out / "verification.json", {"status": "passed", "external_calls": 0,
                   "first": first, "resume": resumed, "repeat": repeated,
                   "article_attempts": attempts, "counts": {t:len(rows) for t,rows in stable.items()},
                   "core_tables_unchanged_on_repeat": True, "integrity": integrity,
                   "foreign_keys": foreign_keys, "noncritical_summary_failure_preserved_facts": True,
                   "optional_location_responses": optional_requests, "used_responses": used,
                   "not_exercised": ["live_crawler", "location_normalizer", "developer_normalizer", "residual_translation", "new_model_generation"]})
    finally:
        after = fingerprints()
        write_json(out / "source-verification.json", {"before": before, "after": after, "unchanged": before == after})
        assert before == after


if __name__ == "__main__":
    main()
