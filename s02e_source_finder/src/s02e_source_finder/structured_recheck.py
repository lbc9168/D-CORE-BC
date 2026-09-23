"""Replay saved observations through revised gates without requests or ledger mutations."""

import argparse
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .pilot import LabelSet, PilotCorpus, assert_sources, digest, private_path
from .pilot_report import score
from .sample import file_hash, write_json
from .structured import Extraction, SYSTEM, TargetCards, decide, model_payload
from .structured_run import rule_snapshot, validate_cards

logger = logging.getLogger(__name__)


def recheck(corpus_path: Path, cards_path: Path, parent_path: Path) -> dict:
    """Verify input and prompt provenance before recomputing deterministic decisions."""
    corpus_path, cards_path, parent_path = [private_path(path, existing=True) for path in (corpus_path, cards_path, parent_path)]
    corpus = PilotCorpus.model_validate_json(corpus_path.read_text(encoding="utf-8"))
    cards = TargetCards.model_validate_json(cards_path.read_text(encoding="utf-8"))
    parent = json.loads(parent_path.read_text(encoding="utf-8"))
    labels_path = corpus_path.with_suffix(".labels.json")
    labels = LabelSet.model_validate_json(labels_path.read_text(encoding="utf-8"))
    if parent["corpus_sha"] != file_hash(corpus_path) or labels.corpus_sha256 != parent["corpus_sha"] or parent["labels_sha256"] != file_hash(labels_path) or cards.selected_sha256 != corpus.selected_sha256:
        raise ValueError("recheck_inputs_changed")
    old_rules = json.loads((parent_path.parent / f"structured-rules-{parent['run_key']}.json").read_text(encoding="utf-8"))
    if digest(old_rules) != parent["rules_sha"] or old_rules["system"] != SYSTEM or old_rules["schema"] != Extraction.model_json_schema():
        raise ValueError("extraction_contract_changed")
    validate_cards(cards, corpus)
    assert_sources(corpus.source_hashes)
    cases = {case.case_id: case for case in corpus.cases if case.split == "development"}
    if set(row["case_id"] for row in parent["results"]) != set(cases):
        raise ValueError("recheck_parent_cases_incomplete")
    results = []
    for previous in parent["results"]:
        row = dict(previous)
        case = cases[row["case_id"]]
        if row.get("call"):
            prompt = SYSTEM + "\nJSON schema:\n" + json.dumps(Extraction.model_json_schema(), ensure_ascii=False) + json.dumps(model_payload(case.context, case.page), ensure_ascii=False)
            if hashlib.sha256(prompt.encode()).hexdigest() != row["call"]["prompt_sha256"]:
                raise ValueError("saved_prompt_mismatch")
        if row.get("extraction"):
            try:
                row.update(decide(case.context, cards.cards[case.context.event_id], case.page, Extraction.model_validate(row["extraction"])))
                row.pop("extraction_failed", None)
            except ValueError as exc:
                logger.error("recheck_evidence_invalid case=%s reason=%s", row["case_id"], str(exc))
                row.update(decision="pending", reason_codes=[str(exc)], extraction_failed=True)
            row["reason"] = ";".join(row["reason_codes"])
        results.append(row)
    snapshot = rule_snapshot()
    key = digest([file_hash(parent_path), file_hash(cards_path), snapshot])[:24]
    result = {key: value for key, value in parent.items() if key not in {"results", "score", "complete", "created_at", "usage"}}
    result.update(recheck_key=key, parent_file=parent_path.name, parent_sha256=file_hash(parent_path), original_cards_sha=parent["cards_sha"], cards_sha=file_hash(cards_path), current_rules=snapshot,
                  created_at=datetime.now(timezone.utc).isoformat(), new_requests=0, results=results, score=score(results, labels),
                  complete=not any(row.get("extraction_failed") for row in results), original_usage=parent["usage"])
    assert_sources(corpus.source_hashes)
    path = corpus_path.parent / f"structured-recheck-{key}.json"
    if path.exists():
        raise ValueError("recheck_already_exists")
    write_json(path, result)
    return {"report": str(path), "score": result["score"], "complete": result["complete"], "new_requests": 0}


def main() -> None:
    """Write one separately identified offline evaluation from a paid parent run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("cards", type=Path)
    parser.add_argument("parent", type=Path)
    args = parser.parse_args()
    logging.basicConfig(filename=private_path(args.corpus, existing=True).parent / "structured.log", level=logging.INFO, encoding="utf-8")
    print(json.dumps(recheck(args.corpus, args.cards, args.parent), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
