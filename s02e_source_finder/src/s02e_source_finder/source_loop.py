"""Five-event development loop: cached references, bounded Google discovery, and verification."""

import argparse
import hashlib
import json
import logging
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit, urljoin

import requests
from dotenv import load_dotenv
from pydantic import Field, model_validator

from . import contracts, fetch, grounding, publishers, structured, structured_client
from .contracts import EventContext, Judgement, Page, StrictModel, make_reference
from .fetch import PublicFetcher, public_url
from .pilot import PilotStore, assert_sources, digest, private_path
from .publishers import PublisherRegistry, verify_publisher
from .sample import ROOT, file_hash, write_json
from .structured import Extraction, SYSTEM, TargetCard, decide, model_payload
from .structured_client import ExtractionError, extract

logger = logging.getLogger(__name__)
ACCEPTED = {"event_reference", "project_reference"}


def write_frozen(path: Path, data: dict) -> None:
    """Allow an identical replay while refusing to overwrite a different audit artifact."""
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != data:
            raise ValueError("frozen_output_changed")
        return
    write_json(path, data)


class SeedPage(StrictModel):
    """Bind a cached page to its target and discovery provenance."""
    event_id: str
    origin: str
    page: Page
    parent_file: str
    parent_sha256: str


class LoopInputs(StrictModel):
    """A frozen development batch with at most five events and explicit total budgets."""
    version: str = "google-source-loop-v1"
    source_hashes: dict[str, str]
    contexts: dict[str, EventContext] = Field(min_length=1, max_length=5)
    cards: dict[str, TargetCard]
    seeds: list[SeedPage]
    reusable_extractions: dict[str, dict]
    prior_searches: dict[str, dict] = Field(default_factory=dict)
    max_search_calls: int = Field(default=10, ge=0, le=10)
    max_model_calls: int = Field(default=24, ge=0, le=24)
    max_new_candidates_per_event: int = Field(default=4, ge=1, le=4)

    @model_validator(mode="after")
    def bound_targets(self):
        """Reject mismatched cards or seeds before opening a ledger or contacting providers."""
        if set(self.contexts) != set(self.cards):
            raise ValueError("loop_cards_do_not_match_targets")
        for key, context in self.contexts.items():
            if context.event_id != key or self.cards[key].input_fingerprint != context.fingerprint():
                raise ValueError("loop_context_fingerprint_mismatch")
        if any(seed.event_id not in self.contexts for seed in self.seeds) or set(self.prior_searches) - set(self.contexts):
            raise ValueError("loop_seed_target_unknown")
        return self


def prompt_hash(context: EventContext, page: Page) -> str:
    """Reconstruct the exact extraction prompt for safe reuse of already paid responses."""
    prompt = SYSTEM + "\nJSON schema:\n" + json.dumps(Extraction.model_json_schema(), ensure_ascii=False) + json.dumps(model_payload(context, page), ensure_ascii=False)
    return hashlib.sha256(prompt.encode()).hexdigest()


def stage_satisfied(rows: list[dict], card: TargetCard) -> bool:
    """Stop discovery at a verified event, or a project reference for an ambiguous target stage."""
    return any(row["decision"] == "event_reference" or card.stage_ambiguous and row["decision"] == "project_reference" for row in rows)


def normalized_url(url: str) -> tuple[str, str, str]:
    """Deduplicate the same host/path/query across HTTP and HTTPS without dropping queries."""
    parsed = urlsplit(url)
    return (parsed.hostname or "").lower(), parsed.path.rstrip("/"), parsed.query


def resolve_citation(uri: str, fetcher: PublicFetcher) -> str:
    """Resolve a bounded Google citation redirect while validating every destination."""
    current = public_url(uri, dns_mode=fetcher.dns_mode)
    for _ in range(3):
        if (urlsplit(current).hostname or "") != "vertexaisearch.cloud.google.com":
            return current
        with fetcher.session.get(current, timeout=(10, 20), allow_redirects=False, stream=True) as response:
            if response.status_code not in {301, 302, 303, 307, 308}:
                # Defer rate limits and transport failures, rather than retry without a bound.
                raise ValueError(f"citation_http_{response.status_code}")
            location = response.headers.get("Location")
            if not location:
                raise ValueError("citation_redirect_without_location")
            current = public_url(urljoin(current, location), dns_mode=fetcher.dns_mode)
    raise ValueError("citation_redirect_limit")


def service_blocked(error: object) -> bool:
    """Only authentication, payment or rate-limit errors stop new calls; never retry here."""
    return str(error) in {"provider_http_401", "provider_http_402", "provider_http_403", "provider_http_429", "provider_key_missing", "gemini_key_missing", "search_budget_exhausted"}


def evaluate_page(inputs: LoopInputs, registry: PublisherRegistry, seed: SeedPage, store: PilotStore, key: str) -> dict:
    """Reuse exact prior extraction when possible and avoid paid validation of unverified sources."""
    context, card, page = inputs.contexts[seed.event_id], inputs.cards[seed.event_id], seed.page
    case_id = digest([seed.event_id, page.final_url, page.text_hash, page.source_statements])[:20] if not page.source_statements else digest([seed.event_id, page.model_dump()])[:20]
    previous = store.conn.execute("SELECT status,result_json FROM attempts WHERE run_key=? AND case_id=?", (key, case_id)).fetchone()
    if previous:
        prior = json.loads(previous["result_json"]) if previous["result_json"] else {"case_id": case_id, "event_id": seed.event_id, "decision": "pending", "processing_status": "pending", "reason_codes": ["reserved_attempt_unresolved"], "url": page.final_url, "title": page.title}
        if any(service_blocked(code) for code in prior.get("reason_codes", [])):
            store.validation_blocked = True
        return prior
    source = verify_publisher(page, registry)
    fingerprint = prompt_hash(context, page)
    reusable = inputs.reusable_extractions.get(fingerprint)
    request_sha = digest(structured_client.request_spec(model_payload(context, page), "deepseek"))
    cached = store.cached_request(request_sha) if not reusable else None
    if cached is None and not reusable:
        # Rule/schema changes can rejudge already extracted facts without buying new facts.
        # Bind legacy responses to the exact target and page, not a broad prompt substring.
        for old in store.conn.execute("SELECT run_key,result_json FROM attempts WHERE case_id=? AND result_json IS NOT NULL ORDER BY rowid DESC", (case_id,)):
            candidate = json.loads(old["result_json"])
            if candidate.get("context_fingerprint") == context.fingerprint() and candidate.get("page") == page.model_dump() and (candidate.get("extraction") or candidate.get("extraction_failed")):
                cached = candidate | {"parent_run": old["run_key"]}
                break
    if cached and any(service_blocked(code) for code in cached.get("reason_codes", [])):
        store.validation_blocked = True
    called = not page.problem and not reusable and cached is None and not source["problem"] and not getattr(store, "validation_blocked", False)
    deferred = None
    try:
        store.reserve(key, case_id, requested=called, request_sha=request_sha if called else None)
    except ValueError as exc:
        if str(exc) != "pilot_call_budget_exhausted":
            raise
        called, deferred = False, "model_budget_exhausted"
        store.reserve(key, case_id, requested=False)
    result = {"case_id": case_id, "event_id": seed.event_id, "project_id": context.project_id, "url": page.final_url, "title": page.title, "origin": seed.origin, "page": page.model_dump(),
              "decision": "pending", "reason_codes": [], "source": source, "model_called": called, "context_fingerprint": context.fingerprint(), "source_registry_checked": True}
    if page.problem:
        permanent = page.problem in {"not_chinese", "http_404", "http_410", "goalfore_not_external", "unsafe_url", "redirect_to_homepage"}
        result.update(decision="rejected" if permanent else "pending", reason_codes=[page.problem])
    elif cached is not None and not cached.get("extraction"):
        result.update(reason_codes=cached.get("reason_codes", [cached.get("error", "cached_extraction_failed")]), call=cached.get("call", {}), reused_request=True, extraction_failed=True)
    elif deferred or getattr(store, "validation_blocked", False) and not reusable and cached is None:
        result["reason_codes"] = [deferred or "validation_service_stopped"]
    elif not reusable and cached is None and source["problem"]:
        result["reason_codes"] = [source["problem"]]
    else:
        try:
            if reusable:
                if reusable["call"]["prompt_sha256"] != fingerprint:
                    raise ValueError("reusable_extraction_mismatch")
                extraction = Extraction.model_validate(reusable["extraction"])
                result["reused_extraction"] = {"parent_file": reusable["parent_file"], "parent_sha256": reusable["parent_sha256"], "prompt_sha256": fingerprint}
            elif cached is not None:
                extraction = Extraction.model_validate(cached["extraction"])
                result.update(call=cached.get("call", {}), reused_request=True, reused_from_run=cached.get("parent_run"))
            else:
                extraction, call = extract(model_payload(context, page), "deepseek")
                result["call"] = call
            result["extraction"] = extraction.model_dump()
            result.update(decide(context, card, page, extraction, registry))
            if not result["source"]:
                result["source"] = source
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            logger.error("loop_validation_failed event=%s type=%s", seed.event_id, type(exc).__name__)
            result.update(decision="pending", reason_codes=[str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__], extraction_failed=True)
            if isinstance(exc, ExtractionError):
                result["call"] = exc.call
            if service_blocked(exc):
                result["provider_failed"] = True
                store.validation_blocked = True
    result["reason"] = ";".join(result["reason_codes"])
    result["processing_status"] = "failed" if result.get("extraction_failed") or page.problem else {"event_reference": "success", "project_reference": "success", "rejected": "excluded", "pending": "pending"}[result["decision"]]
    if result["decision"] in ACCEPTED:
        try:
            project_reference(context, result, registry)
        except ValueError as exc:
            logger.error("reference_projection_failed event=%s reason=%s", seed.event_id, str(exc))
            result.update(decision="pending", processing_status="pending", reason_codes=[str(exc)], reason=str(exc), projection_failed=True)
    store.finish(key, case_id, result)
    return result


def project_reference(context: EventContext, row: dict, registry: PublisherRegistry) -> dict | None:
    """Project independently verified results into the existing public reference metadata contract."""
    if row["decision"] not in ACCEPTED:
        return None
    page = Page.model_validate(row["page"])
    source = row["source"]
    visible = "\n".join([page.title, page.site_name, page.text] + [item.evidence for item in page.source_statements])
    proofs = source["evidence"] + [name for record in registry.records for name in [record.name] + record.aliases if record.name == source["publisher"]]
    publisher_evidence = next((quote for quote in proofs if quote and quote in visible), None)
    if not publisher_evidence:
        raise ValueError("projection_publisher_evidence_missing")
    event_quotes = [check["evidence"] for check in row["event_checks"] if all(check.get(key) for key in ("actual_action", "stage_matches", "scope_matches", "near_project_evidence", "date_matches"))]
    event_scope = row["decision"] == "event_reference"
    verdict = Judgement(decision=row["decision"], reason=row["reason"], language="zh", publisher=source["publisher"], source_type=source["source_type"], publisher_evidence=publisher_evidence,
                        same_project=True, project_evidence=row["identity_evidence"], same_stage=event_scope, same_time=event_scope, event_evidence=event_quotes,
                        conflicts=[], is_reprint=source["is_reprint"], original_publisher=source["original_publisher"])
    stage_en = {"中标": "Awarded", "签约": "Signed", "开工": "Construction started", "在建": "Under construction", "竣工": "Completed", "投入运营": "Operational"}.get(context.event.get("project_stage"), "")
    reference = make_reference(context, page, verdict, stage_en)
    if reference is None:
        raise ValueError("projection_evidence_validation_failed")
    interval = row.get("event_date")
    reference["event_date"] = (interval[0] if interval[0] == interval[1] else "—".join(interval)) if event_scope and interval else None
    return reference


def run(inputs_path: Path, registry_path: Path, *, execute: bool = False) -> dict:
    """Run a frozen five-event development loop under independent search and validation caps."""
    inputs_path, registry_path = private_path(inputs_path, existing=True), private_path(registry_path, existing=True)
    inputs = LoopInputs.model_validate_json(inputs_path.read_text(encoding="utf-8"))
    registry = PublisherRegistry.model_validate_json(registry_path.read_text(encoding="utf-8"))
    assert_sources(inputs.source_hashes)
    for seed in inputs.seeds:
        if file_hash(private_path(Path(seed.parent_file), existing=True)) != seed.parent_sha256:
            raise ValueError("seed_parent_changed")
    for reusable in inputs.reusable_extractions.values():
        if file_hash(private_path(Path(reusable["parent_file"]), existing=True)) != reusable["parent_sha256"]:
            raise ValueError("extraction_parent_changed")
    snapshot = {"input_sha256": file_hash(inputs_path), "registry_sha256": file_hash(registry_path), "code_hashes": {name: file_hash(Path(module.__file__)) for name, module in {"contracts": contracts, "fetch": fetch, "grounding": grounding, "publishers": publishers, "structured": structured, "client": structured_client}.items()}, "runner_sha256": file_hash(Path(__file__)), "schema": Extraction.model_json_schema(), "system": SYSTEM}
    key = digest(snapshot)[:24]
    summary = {"run_key": key, "events": len(inputs.contexts), "seed_candidates": len(inputs.seeds), "max_search_calls": inputs.max_search_calls, "max_model_calls": inputs.max_model_calls, "execute": execute}
    if not execute:
        return summary
    load_dotenv(ROOT / ".env", override=False)
    folder = inputs_path.parent / key
    folder.mkdir(exist_ok=True)
    write_frozen(folder / "rules.json", snapshot)
    search_key, model_key = key + "-search", key + "-validate"
    fetcher = PublicFetcher(dns_mode="https")
    results, searches, failures = [], [], []
    try:
        with closing(PilotStore(inputs_path.parent / "loop.sqlite")) as store:
            store.begin(search_key, snapshot["input_sha256"], snapshot["registry_sha256"], "gemini_google_search", inputs.max_search_calls)
            store.begin(model_key, snapshot["input_sha256"], snapshot["registry_sha256"], "deepseek", inputs.max_model_calls)
            for event_id, context in inputs.contexts.items():
                rows = []
                for seed in [item for item in inputs.seeds if item.event_id == event_id]:
                    row = evaluate_page(inputs, registry, seed, store, model_key)
                    rows.append(row)
                results += rows
                card = inputs.cards[event_id]
                seen = {normalized_url(row["url"]) for row in rows}
                new_count = 0
                for round_number in (1, 2):
                    if stage_satisfied(rows, card) or new_count >= inputs.max_new_candidates_per_event:
                        break
                    prior = inputs.prior_searches.get(event_id) if round_number == 1 else None
                    request = grounding.search_request(context, card, round_number)
                    search_id = f"{event_id}-round-{round_number}"
                    request_sha = digest({"provider": "gemini", "model": grounding.MODEL, "endpoint": "generateContent", "request": request})
                    cached_search = store.cached_request(request_sha)
                    previous = store.conn.execute("SELECT result_json FROM attempts WHERE run_key=? AND case_id=?", (search_key, search_id)).fetchone()
                    if previous:
                        search_result = json.loads(previous[0]) if previous[0] else {"error": "reserved_search_unresolved"}
                    elif cached_search is not None:
                        search_result = cached_search
                    elif prior:
                        search_result = prior | {"reused_search": True}
                        store.reserve(search_key, search_id, requested=False)
                        store.finish(search_key, search_id, search_result)
                    elif getattr(store, "search_blocked", False) or inputs.max_search_calls == 0:
                        search_result = {"error": "search_service_stopped"}
                    else:
                        try:
                            store.reserve(search_key, search_id, requested=True, request_sha=request_sha)
                        except ValueError as exc:
                            if str(exc) != "pilot_call_budget_exhausted":
                                raise
                            store.search_blocked = True
                            store.reserve(search_key, search_id, requested=False)
                            search_result = {"error": "search_budget_exhausted"}
                        else:
                            search_result = None
                        try:
                            if search_result is None:
                                search_result = grounding.search(request)
                        except (requests.RequestException, ValueError, RuntimeError) as exc:
                            logger.error("loop_search_failed event=%s type=%s", event_id, type(exc).__name__)
                            search_result = {"error": str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__}
                        store.finish(search_key, search_id, search_result)
                    if not store.conn.execute("SELECT 1 FROM attempts WHERE run_key=? AND case_id=?", (search_key, search_id)).fetchone():
                        store.reserve(search_key, search_id, requested=False)
                        store.finish(search_key, search_id, search_result)
                    searches.append({"event_id": event_id, "round": round_number, "result": search_result})
                    write_frozen(folder / f"search-{search_id}.json", {"request": None if prior else request, "result": search_result})
                    if search_result.get("error"):
                        failures.append({"event_id": event_id, "problem": search_result["error"]})
                        store.search_blocked = service_blocked(search_result["error"]) or getattr(store, "search_blocked", False)
                    for chunk in search_result.get("grounding", {}).get("groundingChunks", []):
                        web = chunk.get("web")
                        if not web or new_count >= inputs.max_new_candidates_per_event:
                            continue
                        try:
                            url = resolve_citation(web["uri"], fetcher)
                        except (requests.RequestException, ValueError, OSError) as exc:
                            logger.error("loop_citation_failed event=%s type=%s", event_id, type(exc).__name__)
                            failures.append({"event_id": event_id, "citation": web["uri"], "problem": str(exc) if isinstance(exc, ValueError) else type(exc).__name__})
                            continue
                        if normalized_url(url) in seen:
                            continue
                        seen.add(normalized_url(url))
                        new_count += 1
                        page_path = folder / f"page-{digest([event_id, url])[:20]}.json"
                        if page_path.exists():
                            page = Page.model_validate_json(page_path.read_text(encoding="utf-8"))
                        else:
                            page = fetcher.fetch(url)
                            write_json(page_path, page.model_dump())
                        seed = SeedPage(event_id=event_id, origin="google_new", page=page, parent_file=str(page_path), parent_sha256=file_hash(page_path))
                        row = evaluate_page(inputs, registry, seed, store, model_key)
                        rows.append(row)
                        results.append(row)

            attempts = store.conn.execute("SELECT run_key,requested,status,result_json FROM attempts WHERE run_key IN (?,?)", (search_key, model_key)).fetchall()
            summary.update(search_attempts=sum(row["requested"] for row in attempts if row["run_key"] == search_key), model_attempts=sum(row["requested"] for row in attempts if row["run_key"] == model_key), unresolved_attempts=sum(row["status"] == "reserved" for row in attempts))
    finally:
        fetcher.close()
        assert_sources(inputs.source_hashes)
    summary.update(source_unchanged=True, registry_human_confirmed=registry.human_confirmed, results=results, searches=searches, discovery_failures=failures,
                   complete=True, completion_basis="all_selected_candidates_have_status")
    summary["events_result"] = [{"event_id": event_id, "project_id": context.project_id, "event_references": sum(row["decision"] == "event_reference" for row in results if row["event_id"] == event_id),
                                 "project_references": sum(row["decision"] == "project_reference" for row in results if row["event_id"] == event_id),
                                 "external_references": [ref for row in results if row["event_id"] == event_id if (ref := project_reference(context, row, registry)) is not None]} for event_id, context in inputs.contexts.items()]
    path = folder / "report.json"
    write_frozen(path, summary)
    return {name: value for name, value in summary.items() if name not in {"results", "searches", "events_result"}} | {"report": str(path), "coverage": [{k: v for k, v in row.items() if k != "external_references"} for row in summary["events_result"]]}


def main() -> None:
    """Default to a free preview of the frozen five-event batch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path)
    parser.add_argument("registry", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.execute:
        logging.basicConfig(filename=private_path(args.inputs, existing=True).parent / "loop.log", level=logging.INFO, encoding="utf-8")
    print(json.dumps(run(args.inputs, args.registry, execute=args.execute), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
