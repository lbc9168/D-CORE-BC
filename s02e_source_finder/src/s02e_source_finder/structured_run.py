"""Bounded development-only structured calibration over frozen pages and target cards."""

import argparse
import json
import logging
import sys
from contextlib import closing
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import requests
from dotenv import load_dotenv
from pydantic import ValidationError

from . import structured, structured_client
from .pilot import LabelSet, PilotCorpus, PilotStore, assert_sources, digest, private_path
from .pilot_report import score, usage_summary
from .sample import ROOT, file_hash, write_json
from .structured import Extraction, TargetCards, blocks, compact, decide, model_payload
from .structured_client import ExtractionError, extract

logger = logging.getLogger(__name__)


def validate_cards(cards: TargetCards, corpus: PilotCorpus, split: str = "development") -> None:
    """Check fingerprints and every original-source quotation before any model request."""
    for case in corpus.cases:
        if case.split != split:
            continue
        card = cards.cards.get(case.context.event_id)
        if not card or card.input_fingerprint != case.context.fingerprint() or card.stage != case.context.event.get("project_stage"):
            raise ValueError("target_card_context_mismatch")
        articles = {row["url"]: row for row in case.context.source_articles}
        for evidence in card.evidence:
            article = articles.get(evidence.get("url"))
            if not article or not evidence.get("quote") or compact(evidence["quote"]) not in compact(article.get("title", "") + "\n" + article.get("content", "")):
                raise ValueError("target_card_evidence_not_located")


def rule_snapshot() -> dict:
    """Freeze both deterministic policy and the exact extraction request contract."""
    return {"version": structured.VERSION, "system": structured.SYSTEM, "schema": Extraction.model_json_schema(), "source_profiles": structured.PROFILES, "source_origins": structured.ORIGINS,
            "code_hashes": {name: file_hash(Path(module.__file__)) for name, module in {"structured": structured, "client": structured_client}.items()}, "runner_sha256": file_hash(Path(__file__)),
            "temperature": 0, "max_output_tokens": 4096, "deepseek_thinking": "disabled", "gemini_response_format": "responseJsonSchema", "gemini_native_schema": structured_client.native_schema(), "python": sys.version,
            "dependencies": {name: version(name) for name in ("pydantic", "requests")}}


def run(corpus_path: Path, cards_path: Path, *, provider: str, max_calls: int = 18, execute: bool = False, retry_of: str | None = None, split: str = "development", development_evaluation: Path | None = None) -> dict:
    """Extract within a fixed split; holdout requires a complete frozen development pass."""
    if provider not in {"gemini", "deepseek"} or split not in {"development", "holdout"} or not 0 <= max_calls <= (18 if split == "development" else 8):
        raise ValueError("invalid_structured_development_budget")
    corpus_path, cards_path = private_path(corpus_path, existing=True), private_path(cards_path, existing=True)
    corpus = PilotCorpus.model_validate_json(corpus_path.read_text(encoding="utf-8"))
    cards = TargetCards.model_validate_json(cards_path.read_text(encoding="utf-8"))
    if cards.selected_sha256 != corpus.selected_sha256:
        raise ValueError("cards_selection_mismatch")
    validate_cards(cards, corpus, split)
    assert_sources(corpus.source_hashes)
    cases = [case for case in corpus.cases if case.split == split]
    snapshot = json.loads(json.dumps(rule_snapshot(), ensure_ascii=False))
    rules_sha = digest(snapshot)
    corpus_sha, cards_sha = file_hash(corpus_path), file_hash(cards_path)
    development_sha = None
    if split == "holdout":
        if not development_evaluation:
            raise ValueError("holdout_requires_development_pass")
        development_path = private_path(development_evaluation, existing=True)
        development = json.loads(development_path.read_text(encoding="utf-8"))
        dev_rules = development.get("current_rules") or json.loads((development_path.parent / f"structured-rules-{development['run_key']}.json").read_text(encoding="utf-8"))
        checks = development["score"]
        if development["provider"] != provider or development["split"] != "development" or development["corpus_sha"] != corpus_sha or development["labels_sha256"] != file_hash(corpus_path.with_suffix(".labels.json")) or not development["complete"] or not checks["development_gate"] or not checks["safe_event_references"] or dev_rules["code_hashes"]["structured"] != snapshot["code_hashes"]["structured"] or dev_rules["system"] != snapshot["system"] or dev_rules["schema"] != snapshot["schema"]:
            raise ValueError("development_not_qualified_or_rules_changed")
        development_sha = file_hash(development_path)
    if retry_of:
        if len(retry_of) != 24 or any(char not in "0123456789abcdef" for char in retry_of):
            raise ValueError("invalid_retry_key")
        parent = json.loads((corpus_path.parent / f"structured-evaluation-{retry_of}.json").read_text(encoding="utf-8"))
        # A connection failure before the first usable response may be retried in a new audit run.
        # Never replay successful calls, reset reservations, or erase unknown usage from the parent.
        if parent["corpus_sha"] != corpus_sha or parent["cards_sha"] != cards_sha or parent["provider"] != provider or parent["stopped"] != "ConnectionError" or parent["attempted_calls"] != 1 or any(row.get("call") for row in parent["results"]):
            raise ValueError("retry_requires_first_request_connection_failure")
    key_parts = [corpus_sha, cards_sha, rules_sha, provider, split]
    key = digest(key_parts + (["retry_of", retry_of] if retry_of else []))[:24]
    summary = {"version": structured.VERSION, "run_key": key, "provider": provider, "split": split, "corpus_sha": corpus_sha, "cards_sha": cards_sha, "rules_sha": rules_sha, "max_calls": max_calls, "cases": len(cases), "readable": sum(not case.page.problem for case in cases), "execute": execute}
    if development_sha:
        summary["development_evaluation_sha"] = development_sha
    if retry_of:
        summary["retry_of"] = retry_of
    if not execute:
        return summary
    labels_path = corpus_path.with_suffix(".labels.json")
    labels = LabelSet.model_validate_json(labels_path.read_text(encoding="utf-8"))
    if labels.corpus_sha256 != corpus_sha or set(labels.labels) != {case.case_id for case in corpus.cases}:
        raise ValueError("frozen_labels_mismatch")
    load_dotenv(ROOT / ".env", override=False)
    output_dir = corpus_path.parent
    manifest_path = output_dir / f"structured-rules-{key}.json"
    if not manifest_path.exists():
        write_json(manifest_path, snapshot)
    elif json.loads(manifest_path.read_text(encoding="utf-8")) != snapshot:
        raise ValueError("rules_snapshot_changed")
    stopped = None
    with closing(PilotStore(output_dir / "pilot.sqlite")) as store:
        store.begin(key, corpus_sha, digest([rules_sha, cards_sha, file_hash(labels_path)]), provider, max_calls)
        previous = store.conn.execute("SELECT result_json FROM attempts WHERE run_key=? AND result_json IS NOT NULL", (key,)).fetchall()
        if any(json.loads(row[0]).get("provider_failed") for row in previous):
            stopped = "provider_previously_failed"
        try:
            for case in ([] if stopped else cases):
                try:
                    fresh = store.reserve(key, case.case_id, requested=not bool(case.page.problem))
                except ValueError as exc:
                    if str(exc) != "pilot_call_budget_exhausted":
                        raise
                    stopped = str(exc)
                    break
                if not fresh:
                    continue
                result = {"case_id": case.case_id, "event_id": case.context.event_id, "project_id": case.context.project_id, "domain": case.domain, "split": case.split, "url": case.page.final_url, "model_called": False}
                if case.page.problem:
                    permanent = case.page.problem in {"http_404", "http_410", "not_chinese", "goalfore_not_external", "unsafe_url", "non_public_host", "non_public_address", "redirect_to_homepage"}
                    result.update(decision="rejected" if permanent else "pending", reason_codes=[case.page.problem])
                else:
                    result["model_called"] = True
                    try:
                        extraction, call = extract(model_payload(case.context, case.page), provider)
                        result.update(call=call, extraction=extraction.model_dump())
                        result.update(decide(case.context, cards.cards[case.context.event_id], case.page, extraction))
                    except (requests.RequestException, ValueError, RuntimeError, ValidationError) as exc:
                        logger.error("structured_case_failed case=%s type=%s", case.case_id, type(exc).__name__)
                        result.update(decision="pending", reason_codes=[str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__])
                        result["extraction_failed"] = True
                        if isinstance(exc, ExtractionError):
                            result["call"] = exc.call
                        if isinstance(exc, (requests.RequestException, RuntimeError)):
                            result["reason_codes"] = [type(exc).__name__ if isinstance(exc, requests.RequestException) else str(exc)]
                            result["provider_failed"] = True
                            stopped = result["reason_codes"][0]
                result["reason"] = ";".join(result["reason_codes"])
                store.finish(key, case.case_id, result)
                if stopped:
                    break
        finally:
            assert_sources(corpus.source_hashes)
        rows = store.conn.execute("SELECT * FROM attempts WHERE run_key=?", (key,)).fetchall()
        results = [json.loads(row["result_json"]) for row in rows if row["result_json"]]
        unresolved = [row["case_id"] for row in rows if row["status"] == "reserved"]
        complete = len(results) == len(cases) and not unresolved and not stopped and not any(row.get("extraction_failed") for row in results)
        with store.conn:
            store.conn.execute("UPDATE runs SET status=? WHERE run_key=?", ("complete" if complete else "incomplete", key))
    summary.update(created_at=datetime.now(timezone.utc).isoformat(), results=results, attempted_calls=sum(row["requested"] for row in rows), unresolved_attempts=unresolved, stopped=stopped, complete=complete, source_unchanged=True,
                   human_labels_confirmed=labels.human_confirmed, human_cards_confirmed=cards.human_confirmed, labels_sha256=file_hash(labels_path))
    summary["score"] = score(results, labels)
    summary["usage"] = usage_summary(summary)
    path = output_dir / f"structured-evaluation-{key}.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {name: value for name, value in summary.items() if name != "results"} | {"report": str(path)}


def main() -> None:
    """Default to a zero-call preview; holdout requires explicit qualified development input."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("cards", type=Path)
    parser.add_argument("--provider", choices=["gemini", "deepseek"], required=True)
    parser.add_argument("--max-calls", type=int, default=18)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--split", choices=["development", "holdout"], default="development")
    parser.add_argument("--development-evaluation", type=Path)
    parser.add_argument("--retry-of", help="Explicitly retain and retry a run stopped at its first connection failure")
    args = parser.parse_args()
    if args.execute:
        logging.basicConfig(filename=private_path(args.corpus, existing=True).parent / "structured.log", level=logging.INFO, encoding="utf-8")
    print(json.dumps(run(args.corpus, args.cards, provider=args.provider, max_calls=args.max_calls, execute=args.execute, retry_of=args.retry_of, split=args.split, development_evaluation=args.development_evaluation), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
