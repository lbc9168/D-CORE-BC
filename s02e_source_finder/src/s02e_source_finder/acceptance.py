"""Process frozen development candidates independently with bounded discovery."""

import argparse
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import requests
from dotenv import load_dotenv
from pydantic import Field, model_validator

from . import contracts, fetch, grounding, publishers, source_loop, structured, structured_client
from .contracts import EventContext, Page, StrictModel
from .fetch import PublicFetcher
from .pilot import PilotStore, assert_sources, digest, private_path
from .publishers import PublisherRegistry
from .sample import ROOT, file_hash
from .source_loop import LoopInputs, SeedPage, evaluate_page, normalized_url, project_reference, resolve_citation, stage_satisfied, write_frozen, service_blocked

logger = logging.getLogger(__name__)


class FrozenLabelFile(StrictModel):
    """Bind an independently reviewed answer file without loading its contents."""
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class LabelsManifest(StrictModel):
    """Retain label provenance without imposing a positive-count or coverage gate."""
    files: list[FrozenLabelFile] = Field(min_length=1)
    known_positive_distinct_event_count: int = Field(ge=0, le=10)


class AcceptanceInputs(LoopInputs):
    """Freeze bounded development targets and budgets; permit previously paid results."""
    version: str = "acceptance-v1"
    contexts: dict[str, EventContext] = Field(min_length=1, max_length=10)
    max_search_calls: int = Field(default=20, ge=0, le=20)
    max_model_calls: int = Field(default=60, ge=0, le=60)
    search_concurrency: int = Field(default=4, ge=1, le=4)
    selection_path: str
    selection_sha256: str
    labels_path: str
    labels_sha256: str

    @model_validator(mode="after")
    def fresh_batch(self):
        """Keep project selection and candidate budgets explicit; this is development validation."""
        if len({context.project_id for context in self.contexts.values()}) != len(self.contexts):
            raise ValueError("acceptance_requires_ten_distinct_projects")
        if any(sum(seed.event_id == event_id for seed in self.seeds) > 2 for event_id in self.contexts):
            raise ValueError("acceptance_legacy_candidate_limit")
        return self


def search_safely(request: dict) -> dict:
    """Capture each in-flight result without losing reservations when another worker fails."""
    try:
        return grounding.search(request)
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        logger.error("acceptance_search_failed type=%s", type(exc).__name__)
        return {"error": str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__}


def search_round(jobs: list[dict], store: PilotStore, key: str, folder: Path, concurrency: int) -> list[dict]:
    """Reserve in the main thread, run at most four calls, then persist every returned outcome."""
    completed = []
    for offset in range(0, len(jobs), concurrency):
        batch = jobs[offset:offset + concurrency]
        cached, pending = {}, []
        for index, job in enumerate(batch):
            old = store.conn.execute("SELECT result_json FROM attempts WHERE run_key=? AND case_id=?", (key, job["case_id"])).fetchone()
            if old:
                cached[job["case_id"]] = json.loads(old[0]) if old[0] else {"error": "reserved_search_unresolved"}
            else:
                request_sha = digest({"provider": "gemini", "model": grounding.MODEL, "endpoint": "generateContent", "request": job["request"]})
                reused = store.cached_request(request_sha)
                if reused is not None:
                    cached[job["case_id"]] = reused
                    continue
                if getattr(store, "search_blocked", False):
                    cached[job["case_id"]] = {"error": "search_service_stopped"}
                    continue
                try:
                    store.reserve(key, job["case_id"], requested=True, request_sha=request_sha)
                except ValueError as exc:
                    if str(exc) != "pilot_call_budget_exhausted":
                        raise
                    logger.warning("acceptance_search_budget_exhausted run=%s", key)
                    cached[job["case_id"]] = {"error": "search_budget_exhausted"}
                    store.search_blocked = True
                    continue
                pending.append(job)
        # Worker threads never share or write SQLite connections.
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [(job, executor.submit(search_safely, job["request"])) for job in pending]
            for job, future in futures:
                result = future.result()
                store.finish(key, job["case_id"], result)
                cached[job["case_id"]] = result
        for job in batch:
            if not store.conn.execute("SELECT 1 FROM attempts WHERE run_key=? AND case_id=?", (key, job["case_id"])).fetchone():
                store.reserve(key, job["case_id"], requested=False)
                store.finish(key, job["case_id"], cached[job["case_id"]])
            result = job | {"result": cached[job["case_id"]]}
            write_frozen(folder / f"search-{job['case_id']}.json", result)
            completed.append(result)
        if any(service_blocked(item["result"].get("error")) for item in completed):
            store.search_blocked = True
    return completed


def run(inputs_path: Path, registry_path: Path, *, execute: bool = False) -> dict:
    """Execute the frozen acceptance batch without changing labels, targets, or source data."""
    inputs_path, registry_path = private_path(inputs_path, existing=True), private_path(registry_path, existing=True)
    inputs = AcceptanceInputs.model_validate_json(inputs_path.read_text(encoding="utf-8"))
    registry = PublisherRegistry.model_validate_json(registry_path.read_text(encoding="utf-8"))
    assert_sources(inputs.source_hashes)
    manifest = LabelsManifest.model_validate_json(private_path(Path(inputs.labels_path), existing=True).read_text(encoding="utf-8"))
    for path, expected in [(inputs.selection_path, inputs.selection_sha256), (inputs.labels_path, inputs.labels_sha256)] + [(item.path, item.sha256) for item in manifest.files] + [(seed.parent_file, seed.parent_sha256) for seed in inputs.seeds]:
        if file_hash(private_path(Path(path), existing=True)) != expected:
            raise ValueError("acceptance_frozen_parent_changed")
    for reusable in inputs.reusable_extractions.values():
        if file_hash(private_path(Path(reusable["parent_file"]), existing=True)) != reusable["parent_sha256"]:
            raise ValueError("extraction_parent_changed")
    snapshot = {"input_sha256": file_hash(inputs_path), "registry_sha256": file_hash(registry_path), "labels_sha256": inputs.labels_sha256,
                "selection_sha256": inputs.selection_sha256, "runner_sha256": file_hash(Path(__file__)),
                "code_hashes": {name: file_hash(Path(module.__file__)) for name, module in {"contracts": contracts, "fetch": fetch, "grounding": grounding, "publishers": publishers, "source_loop": source_loop, "structured": structured, "client": structured_client}.items()},
                "schema": structured.Extraction.model_json_schema(), "system": structured.SYSTEM}
    key = digest(snapshot)[:24]
    summary = {"run_key": key, "execute": execute, "events": len(inputs.contexts), "seed_candidates": len(inputs.seeds), "max_search_calls": inputs.max_search_calls,
               "max_model_calls": inputs.max_model_calls, "search_concurrency": inputs.search_concurrency, "labels_sha256": inputs.labels_sha256}
    if not execute:
        return summary
    load_dotenv(ROOT / ".env", override=False)
    folder = inputs_path.parent / key
    folder.mkdir(exist_ok=True)
    write_frozen(folder / "rules.json", snapshot)
    search_key, model_key = key + "-search", key + "-validate"
    fetcher = PublicFetcher(dns_mode="https")
    results, searches, failures = [], [], []
    seen = {event_id: set() for event_id in inputs.contexts}
    new_counts = {event_id: 0 for event_id in inputs.contexts}
    try:
        with closing(PilotStore(inputs_path.parent / "acceptance.sqlite")) as store:
            store.begin(search_key, snapshot["input_sha256"], snapshot["registry_sha256"], "gemini_google_search", inputs.max_search_calls)
            store.begin(model_key, snapshot["input_sha256"], snapshot["registry_sha256"], "deepseek", inputs.max_model_calls)
            for seed in inputs.seeds:
                result = evaluate_page(inputs, registry, seed, store, model_key)
                results.append(result)
                seen[seed.event_id].add(normalized_url(seed.page.final_url))
                write_frozen(folder / f"result-{result['case_id']}.json", result)
            for round_number in (1, 2):
                jobs = [{"case_id": f"{event_id}-round-{round_number}", "event_id": event_id, "round": round_number, "request": grounding.search_request(context, inputs.cards[event_id], round_number)}
                        for event_id, context in inputs.contexts.items() if new_counts[event_id] < inputs.max_new_candidates_per_event
                        and not stage_satisfied([row for row in results if row["event_id"] == event_id], inputs.cards[event_id])]
                if round_number == 1:
                    for job in jobs:
                        prior = inputs.prior_searches.get(job["event_id"])
                        if prior is not None and not store.conn.execute("SELECT 1 FROM attempts WHERE run_key=? AND case_id=?", (search_key, job["case_id"])).fetchone():
                            store.reserve(search_key, job["case_id"], requested=False)
                            store.finish(search_key, job["case_id"], prior)
                current = search_round(jobs, store, search_key, folder, inputs.search_concurrency)
                searches += current
                for item in current:
                    event_id = item["event_id"]
                    for chunk in item["result"].get("grounding", {}).get("groundingChunks", []):
                        if new_counts[event_id] >= inputs.max_new_candidates_per_event:
                            break
                        web = chunk.get("web")
                        if not web:
                            continue
                        try:
                            url = resolve_citation(web["uri"], fetcher)
                        except (requests.RequestException, ValueError, OSError) as exc:
                            logger.warning("acceptance_citation_failed event=%s type=%s", event_id, type(exc).__name__)
                            failures.append({"event_id": event_id, "citation": web["uri"], "problem": str(exc) if isinstance(exc, ValueError) else type(exc).__name__})
                            continue
                        if normalized_url(url) in seen[event_id]:
                            continue
                        seen[event_id].add(normalized_url(url))
                        new_counts[event_id] += 1
                        page_path = folder / f"page-{digest([event_id, url])[:20]}.json"
                        if page_path.exists():
                            page = Page.model_validate_json(page_path.read_text(encoding="utf-8"))
                        else:
                            page = fetcher.fetch(url)
                            write_frozen(page_path, page.model_dump())
                        seed = SeedPage(event_id=event_id, origin="google_new", page=page, parent_file=str(page_path), parent_sha256=file_hash(page_path))
                        result = evaluate_page(inputs, registry, seed, store, model_key)
                        results.append(result)
                        write_frozen(folder / f"result-{result['case_id']}.json", result)
            attempts = store.conn.execute("SELECT run_key,requested,status FROM attempts WHERE run_key IN (?,?)", (search_key, model_key)).fetchall()
            summary.update(search_attempts=sum(row["requested"] for row in attempts if row["run_key"] == search_key), model_attempts=sum(row["requested"] for row in attempts if row["run_key"] == model_key),
                           unresolved_attempts=sum(row["status"] == "reserved" for row in attempts))
    finally:
        fetcher.close()
        assert_sources(inputs.source_hashes)
    events = [{"event_id": event_id, "project_id": context.project_id,
               "event_references": sum(row["decision"] == "event_reference" for row in results if row["event_id"] == event_id),
               "project_references": sum(row["decision"] == "project_reference" for row in results if row["event_id"] == event_id),
               "external_references": [ref for row in results if row["event_id"] == event_id if (ref := project_reference(context, row, registry)) is not None]}
              for event_id, context in inputs.contexts.items()]
    summary.update(source_unchanged=True, complete=True, completion_basis="all_selected_candidates_have_status")
    write_frozen(folder / "report.json", summary | {"results": results, "searches": searches, "events_result": events, "discovery_failures": failures})
    return summary | {"report": str(folder / "report.json"), "coverage": [{k: v for k, v in row.items() if k != "external_references"} for row in events]}


def main() -> None:
    """Preview by default; require an explicit execution flag for external requests."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path)
    parser.add_argument("registry", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.execute:
        logging.basicConfig(filename=private_path(args.inputs, existing=True).parent / "acceptance.log", level=logging.INFO, encoding="utf-8")
    print(json.dumps(run(args.inputs, args.registry, execute=args.execute), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
