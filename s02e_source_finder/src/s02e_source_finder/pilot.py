"""Frozen thirty-event calibration artifacts, bounded retrieval and resumable evaluations."""

import argparse
import hashlib
import json
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import requests
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .contracts import EventContext, Judgement, Page, validate_evidence
from .fetch import PublicFetcher
from .sample import MODULE, ROOT, SYSTEM, JudgementOutputError, file_hash, judge, write_json

logger = logging.getLogger(__name__)


def digest(value: object) -> str:
    """Use canonical JSON fingerprints for immutable corpus and request identities."""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class PilotCase(BaseModel):
    """Bind a captured candidate to one frozen project/event and evaluation split."""
    model_config = ConfigDict(extra="forbid")
    case_id: str
    domain: Literal["main", "tx", "mf"]
    split: Literal["development", "holdout"]
    context: EventContext
    candidate: dict
    page: Page


class PilotCorpus(BaseModel):
    """Validate complete pilot input before models or budget writes are allowed."""
    model_config = ConfigDict(extra="forbid")
    version: Literal["pilot-corpus-v1"] = "pilot-corpus-v1"
    selected_sha256: str
    created_at: str
    event_count: int = Field(ge=1, le=30)
    cases: list[PilotCase] = Field(max_length=120)
    events_without_candidates: list[dict]
    source_hashes: dict[str, str]


class Label(BaseModel):
    """A source-grounded independent label; never presented as user-confirmed by default."""
    model_config = ConfigDict(extra="forbid")
    decision: Literal["event_reference", "project_reference", "pending", "rejected"]
    reason: str = Field(min_length=1)
    evidence: list[str]


class LabelSet(BaseModel):
    """Keep authorship and human confirmation separate from provisional score labels."""
    model_config = ConfigDict(extra="forbid")
    corpus_sha256: str
    reviewer: str
    human_confirmed: bool = False
    created_at: str
    labels: dict[str, Label]


def private_path(path: Path, *, existing: bool = False) -> Path:
    """Keep pilot state inside the module's private local data directory."""
    path = path.resolve(strict=existing)
    if not path.is_relative_to((MODULE / "data").resolve()):
        raise ValueError("pilot_path_must_be_private")
    return path


def assert_sources(hashes: dict[str, str]) -> None:
    """Refuse stale corpus use when any recorded source database has changed."""
    if not hashes or any(file_hash(Path(path)) != value for path, value in hashes.items()):
        raise ValueError("source_database_changed")


def prepare(selected_path: Path, *, execute: bool = False, dns_mode: str = "system", per_event: int = 1) -> dict:
    """Capture a bounded set once; resumed preparation reuses already saved pages."""
    selected_path = private_path(selected_path, existing=True)
    if per_event not in (1, 2):
        raise ValueError("pilot_per_event_must_be_one_or_two")
    selected = json.loads(selected_path.read_text(encoding="utf-8"))
    events = selected.get("events") if isinstance(selected, dict) else None
    if not isinstance(events, list) or len(events) != 30:
        raise ValueError("pilot_requires_exactly_30_events")
    parsed = []
    for row in events:
        row["domain"] = str(row.get("domain", "")).lower()
        context = EventContext.model_validate(row["context"])
        if row["domain"] not in {"main", "tx", "mf"} or row["split"] not in {"development", "holdout"}:
            raise ValueError("invalid_domain_or_split")
        candidates = row["candidates"]
        if not isinstance(candidates, list) or any(not isinstance(item, dict) or not isinstance(item.get("url"), str) for item in candidates):
            raise ValueError("invalid_candidates")
        parsed.append((row, context, candidates[:per_event]))
    summary = {"events": len(events), "candidate_associations": sum(len(items) for _, _, items in parsed), "no_candidate_events": sum(not items for _, _, items in parsed), "execute": execute}
    if not execute:
        return summary
    output = selected_path.parent / f"corpus-first-{per_event}.json"
    if output.exists():
        corpus = PilotCorpus.model_validate_json(output.read_text(encoding="utf-8"))
        if corpus.selected_sha256 != file_hash(selected_path):
            raise ValueError("selection_changed_do_not_overwrite_corpus")
        assert_sources(corpus.source_hashes)
        return {**summary, "corpus": str(output), "reused": True}
    source_paths = (ROOT / "s02a_duplicate_merger/data/db/bri_re_database.db", ROOT / "s01a_news_crawler/data/raw_news.db")
    source_hashes = {str(path.resolve()): file_hash(path) for path in source_paths}
    if selected.get("source_hashes") != source_hashes:
        raise ValueError("selection_sources_changed")
    page_dir = selected_path.parent / "pages"
    page_dir.mkdir(exist_ok=True)
    cases = []
    empty = []
    fetcher = PublicFetcher(dns_mode=dns_mode)
    try:
        for row, context, candidates in parsed:
            if not candidates:
                empty.append({"project_id": context.project_id, "event_id": context.event_id, "domain": row["domain"], "split": row["split"]})
            for candidate in candidates:
                page_path = page_dir / (digest(candidate["url"]) + ".json")
                if page_path.exists():
                    page = Page.model_validate_json(page_path.read_text(encoding="utf-8"))
                else:
                    page = fetcher.fetch(candidate["url"])
                    write_json(page_path, page.model_dump())
                case_id = digest([context.project_id, context.event_id, candidate["url"]])[:20]
                cases.append(PilotCase(case_id=case_id, domain=row["domain"], split=row["split"], context=context, candidate=candidate, page=page))
                logger.info("pilot_page event=%s problem=%s", context.event_id, page.problem)
    finally:
        fetcher.close()
        assert_sources(source_hashes)
    corpus = PilotCorpus(selected_sha256=file_hash(selected_path), created_at=datetime.now(timezone.utc).isoformat(), event_count=len(events), cases=cases, events_without_candidates=empty, source_hashes=source_hashes)
    write_json(output, corpus.model_dump())
    return {**summary, "corpus": str(output), "readable": sum(not case.page.problem for case in cases)}


class PilotStore:
    """Keep append-only model results and reserve every paid attempt transactionally."""

    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        with self.conn:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_key TEXT PRIMARY KEY, corpus_sha TEXT NOT NULL, rules_sha TEXT NOT NULL,
                    provider TEXT NOT NULL, call_limit INTEGER NOT NULL CHECK(call_limit >= 0),
                    created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'incomplete'
                );
                CREATE TABLE IF NOT EXISTS request_identities (
                    request_sha TEXT PRIMARY KEY, run_key TEXT NOT NULL, case_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    run_key TEXT NOT NULL REFERENCES runs(run_key), case_id TEXT NOT NULL,
                    requested INTEGER NOT NULL CHECK(requested IN (0,1)),
                    status TEXT NOT NULL, result_json TEXT,
                    PRIMARY KEY(run_key,case_id)
                );
            """)

    def begin(self, key: str, corpus_sha: str, rules_sha: str, provider: str, limit: int) -> None:
        """An existing run cannot silently acquire a different budget or fingerprint."""
        if not 0 <= limit <= 120:
            raise ValueError("invalid_pilot_call_limit")
        with self.conn:
            row = self.conn.execute("SELECT * FROM runs WHERE run_key=?", (key,)).fetchone()
            if row:
                if (row["corpus_sha"], row["rules_sha"], row["provider"], row["call_limit"]) != (corpus_sha, rules_sha, provider, limit):
                    raise ValueError("run_contract_changed")
            else:
                self.conn.execute("INSERT INTO runs(run_key,corpus_sha,rules_sha,provider,call_limit,created_at) VALUES(?,?,?,?,?,?)", (key, corpus_sha, rules_sha, provider, limit, datetime.now(timezone.utc).isoformat()))

    def cached_request(self, request_sha: str) -> dict | None:
        """Reuse success or failure across rule runs; unresolved requests must not be resent."""
        row = self.conn.execute("SELECT a.result_json FROM request_identities r JOIN attempts a ON a.run_key=r.run_key AND a.case_id=r.case_id WHERE r.request_sha=?", (request_sha,)).fetchone()
        return (json.loads(row[0]) if row[0] else {"error": "reserved_attempt_unresolved"}) if row else None

    def reserve(self, key: str, case_id: str, *, requested: bool, request_sha: str | None = None) -> bool:
        """Reserve before HTTP; an interrupted attempt is not automatically charged twice."""
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            if self.conn.execute("SELECT 1 FROM attempts WHERE run_key=? AND case_id=?", (key, case_id)).fetchone():
                self.conn.rollback()
                return False
            limit = self.conn.execute("SELECT call_limit FROM runs WHERE run_key=?", (key,)).fetchone()[0]
            count = self.conn.execute("SELECT COALESCE(SUM(requested),0) FROM attempts WHERE run_key=?", (key,)).fetchone()[0]
            if requested and count >= limit:
                raise ValueError("pilot_call_budget_exhausted")
            if request_sha:
                self.conn.execute("INSERT INTO request_identities VALUES(?,?,?)", (request_sha, key, case_id))
            self.conn.execute("INSERT INTO attempts VALUES(?,?,?,'reserved',NULL)", (key, case_id, int(requested)))
            self.conn.commit()
            return True
        except (sqlite3.Error, ValueError):
            self.conn.rollback()
            raise

    def finish(self, key: str, case_id: str, result: dict) -> None:
        """Persist a verdict or an explicit service error without replacing history."""
        with self.conn:
            changed = self.conn.execute("UPDATE attempts SET status='finished',result_json=? WHERE run_key=? AND case_id=? AND status='reserved'", (json.dumps(result, ensure_ascii=False), key, case_id)).rowcount
            if changed != 1:
                raise ValueError("attempt_not_reserved_or_already_final")

    def close(self) -> None:
        """Close the pilot database handle."""
        self.conn.close()


def rules_fingerprint() -> str:
    """Bind pilot comparisons to exact prompt, schema, guards and provider request code."""
    from . import contracts, sample
    return digest({"system": SYSTEM, "schema": Judgement.model_json_schema(), "contracts_sha": file_hash(Path(contracts.__file__)), "sample_sha": file_hash(Path(sample.__file__))})


def augment(corpus_path: Path, selected_path: Path, additions_path: Path, *, execute: bool = False, dns_mode: str = "system") -> dict:
    """Capture reviewed search candidates and title-extraction fixes into a new frozen corpus."""
    corpus_path = private_path(corpus_path, existing=True)
    selected_path = private_path(selected_path, existing=True)
    additions_path = private_path(additions_path, existing=True)
    corpus = PilotCorpus.model_validate_json(corpus_path.read_text(encoding="utf-8"))
    selected = json.loads(selected_path.read_text(encoding="utf-8"))
    additions = json.loads(additions_path.read_text(encoding="utf-8"))
    events = {row["context"]["event_id"]: row for row in selected["events"]}
    if not isinstance(additions, list) or len(additions) > 30 or any(not isinstance(row, dict) or row.get("event_id") not in events or not isinstance(row.get("url"), str) for row in additions):
        raise ValueError("invalid_or_unbounded_pilot_additions")
    if corpus.selected_sha256 != file_hash(selected_path):
        raise ValueError("selection_changed")
    assert_sources(corpus.source_hashes)
    refresh = [case for case in corpus.cases if case.page.problem == "insufficient_content" and len(case.page.text) > 50 and case.page.site_name]
    output = corpus_path.parent / "corpus-calibration-v1.json"
    summary = {"additions": len(additions), "refresh_empty_titles": len(refresh), "execute": execute, "output": str(output)}
    if not execute:
        return summary
    if output.exists():
        raise FileExistsError("calibration_corpus_already_frozen")
    page_dir = corpus_path.parent / "calibration-pages"
    page_dir.mkdir(exist_ok=True)
    fetcher = PublicFetcher(dns_mode=dns_mode)
    try:
        for case in refresh:
            cached = page_dir / (digest(case.page.url) + ".json")
            if cached.exists():
                case.page = Page.model_validate_json(cached.read_text(encoding="utf-8"))
            else:
                case.page = fetcher.fetch(case.page.url)
                write_json(cached, case.page.model_dump())
        for item in additions:
            event = events[item["event_id"]]
            context = EventContext.model_validate(event["context"])
            case_id = digest([context.project_id, context.event_id, item["url"]])[:20]
            if any(case.case_id == case_id for case in corpus.cases):
                continue
            cached = page_dir / (digest(item["url"]) + ".json")
            if cached.exists():
                page = Page.model_validate_json(cached.read_text(encoding="utf-8"))
            else:
                page = fetcher.fetch(item["url"])
                write_json(cached, page.model_dump())
            corpus.cases.append(PilotCase(case_id=case_id, domain=event["domain"].lower(), split=event["split"], context=context, candidate=item, page=page))
            logger.info("pilot_addition event=%s problem=%s", context.event_id, page.problem)
    finally:
        fetcher.close()
        assert_sources(corpus.source_hashes)
    represented = {case.context.event_id for case in corpus.cases}
    corpus.events_without_candidates = [row for row in corpus.events_without_candidates if row["event_id"] not in represented]
    # Revalidate after local list mutation so bounds remain enforced at the file boundary.
    corpus = PilotCorpus.model_validate(corpus.model_dump())
    write_json(output, corpus.model_dump())
    return summary | {"cases": len(corpus.cases), "readable": sum(not case.page.problem for case in corpus.cases), "events_without_candidates": len(corpus.events_without_candidates)}


def evaluate(corpus_path: Path, *, provider: str, max_calls: int, execute: bool = False, split: str = "development") -> dict:
    """Evaluate frozen pages with one provider, keeping incomplete/error runs explicit."""
    if provider not in {"gemini", "deepseek"} or not 0 <= max_calls <= 120 or split not in {"development", "holdout"}:
        raise ValueError("invalid_provider_or_budget")
    corpus_path = private_path(corpus_path, existing=True)
    corpus = PilotCorpus.model_validate_json(corpus_path.read_text(encoding="utf-8"))
    corpus_sha = file_hash(corpus_path)
    rules_sha = rules_fingerprint()
    cases = [case for case in corpus.cases if case.split == split]
    summary = {"provider": provider, "split": split, "events": len({case.context.event_id for case in cases}), "cases": len(cases), "readable": sum(not case.page.problem for case in cases), "max_calls": max_calls, "corpus_sha": corpus_sha, "rules_sha": rules_sha, "execute": execute}
    assert_sources(corpus.source_hashes)
    if not execute:
        return summary
    # Labels are frozen separately and are never included in model input.
    labels_path = corpus_path.with_suffix(".labels.json")
    if not labels_path.exists():
        raise ValueError("independent_labels_required_before_evaluation")
    labels = LabelSet.model_validate_json(labels_path.read_text(encoding="utf-8"))
    if labels.corpus_sha256 != corpus_sha or set(labels.labels) != {case.case_id for case in corpus.cases}:
        raise ValueError("labels_do_not_match_frozen_corpus")
    load_dotenv(ROOT / ".env", override=False)
    run_key = digest([corpus_sha, rules_sha, provider, split])[:24]
    with closing(PilotStore(corpus_path.parent / "pilot.sqlite")) as store:
        store.begin(run_key, corpus_sha, digest([rules_sha, file_hash(labels_path)]), provider, max_calls)
        stopped = None
        earlier = store.conn.execute("SELECT result_json FROM attempts WHERE run_key=? AND result_json IS NOT NULL", (run_key,)).fetchall()
        for row in earlier:
            reason = json.loads(row[0]).get("reason", "")
            if reason.startswith(("model_error:provider_http_", "model_error:RequestException", "model_error:ConnectionError", "model_error:Timeout")):
                stopped = "provider_previously_failed_this_run"
                break
        try:
            for case in ([] if stopped else cases):
                requested = not bool(case.page.problem)
                try:
                    fresh = store.reserve(run_key, case.case_id, requested=requested)
                except ValueError as exc:
                    if str(exc) != "pilot_call_budget_exhausted":
                        raise
                    stopped = str(exc)
                    break
                if not fresh:
                    continue
                result = {"case_id": case.case_id, "project_id": case.context.project_id, "event_id": case.context.event_id, "domain": case.domain, "split": case.split, "url": case.page.final_url}
                if case.page.problem:
                    permanent = case.page.problem in {"http_404", "http_410", "not_chinese", "goalfore_not_external", "unsafe_url", "non_public_host", "non_public_address", "redirect_to_homepage"}
                    result.update(decision="rejected" if permanent else "pending", reason=case.page.problem, model_called=False)
                else:
                    try:
                        verdict, call = judge(case.context, case.page, provider)
                        errors = validate_evidence(verdict, case.page)
                        result.update(verdict.model_dump())
                        result.update(call=call, validation_errors=errors, model_called=True, raw_decision=verdict.decision)
                        if errors:
                            result.update(decision="pending", reason="; ".join(errors))
                    except (requests.RequestException, ValidationError, ValueError, RuntimeError, TypeError, KeyError) as exc:
                        logger.error("pilot_judge_failed case=%s type=%s", case.case_id, type(exc).__name__)
                        reason = str(exc) if isinstance(exc, RuntimeError) and str(exc).startswith("provider_http_") else type(exc).__name__
                        result.update(decision="pending", reason="model_error:" + reason, model_called=True)
                        if isinstance(exc, JudgementOutputError):
                            result["call"] = exc.call
                        if isinstance(exc, (requests.RequestException, RuntimeError)):
                            stopped = reason
                store.finish(run_key, case.case_id, result)
                if stopped:
                    break
        finally:
            assert_sources(corpus.source_hashes)
        attempts = store.conn.execute("SELECT * FROM attempts WHERE run_key=?", (run_key,)).fetchall()
        results = [json.loads(row["result_json"]) for row in attempts if row["result_json"]]
        unresolved = [row["case_id"] for row in attempts if row["status"] == "reserved"]
        complete = len(results) == len(cases) and not unresolved and not any(str(row.get("reason", "")).startswith("model_error:") for row in results)
        with store.conn:
            store.conn.execute("UPDATE runs SET status=? WHERE run_key=?", ("complete" if complete else "incomplete", run_key))
        summary.update(run_key=run_key, results=results, attempted_calls=sum(row["requested"] for row in attempts), unresolved_attempts=unresolved, stopped=stopped, complete=complete, labels_sha256=file_hash(labels_path), human_labels_confirmed=labels.human_confirmed, source_unchanged=True)
    # Summary is a replaceable view; immutable attempts remain in the SQLite ledger.
    output = corpus_path.parent / f"evaluation-{run_key}.json"
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {key: value for key, value in summary.items() if key != "results"} | {"report": str(output)}


def main() -> None:
    """Expose preparation and evaluation as explicit, bounded commands with file logging."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("selected", type=Path)
    prep.add_argument("--execute", action="store_true")
    prep.add_argument("--dns-mode", choices=["system", "https"], default="system")
    prep.add_argument("--per-event", type=int, choices=[1, 2], default=1)
    run = commands.add_parser("evaluate")
    run.add_argument("corpus", type=Path)
    run.add_argument("--provider", choices=["gemini", "deepseek"], required=True)
    run.add_argument("--max-calls", type=int, default=60)
    run.add_argument("--execute", action="store_true")
    run.add_argument("--split", choices=["development", "holdout"], default="development")
    add = commands.add_parser("augment")
    add.add_argument("corpus", type=Path)
    add.add_argument("selected", type=Path)
    add.add_argument("additions", type=Path)
    add.add_argument("--execute", action="store_true")
    add.add_argument("--dns-mode", choices=["system", "https"], default="system")
    args = parser.parse_args()
    if args.execute:
        directory = private_path(args.selected if args.command == "prepare" else args.corpus, existing=True).parent
        logging.basicConfig(level=logging.INFO, filename=directory / "pilot.log", encoding="utf-8", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.command == "prepare":
        result = prepare(args.selected, execute=args.execute, dns_mode=args.dns_mode, per_event=args.per_event)
    elif args.command == "augment":
        result = augment(args.corpus, args.selected, args.additions, execute=args.execute, dns_mode=args.dns_mode)
    else:
        result = evaluate(args.corpus, provider=args.provider, max_calls=args.max_calls, execute=args.execute, split=args.split)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
