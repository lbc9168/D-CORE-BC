"""Replay immutable live response text through current guards, without paid calls.

Pins the latest extraction round per article and consumes responses in call order. This is
postprocessing verification, not a fresh live generation or a substitute for review.
Earlier responses remain in the original audit directories, never overwritten.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import unicodedata

from entity_recovery_validation import ROOT, fingerprints, persist_isolated, write_json


def normalize_key(value):
    """Only harmless text formatting, never fuzzy semantic project matching."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))


def project_key(prompt, stage):
    if stage in {"s3", "s34", "s4"}:
        match = re.search(r"^目标项目[：:](.+)$", prompt, re.M)
        if not match:
            raise ValueError("No project name in recorded per-project stage prompt")
        name = re.split(r"[（(]类型[：:]", match.group(1), maxsplit=1)[0]
        return normalize_key(name)
    if stage == "s5":
        payload = prompt.split("结构化项目：", 1)[1].lstrip()
        return normalize_key(json.JSONDecoder().raw_decode(payload)[0]["common_info"]["Project Name"])
    return ""


def project_context(prompt):
    match = re.search(r"^原文项目上下文[：:]([^{]*\{.*\})\s*$", prompt, re.M)
    if not match:
        return ""
    value = json.loads(match.group(1))
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if value else ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recordings", type=Path, nargs="+", required=True)
    parser.add_argument("--sample-audit", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists() or not out.is_relative_to(ROOT / "output"):
        parser.error("Use a new isolated output directory")
    out.mkdir(parents=True)
    logging.basicConfig(filename=out / "run.log", encoding="utf-8", level=logging.INFO)
    os.environ["S01B_MERGE_S34"] = "1"
    import sys
    sys.path.insert(0, str(ROOT / "s03_data_exporter/src"))
    from s01b_project_extractor.stages.orchestrator import extract_staged
    from s01b_project_extractor.llm.client import LLMResponseParseError
    before = fingerprints()
    candidates = []
    all_calls = []
    for folder in args.recordings:
        for call in json.loads((folder / "calls.json").read_text(encoding="utf-8")):
            all_calls.append((folder, call))
    latest_round = {}
    for folder, call in all_calls:
        if call.get("raw_id") and call.get("stage") == "s1":
            latest_round[call["raw_id"]] = (str(folder), call.get("phase", "legacy"))
    for folder, call in all_calls:
        if latest_round.get(call.get("raw_id")) != (str(folder), call.get("phase", "legacy")):
            continue
        if not call.get("response_file"):
            continue
        if not call.get("raw_id") or call["stage"] not in {"s1", "s2", "s3", "s4", "s34", "s5"}:
            continue
        request = json.loads((folder / "responses" / f"request-{call['number']:03}.json").read_text(encoding="utf-8"))
        prompt = request.get("contents") or request["messages"][0]["content"]
        key = (call["raw_id"], call["stage"], project_key(prompt, call["stage"]))
        path = (folder / "responses" / call["response_file"]).resolve()
        source = {"file": str(path), "text": path.read_text(encoding="utf-8"),
                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "context": project_context(prompt),
                  "phase": call.get("phase", "legacy"), "request_number": call["number"]}
        candidates.append((key, source))
    audit = json.loads(args.sample_audit.read_text(encoding="utf-8"))
    used, records, consumed = [], [], set()
    class Replay:
        def __init__(self, raw_id):
            self.raw_id = raw_id
        def complete(self, prompt, *, stage, json_mode=True):
            key = (self.raw_id, stage, project_key(prompt, stage))
            remaining = [(k, v) for k, v in candidates if v["file"] not in consumed]
            binding = "stage_sequence" if stage in {"s1", "s2"} else "normalized_name_sequence"
            matches = [(k, v) for k, v in remaining if k == key]
            if stage in {"s3", "s4", "s34"}:
                context = project_context(prompt)
                if len({v["context"] for _, v in matches}) > 1:
                    matches = [(k, v) for k, v in matches if v["context"] == context]
                    binding = "name_and_exact_context_sequence"
                if not matches and context:
                    context_matches = [(k, v) for k, v in remaining if k[:2] == key[:2] and v["context"] == context]
                    if len({k for k, _ in context_matches}) == 1:
                        matches = context_matches
                        binding = "unique_exact_source_context_sequence"
            if not matches:
                raise RuntimeError(f"Needs re-extraction: no unconsumed response with unambiguous project binding: {key}")
            # Initial S2 and boundary-correction S2 are separate calls, not revisions
            # of one snapshot. Consuming the last S2 first would drop sibling projects.
            _, source = matches[0]
            consumed.add(source["file"])
            used.append({"key": key, "file": source["file"], "sha256": source["sha256"],
                         "binding": binding, "phase": source["phase"], "request_number": source["request_number"]})
            try:
                return deepcopy(json.loads(source["text"]))
            except json.JSONDecodeError as exc:
                raise LLMResponseParseError(str(exc), source["text"]) from exc
    try:
        for sample in audit["samples"]:
            if sample.get("group") == "holdout":
                continue
            row = sample["source"]
            try:
                result = extract_staged(row["title"], row["content"], row["publish_date"], llm=Replay(row["id"]))
                records.append({"source": row, "result": result})
            except (RuntimeError, ValueError, TypeError, KeyError) as exc:
                records.append({"source": row, "error": str(exc), "payload": getattr(exc, "payload", None)})
            write_json(out / "results.json", records)
        write_json(out / "manifest.json", {"mode": "recorded_response_postprocessing", "paid_calls": 0,
                   "selection": "latest article extraction round; chronological response consumption within stage/project, unique exact source context fallback only", "used": used, "latest_round": latest_round,
                   "unused_responses": [{"key": key, "file": value["file"], "request_number": value["request_number"]} for key, value in candidates if value["file"] not in consumed],
                   "code_hashes": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in (ROOT / "s01b_project_extractor/src/s01b_project_extractor").rglob("*.py")}})
        persisted = persist_isolated(records, out)
        if (out / "main.db").exists():
            from s03_data_exporter.main import run
            export = run(src_db=str(out / "main.db"), dst_dir=str(out / "export"), web_data_dir=str(out / "web/data"), province_cache=str(out / "province-cache.json"), allow_model_calls=False)
            write_json(out / "export-result.json", export)
        write_json(out / "replay-limitations.json", {"external_calls": 0, "merge": "S01B writer and S02A deterministic write functions only; no new LLM duplicate decisions. Live phase must verify real duplicate pair.", "old_contract": "Old responses retained verbatim; absent semantic fields are not filled with affirmative defaults.", "persistence": persisted})
    finally:
        after = fingerprints()
        write_json(out / "source-verification.json", {"before": before, "after": after, "unchanged": before == after, "actual_requests": 0})
        assert before == after


if __name__ == "__main__":
    main()
