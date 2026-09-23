"""Replay immutable live response text through current guards, without paid calls.

Pins the last recorded attempt for each article/stage/exact project name. This is
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

from entity_recovery_validation import ROOT, fingerprints, persist_isolated, write_json


def project_key(prompt, stage):
    if stage in {"s34", "s4"}:
        match = re.search(r"^目标项目：(.+)（类型：", prompt, re.M)
        if not match:
            raise ValueError("No exact project name in recorded entity-stage prompt")
        return match.group(1)
    if stage == "s5":
        # Both older correction prompts and summary-only prompts contain one JSON
        # payload followed by output instructions. Decode that one explicit object.
        payload = prompt.split("结构化项目：\n", 1)[1].lstrip()
        return json.JSONDecoder().raw_decode(payload)[0]["common_info"]["Project Name"]
    return ""


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
    from s01b_project_extractor.stages.orchestrator import extract_staged
    from s01b_project_extractor.llm.client import LLMResponseParseError
    before = fingerprints()
    responses = {}
    for folder in args.recordings:
        for call in json.loads((folder / "calls.json").read_text(encoding="utf-8")):
            if not call.get("response_file"):
                continue
            prompt = (folder / f"request-{call['number']:03}.txt").read_text(encoding="utf-8")
            key = (call["raw_id"], call["stage"], project_key(prompt, call["stage"]))
            path = (folder / call["response_file"]).resolve()
            responses[key] = {"file": str(path), "text": path.read_text(encoding="utf-8"),
                              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    audit = json.loads(args.sample_audit.read_text(encoding="utf-8"))
    used, records = [], []
    class Replay:
        def __init__(self, raw_id):
            self.raw_id = raw_id
        def complete(self, prompt, *, stage, json_mode=True):
            key = (self.raw_id, stage, project_key(prompt, stage))
            if key not in responses:
                raise RuntimeError(f"No recorded response for exact stage/project key: {key}")
            source = responses[key]
            used.append({"key": key, "file": source["file"], "sha256": source["sha256"]})
            try:
                return deepcopy(json.loads(source["text"]))
            except json.JSONDecodeError as exc:
                raise LLMResponseParseError(str(exc), source["text"]) from exc
    try:
        for sample in audit["samples"]:
            row = sample["source"]
            try:
                result = extract_staged(row["title"], row["content"], row["publish_date"], llm=Replay(row["id"]))
                records.append({"source": row, "result": result})
            except (RuntimeError, ValueError, TypeError, KeyError) as exc:
                records.append({"source": row, "error": str(exc), "payload": getattr(exc, "payload", None)})
            write_json(out / "results.json", records)
        write_json(out / "manifest.json", {"mode": "recorded_response_postprocessing", "paid_calls": 0,
                   "selection": "last recorded attempt per article/stage/exact project name", "used": used,
                   "code_hashes": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in (ROOT / "s01b_project_extractor/src/s01b_project_extractor").rglob("*.py")}})
        persist_isolated(records, out)
    finally:
        after = fingerprints()
        write_json(out / "source-verification.json", {"before": before, "after": after, "unchanged": before == after, "actual_requests": 0})
        assert before == after


if __name__ == "__main__":
    main()
