"""Bounded discovery and paired extraction on immutable pages; never writes project databases."""

import argparse
import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from . import grounding, structured, structured_client
from .contracts import EventContext, Page
from .fetch import PublicFetcher
from .pilot import assert_sources, digest, private_path
from .publishers import PublisherRegistry
from .sample import MODULE, ROOT, file_hash
from .source_loop import normalized_url, project_reference, resolve_citation, service_blocked, write_frozen
from .structured import Extraction, TargetCard, decide, model_payload
from .structured_client import ExtractionError
from .trial_budget import TrialBudget

LOG = logging.getLogger(__name__)
DEFAULT_DIR = MODULE / "data/luna-capacity-pilot-20261005"


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_frozen(path, value)


def assert_corpus_sources(corpus: dict) -> None:
    """Historical contexts bind to frozen parents, current selections to source databases."""
    if corpus.get("split") == "old" and not corpus.get("source_hashes"):
        assert_sources(corpus.get("parent_files", {}))
    else:
        assert_sources(corpus.get("source_hashes", {}))


def prepare_old(output: Path) -> dict:
    """Freeze the existing 13 cases as development material, not a new holdout."""
    output = private_path(output)
    source = MODULE / "data/acceptance-v1/34763d715803fa57341ef6f3/recovered/report.json"
    inputs_path = MODULE / "data/acceptance-v1/inputs-v1.json"
    inputs, results = read(inputs_path), read(source)["results"]
    cases = []
    for row in results:
        event_id = row["event_id"]
        context = EventContext.model_validate(inputs["contexts"][event_id])
        card = TargetCard.model_validate(inputs["cards"][event_id])
        page = Page.model_validate(row["page"])
        cases.append({"case_id": row["case_id"], "event_id": event_id, "project_id": context.project_id,
                      "pool": "old_development", "context": context.model_dump(), "card": card.model_dump(mode="json"),
                      "page": page.model_dump(), "historical_parent_sha256": file_hash(source)})
    corpus = {"version": "enrichment-corpus-v1", "split": "old", "independent_holdout": False,
              "parent_files": {str(source): file_hash(source), str(inputs_path): file_hash(inputs_path)}, "cases": cases,
              "source_hashes": {}, "source_note": "Historical frozen contexts; no claim they equal current production facts"}
    save(output / "old-corpus.json", corpus)
    return {"cases": len(cases), "readable": sum(not c["page"]["problem"] for c in cases), "external_calls": 0}


def discovery_request(event: dict, round_number: int) -> dict:
    context = EventContext.model_validate(event["context"])
    card = TargetCard.model_validate(event["card"])
    request = grounding.search_request(context, card, round_number)
    language = "中文" if round_number == 1 else "英文（优先当地政府、项目业主、实际参与企业的英文官方网站；可补充中文）"
    target = {"project_name": context.project.get("project_name"), "project_name_en": context.project.get("project_name_en"),
              "country": context.project.get("country"), "location": context.project.get("location"),
              "participants": [row.get("raw_entity_name") or row.get("implementing_entity") or row.get("entity_name") for row in context.entities],
              "stage_to_check": card.stage}
    goal = "确认具体电站真实存在、参与企业及明确报道的发电装机容量（保留MWac/MWp/MWdc和期次）。" if event["pool"] == "capacity_missing" else "寻找能确认该项目身份和具体进展的原始报道。"
    prompt = f"请实际使用Google搜索，为以下具体项目寻找最多3篇{language}完整报道。{goal}排除goalfore.cn及其子域。不能用设备订单、园区总容量或另一期项目代替本项目。无结果就明确无结果。每项简述标题、实际发布者和日期，并附来源引用；不要把搜索摘要当成已经核验的事实。最多600中文字。目标：" + json.dumps(target, ensure_ascii=False)
    request["contents"][0]["parts"][0]["text"] = prompt
    return request


def discover(output: Path, *, execute: bool, dns_mode: str) -> dict:
    output = private_path(output, existing=True)
    selected_path = output / "selected-targets.json"
    selected = read(selected_path)
    events = selected["events"]
    reviewed_cards_path = output / "reviewed-target-cards.json"
    reviewed_cards = read(reviewed_cards_path)
    if reviewed_cards.get("selection_sha256") != file_hash(selected_path) or set(reviewed_cards.get("cards", {})) != {e["event_id"] for e in events}:
        raise ValueError("reviewed_target_cards_not_bound_to_selection")
    events = [event | {"card": reviewed_cards["cards"][event["event_id"]]} for event in events]
    if len(events) != 10 or len({r["project_id"] for r in events}) != 10:
        raise ValueError("trial_requires_frozen_ten_projects")
    assert_sources(selected["source_hashes"])
    summary = {"projects": len(events), "search_cap": 20, "pages_per_project": 4, "execute": execute}
    if not execute:
        return summary
    corpus_path = output / "new-corpus.json"
    if corpus_path.exists():
        existing = read(corpus_path)
        if existing["selection_sha256"] != file_hash(selected_path) or existing.get("reviewed_cards_sha256") != file_hash(reviewed_cards_path):
            raise ValueError("trial_selection_changed")
        return summary | {"reused": True, "cases": len(existing["cases"]), "new_requests": 0}
    ledger = TrialBudget(output / "calls.sqlite")
    cases, failures, searches = [], [], []
    fetcher = PublicFetcher(dns_mode=dns_mode, allow_english=True)
    blocked = False
    try:
        for event in events:
            candidates = []
            # Freeze all search results before model comparison. Neither model chooses its own corpus.
            for round_number in (1, 2):
                request = discovery_request(event, round_number)
                spec = {"provider": "gemini", "model": grounding.MODEL, "endpoint": "generateContent", "request": request}
                result = ledger.cached(spec)
                if result is None and blocked:
                    result = {"error": "search_provider_stopped"}
                elif result is None:
                    try:
                        if ledger.reserve(spec, "search", "search"):
                            try:
                                result = grounding.search(request)
                            except (requests.RequestException, ValueError, RuntimeError) as exc:
                                result = {"error": str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__}
                            ledger.finish(spec, result, failed=bool(result.get("error")))
                        else:
                            result = ledger.cached(spec)
                    except ValueError as exc:
                        if str(exc) != "trial_budget_exhausted":
                            raise
                        result = {"error": "trial_budget_exhausted"}
                result = result or {"error": "reserved_request_unresolved"}
                if service_blocked(result.get("error")) or result.get("error") == "trial_budget_exhausted":
                    blocked = True
                searches.append({"event_id": event["event_id"], "round": round_number, "request_hash": digest(spec), "error": result.get("error")})
                for chunk in result.get("grounding", {}).get("groundingChunks", []):
                    web = chunk.get("web")
                    if not web or not web.get("uri"):
                        continue
                    try:
                        url = resolve_citation(web["uri"], fetcher)
                    except (requests.RequestException, ValueError, OSError) as exc:
                        failures.append({"event_id": event["event_id"], "kind": "citation", "reason": type(exc).__name__, "round": round_number})
                        continue
                    if normalized_url(url) not in {normalized_url(c["url"]) for c in candidates}:
                        candidates.append({"url": url, "origin": f"google_round_{round_number}"})
            # Keep Chinese priority, but reserve one slot for an English supplementary search result.
            first = [c for c in candidates if c["origin"] == "google_round_1"][:3]
            second = [c for c in candidates if c not in first]
            chosen = (first + second)[:4]
            for candidate in chosen:
                page_path = output / "pages" / (digest(candidate["url"]) + ".json")
                if page_path.exists():
                    page = Page.model_validate(read(page_path))
                else:
                    page = fetcher.fetch(candidate["url"])
                    save(page_path, page.model_dump())
                context = EventContext.model_validate(event["context"])
                case_id = digest([event["event_id"], page.final_url, page.text_hash])[:20]
                cases.append({"case_id": case_id, "event_id": event["event_id"], "project_id": event["project_id"], "pool": event["pool"],
                              "context": context.model_dump(), "card": event["card"], "page": page.model_dump(),
                              "participation_evidence": event.get("participation_evidence", []), "origin": candidate["origin"]})
            print(json.dumps({"discovery_project": event["project_id"], "candidate_pages": len(chosen)}, ensure_ascii=False), flush=True)
    finally:
        fetcher.close()
        assert_sources(selected["source_hashes"])
    corpus = {"version": "enrichment-corpus-v1", "split": "new", "independent_holdout": True, "selection_sha256": file_hash(selected_path),
              "reviewed_cards_sha256": file_hash(reviewed_cards_path),
              "source_hashes": selected["source_hashes"], "cases": cases, "searches": searches, "discovery_failures": failures,
              "events_without_candidates": [e["event_id"] for e in events if not any(c["event_id"] == e["event_id"] for c in cases)]}
    save(corpus_path, corpus)
    return summary | {"cases": len(cases), "budget": ledger.summary()}


def judge_case(case: dict, provider: str, registry: PublisherRegistry, ledger: TrialBudget, bucket: str, *, execute: bool, blocked: bool) -> dict:
    context, card, page = EventContext.model_validate(case["context"]), TargetCard.model_validate(case["card"]), Page.model_validate(case["page"])
    base = {"case_id": case["case_id"], "project_id": context.project_id, "event_id": context.event_id, "pool": case["pool"], "provider": provider,
            "decision": "pending", "reason": "", "external_reference": None, "capacity_observations": [], "evidence_language": None}
    if page.problem:
        return base | {"reason": page.problem, "processing_status": "failed"}
    spec = structured_client.request_spec(model_payload(context, page), provider)
    cached = ledger.cached(spec)
    if cached is None:
        if blocked or not execute:
            return base | {"reason": "provider_stopped" if blocked else "not_executed"}
        try:
            if ledger.reserve(spec, bucket, provider):
                try:
                    extraction, call = structured_client.extract(model_payload(context, page), provider)
                    cached = {"extraction": extraction.model_dump(), "call": call}
                except (requests.RequestException, ValueError, RuntimeError) as exc:
                    cached = {"error": str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__}
                    if isinstance(exc, ExtractionError):
                        cached["call"] = exc.call
                ledger.finish(spec, cached, failed=bool(cached.get("error")))
            else:
                cached = ledger.cached(spec)
        except ValueError as exc:
            if str(exc) != "trial_budget_exhausted":
                raise
            return base | {"reason": "trial_budget_exhausted"}
    cached = cached or {"error": "reserved_request_unresolved"}
    if cached.get("error"):
        return base | {"reason": cached["error"], "call": cached.get("call", {}), "processing_status": "failed"}
    extraction = Extraction.model_validate(cached["extraction"])
    row = base | {"extraction": extraction.model_dump(), "call": cached.get("call", {}), "evidence_language": extraction.language}
    try:
        public_result = decide(context, card, page, extraction, registry)
        evidence_result = decide(context, card, page, extraction, registry, allow_english=True)
        row.update(public_result)
        row["reason"] = ";".join(public_result["reason_codes"])
        row["evidence_result"] = evidence_result
        row["capacity_observations"] = evidence_result.get("measurements", [])
        row["project_check"] = {"same_project": extraction.relation, "publisher_verified": not evidence_result.get("source", {}).get("problem", "unverified"),
                                "participants": [p.model_dump() for p in extraction.participants], "not_found_is_not_nonexistent": True}
        if extraction.language == "zh" and row["decision"] in {"event_reference", "project_reference"}:
            row["external_reference"] = project_reference(context, row | {"page": page.model_dump()}, registry)
    except (ValueError, KeyError, TypeError) as exc:
        # A failed projection or evidence span is one candidate's failure, never a batch abort.
        row.update(decision="pending", reason="evidence_or_projection_invalid:" + type(exc).__name__, external_reference=None)
    row["processing_status"] = "success" if row["external_reference"] else "excluded" if row["decision"] == "rejected" else "pending"
    return row


def evaluate(output: Path, split: str, registry_path: Path, labels_path: Path, *, execute: bool) -> dict:
    output, registry_path, labels_path = private_path(output, existing=True), private_path(registry_path, existing=True), private_path(labels_path, existing=True)
    corpus_path = output / f"{split}-corpus.json"
    corpus, labels = read(corpus_path), read(labels_path)
    registry = PublisherRegistry.model_validate(read(registry_path))
    if labels.get("corpus_sha256") != file_hash(corpus_path) or set(labels.get("cases", {})) != {c["case_id"] for c in corpus["cases"]}:
        raise ValueError("independent_labels_not_frozen_to_corpus")
    if labels.get("human_confirmed") is not False:
        raise ValueError("pilot_labels_must_identify_agent_review")
    assert_corpus_sources(corpus)
    snapshot = {"corpus_sha256": file_hash(corpus_path), "labels_sha256": file_hash(labels_path), "registry_sha256": file_hash(registry_path),
                "schema": Extraction.model_json_schema(), "prompt": structured.SYSTEM, "rule_version": structured.VERSION,
                "code": {str(p.relative_to(ROOT)): file_hash(p) for p in [Path(__file__), Path(structured.__file__), Path(structured_client.__file__)]}}
    if not execute:
        return {"split": split, "cases": len(corpus["cases"]), "max_extract_calls": len(corpus["cases"]) * 2, "execute": False, "snapshot": snapshot}
    save(output / f"{split}-evaluation-freeze.json", snapshot)
    ledger = TrialBudget(output / "calls.sqlite")
    bucket = "old_extract" if split == "old" else "new_extract"
    providers = ("deepseek", "openai")
    blocked = {provider: False for provider in providers}
    results = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for case in corpus["cases"]:
            jobs = {p: pool.submit(judge_case, case, p, registry, ledger, bucket, execute=True, blocked=blocked[p]) for p in providers}
            for provider, future in jobs.items():
                row = future.result()
                if service_blocked(row["reason"]) or row["reason"] == "trial_budget_exhausted":
                    blocked[provider] = True
                results.append(row)
                print(json.dumps({"case": case["case_id"], "provider": provider, "decision": row["decision"], "reason": row["reason"]}, ensure_ascii=False), flush=True)
    assert_corpus_sources(corpus)
    report = {"version": "enrichment-comparison-v1", "split": split, "freeze": snapshot, "labels_human_confirmed": False,
              "rows": results, "budget": ledger.summary(), "production_applied": False}
    save(output / f"{split}-comparison.json", report)
    return {"split": split, "rows": len(results), "budget": report["budget"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare-old", "discover", "evaluate", "budget"])
    parser.add_argument("--output", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--split", choices=["old", "new"], default="old")
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--dns-mode", choices=["system", "https"], default="system")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=False)
    if args.command == "prepare-old":
        result = prepare_old(args.output)
    elif args.command == "discover":
        result = discover(args.output, execute=args.execute, dns_mode=args.dns_mode)
    elif args.command == "evaluate":
        if args.registry is None or args.labels is None:
            parser.error("evaluate requires --registry and --labels")
        result = evaluate(args.output, args.split, args.registry, args.labels, execute=args.execute)
    else:
        result = TrialBudget(args.output / "calls.sqlite").summary()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
