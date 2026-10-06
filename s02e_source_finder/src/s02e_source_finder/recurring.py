"""Resumable reference discovery after merge and before normal s03 export.

Search and validation retain the previously calibrated evidence gates. Pending
work is durable; an empty or exhausted authorization never silently buys calls.
"""
import argparse
import json
import logging
import sqlite3
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from urllib.parse import urlsplit

import requests
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from . import grounding, structured, structured_client
from .contracts import EventContext, Page
from .enrichment_report import assess_capacity_row
from .enrichment_trial import judge_case
from .fetch import PublicFetcher
from .pilot import digest
from .publishers import PublisherRegistry, verify_publisher
from .recurring_budget import RecurringBudget
from .sample import ROOT, MODULE, file_hash
from .source_loop import normalized_url, resolve_citation, service_blocked
from .source_admission import url_identity
from .structured import TargetCard

LOG = logging.getLogger(__name__)
DEFAULT_CONFIG = ROOT / "supporting_docs/source_finder_config.json"
DEFAULT_DB = ROOT / "s02a_duplicate_merger/data/db/bri_re_database.db"
SEARCH_POLICY = "bounded-source-search-v1"


class RetryProgress(BaseModel):
    """One old-source pass and two fresh searches survive restarts without replay."""
    model_config = ConfigDict(extra="forbid")
    version: str = Field(default=SEARCH_POLICY, pattern="^bounded-source-search-v1$")
    fingerprint: str
    phase: int = Field(default=0, ge=0, le=3)
    index: int = Field(default=0, ge=0, le=4)
    old_urls: list[str] = Field(max_length=4)
    excluded_urls: list[str]
    round_urls: list[str] | None = Field(default=None, max_length=4)
    rows: list[dict] = Field(default_factory=list, max_length=12)
    capacity_candidates: list[dict] = Field(default_factory=list)
    capacity_audit: list[dict] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)


def prepare_bounded_retries(queue: Path, candidates: list[dict], *, refresh_days=80):
    """Skip valid successes, reopen old no-source completions once, retain terminal outcomes."""
    by_event = {}
    for row in candidates:
        by_event.setdefault(row["event_id"], []).append(row)
    now = datetime.now(timezone.utc)
    with closing(sqlite3.connect(queue)) as conn:
        conn.row_factory = sqlite3.Row
        # Preserve the pre-policy queue without overwriting an earlier backup.
        backup = queue.with_name(f"before-{SEARCH_POLICY}.sqlite")
        if not backup.exists():
            with closing(sqlite3.connect(backup)) as saved:
                conn.backup(saved)
                if saved.execute("SELECT count(*) FROM jobs").fetchone()[0] != conn.execute("SELECT count(*) FROM jobs").fetchone()[0]:
                    raise ValueError("bounded_search_queue_backup_count_mismatch")
            backup.with_suffix(".manifest.json").write_text(json.dumps({"source": str(queue), "sha256": file_hash(backup),
                                                                        "created_at": now.isoformat()}), encoding="utf-8")
        jobs, updates = [], []
        for row in conn.execute("SELECT * FROM jobs WHERE active=1 ORDER BY attempts,enqueued_at DESC,fingerprint,event_id"):
            job = dict(row)
            prior = json.loads(job["result_json"]) if job["result_json"] else {}
            target = json.loads(job["target_json"])
            context = EventContext.model_validate(target["context"])
            if prior.get("search_policy") == SEARCH_POLICY and prior.get("outcome") in {"qualified", "no_reference_found"}:
                continue
            previous_urls, seeds, accepted = [], [], False
            for candidate in by_event.get(job["event_id"], []):
                projected = json.loads(candidate["candidate_json"])
                if candidate.get("candidate_url"):
                    previous_urls.append(candidate["candidate_url"])
                    seeds.append({"url": candidate["candidate_url"]})
                metadata = json.loads(candidate["result_json"]) if candidate.get("result_json") else {}
                if metadata.get("url"):
                    previous_urls.append(metadata["url"])
                ref = projected.get("reference")
                if ref and candidate.get("context_sha256") == context.fingerprint():
                    expiry = datetime.fromisoformat(ref["valid_until"])
                    checked = datetime.fromisoformat(ref["checked_at"])
                    accepted |= (expiry > now and checked > now - timedelta(days=refresh_days)
                                 and projected.get("decision") in {"event_reference", "project_reference"})
            if accepted:
                metadata = json.dumps({"search_policy": SEARCH_POLICY, "outcome": "qualified"})
                updates.append(("completed", job["checked_at"] or now.isoformat(), metadata, job["event_id"]))
                continue
            # Old 'completed' can mean no usable source. Give it the bounded retry,
            # including events whose old failure was substantive rather than HTTP.
            if job["status"] == "completed":
                updates.append(("pending", job["checked_at"], job["result_json"], job["event_id"]))
            if job["attempts"]:
                seen, ordered = set(), []
                for seed in seeds + target.get("candidates", []):
                    try:
                        key = url_identity(seed["url"])
                    except ValueError:
                        # Internal or otherwise prohibited legacy links are not
                        # retryable public candidates; isolate them like fetch does.
                        continue
                    if key not in seen:
                        seen.add(key)
                        ordered.append(seed)
                target["candidates"] = ordered
                target["previous_urls"] = previous_urls
                job["target_json"] = json.dumps(target, ensure_ascii=False)
            jobs.append(job)
        conn.executemany("UPDATE jobs SET status=?,checked_at=?,result_json=? WHERE event_id=?", updates)
        conn.commit()
    return jobs


class RecurringConfig(BaseModel):
    """Paths are relative to this config; authorization covers a cumulative ledger."""
    model_config = ConfigDict(extra="forbid")
    version: str = Field(default="recurring-references-v1", pattern="^recurring-references-v1$")
    source_db: Path
    raw_db: Path
    state_dir: Path
    publisher_registry: Path
    legacy_snapshot: Path | None = None
    enabled: bool = True
    budget_usd: float | None = Field(default=0, ge=0, allow_inf_nan=False)
    max_search_calls: int | None = Field(default=0, ge=0)
    max_model_calls: int | None = Field(default=0, ge=0)
    max_targets_per_run: int = Field(default=25, ge=1, le=5000)
    max_candidates_per_event: int = Field(default=4, ge=1, le=4)
    search_rounds: int = Field(default=2, ge=1, le=2)
    workers: int = Field(default=4, ge=1)
    refresh_days: int = Field(default=80, ge=1, le=89)
    dns_mode: str = Field(default="system", pattern="^(system|https)$")
    local_proxy: str | None = None
    authorization_note: str = "Budget pending; do not call providers."
    source_review_enabled: bool = False  # Existing isolated trial configs keep their frozen policy.
    source_review_min_urls: int = Field(default=6, ge=6)


def read_config(path: Path, main_db: Path, raw_db: Path):
    cfg = RecurringConfig.model_validate_json(path.read_text(encoding="utf-8"))
    for field in ("source_db", "raw_db", "state_dir", "publisher_registry", "legacy_snapshot"):
        value = getattr(cfg, field)
        if value is not None:
            setattr(cfg, field, (path.parent / value).resolve())
    if cfg.source_db != main_db.resolve() or cfg.raw_db != raw_db.resolve():
        raise ValueError("source_finder_config_database_mismatch")
    if cfg.state_dir in {cfg.source_db, cfg.raw_db} or cfg.state_dir.is_relative_to(ROOT / "s04_web_visualization/public"):
        raise ValueError("source_finder_state_path_invalid")
    if cfg.local_proxy is not None:
        proxy = urlsplit(cfg.local_proxy)
        if proxy.scheme != "http" or proxy.hostname not in {"127.0.0.1", "localhost", "::1"} or proxy.username or proxy.password or not proxy.port:
            raise ValueError("source_finder_proxy_must_be_explicit_local_loopback")
    return cfg


def sync_queue(path: Path, targets: list[dict], rule_hash: str, refresh_days: int, *, source_policy_upgrade=False):
    """Keep changed and unfinished events even if their upstream merge already committed."""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=refresh_days)).isoformat()
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
            event_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, target_json TEXT NOT NULL,
            status TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 1,
            active INTEGER NOT NULL DEFAULT 1, checked_at TEXT, result_json TEXT,
            attempts INTEGER NOT NULL DEFAULT 0, enqueued_at TEXT NOT NULL DEFAULT '')""")
        columns = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
        source_only_migration = source_policy_upgrade and "source_policy" not in columns
        if "attempts" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
        if "enqueued_at" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN enqueued_at TEXT NOT NULL DEFAULT ''")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE jobs SET active=0")
        for target in targets:
            fingerprint = digest([EventContext.model_validate(target["context"]).fingerprint(), rule_hash])
            old = conn.execute("SELECT * FROM jobs WHERE event_id=?", (target["event_id"],)).fetchone()
            encoded = json.dumps(target, ensure_ascii=False)
            if old is not None and source_only_migration:
                previous = json.loads(old["target_json"])
                # This one-time source-policy upgrade does not change extraction,
                # project/event evidence or search prompts. Preserve paid generations.
                if EventContext.model_validate(previous["context"]).fingerprint() == EventContext.model_validate(target["context"]).fingerprint() and previous.get("card") == target.get("card"):
                    conn.execute("UPDATE jobs SET fingerprint=? WHERE event_id=?", (fingerprint, target["event_id"]))
                    old = conn.execute("SELECT * FROM jobs WHERE event_id=?", (target["event_id"],)).fetchone()
            if old is None:
                conn.execute("INSERT INTO jobs(event_id,fingerprint,target_json,status,enqueued_at) VALUES(?,?,?,'pending',?)",
                             (target["event_id"], fingerprint, encoded, now.isoformat()))
            elif old["fingerprint"] != fingerprint or old["checked_at"] and old["checked_at"] < cutoff:
                conn.execute("UPDATE jobs SET fingerprint=?,target_json=?,status='pending',generation=generation+1,active=1,checked_at=NULL,result_json=NULL,attempts=0,enqueued_at=? WHERE event_id=?",
                             (fingerprint, encoded, now.isoformat(), target["event_id"]))
            else:
                conn.execute("UPDATE jobs SET active=1 WHERE event_id=?", (target["event_id"],))
        conn.commit()
        # Give the latest increment its first pass ahead of the historical backlog;
        # failed retries still yield to events that have never been attempted.
        return [dict(row) for row in conn.execute("SELECT * FROM jobs WHERE active=1 AND status!='completed' ORDER BY attempts,enqueued_at DESC,fingerprint,event_id")]


def search_once(target, card, generation, round_number, budget, *, fresh=False, excluded_urls=()):
    context = EventContext.model_validate(target["context"])
    request = grounding.search_request(context, card, round_number, candidate_limit=4 if fresh else 3,
                                       excluded_urls=excluded_urls)
    # Capacity is supplementary to this same source search, never a separate paid hunt.
    if target["pool"] == "capacity_missing":
        request["contents"][0]["parts"][0]["text"] += " 如报道恰好给出本期电站发电装机容量，请保留原数值和单位；不要推算或扩大搜索。"
    spec = {"provider": "gemini", "model": grounding.MODEL, "generation": generation, "request": request}
    if fresh:
        spec["search_policy"] = SEARCH_POLICY
    cached = budget.cached(spec)
    if cached is not None:
        return cached
    if not budget.reserve(spec, "search", "search"):
        return budget.cached(spec)
    try:
        result = grounding.search(request)
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        result = {"error": str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__}
    budget.finish(spec, result, failed=bool(result.get("error")))
    return result


class AttemptBudget:
    """Reuse successes, retry a known failure once per later run, never replay unknown requests."""
    def __init__(self, budget, attempt):
        self.budget, self.attempt = budget, attempt

    def _spec(self, spec):
        previous = self.budget.cached(spec)
        if self.attempt and previous and previous.get("error") and not previous.get("request_outcome_unknown") and previous["error"] != "reserved_request_unresolved":
            for number in range(1, self.attempt + 1):
                tried = spec | {"retry_attempt": number}
                cached = self.budget.cached(tried)
                if cached is not None and (not cached.get("error") or cached.get("request_outcome_unknown") or cached["error"] == "reserved_request_unresolved"):
                    return tried
            return spec | {"retry_attempt": self.attempt}
        return spec

    def cached(self, spec):
        return self.budget.cached(self._spec(spec))

    def reserve(self, spec, bucket, provider):
        return self.budget.reserve(self._spec(spec), bucket, provider)

    def finish(self, spec, result, *, failed=False):
        return self.budget.finish(self._spec(spec), result, failed=failed)


def process_target(target, generation, cfg, registry, budget, fetcher, *, attempt=0, stop_event=None, admission=None,
                   seeds_only=False, bounded_retry=False):
    """Isolate every candidate and stop only the corresponding failed service/budget."""
    card = TargetCard.model_validate(target["card"])
    rows, capacities, diagnostics, visited = [], [], [], set()
    capacity_audit = []
    deferred = blocked = False
    budget = AttemptBudget(budget, attempt)
    progress = None
    identity = url_identity if bounded_retry else normalized_url

    def safe_identity(url):
        try:
            return identity(url)
        except ValueError:
            # Legacy candidate lists can contain internal URLs that fetching rejects.
            return None

    def public_urls(urls):
        seen, result = set(), []
        for url in urls:
            key = safe_identity(url)
            if key is not None and key not in seen:
                seen.add(key)
                result.append(url)
        return result

    if bounded_retry:
        progress_path = cfg.state_dir / "results" / f"{target['event_id']}-{generation}-{SEARCH_POLICY}.progress.json"
        fingerprint = EventContext.model_validate(target["context"]).fingerprint()
        if progress_path.exists():
            progress = RetryProgress.model_validate_json(progress_path.read_text(encoding="utf-8"))
            if progress.fingerprint != fingerprint:
                raise ValueError(f"bounded_search_progress_context_mismatch:{progress_path}")
            progress.old_urls = public_urls(progress.old_urls)
            progress.excluded_urls = public_urls(progress.excluded_urls)
            if progress.round_urls is not None:
                progress.round_urls = public_urls(progress.round_urls)
        else:
            old_urls = [seed["url"] for seed in target.get("candidates", [])[:cfg.max_candidates_per_event]]
            progress = RetryProgress(fingerprint=fingerprint, old_urls=old_urls,
                                     excluded_urls=public_urls(target.get("previous_urls", []) +
                                                               [s["url"] for s in target.get("candidates", [])]))
        rows, capacities = list(progress.rows), list(progress.capacity_candidates)
        capacity_audit, diagnostics = list(progress.capacity_audit), list(progress.diagnostics)
        visited = {key for row in rows for url in (row.get("candidate_url"), row.get("url"))
                   if url and (key := safe_identity(url)) is not None}

    def save_progress():
        progress.rows, progress.capacity_candidates = rows, capacities
        progress.capacity_audit, progress.diagnostics = capacity_audit, diagnostics
        progress_path.parent.mkdir(parents=True, exist_ok=True)
        staged = progress_path.with_suffix(".tmp")
        staged.write_text(progress.model_dump_json(indent=2), encoding="utf-8")
        staged.replace(progress_path)

    def evaluate(url, origin):
        nonlocal deferred, blocked
        if stop_event is not None and stop_event.is_set():
            deferred = True
            return
        if identity(url) in visited or (not bounded_retry and len(visited) >= cfg.max_candidates_per_event):
            return
        visited.add(identity(url))
        host = (urlsplit(url).hostname or "").lower()
        if not cfg.source_review_enabled and not any(host in record.hosts for record in registry.records):
            rows.append({"case_id": digest([target["event_id"], url])[:24], "project_id": target["project_id"],
                         "event_id": target["event_id"], "candidate_url": url, "url": url, "origin": origin,
                         "decision": "pending", "reason": "publisher_not_registered", "external_reference": None})
            return
        cache = cfg.state_dir / "pages" / (digest([target["event_id"], generation, url]) + ".json")
        if cache.exists():
            cached_page = json.loads(cache.read_text(encoding="utf-8"))
            page = Page.model_validate(cached_page)
            if page.problem and attempt or cfg.source_review_enabled and "identity_text" not in cached_page:
                page = fetcher.fetch(url)
                cache.write_text(page.model_dump_json(indent=2), encoding="utf-8")
        else:
            page = fetcher.fetch(url)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(page.model_dump_json(indent=2), encoding="utf-8")
        case = {"case_id": digest([target["event_id"], page.final_url, page.text_hash])[:24],
                "project_id": target["project_id"], "event_id": target["event_id"], "pool": target["pool"],
                "context": target["context"], "card": target["card"], "page": page.model_dump()}
        candidate_registry = registry
        source_review = None
        final_key, requested_key = safe_identity(page.final_url), safe_identity(url)
        duplicate_redirect = (bounded_retry and progress.phase > 0 and final_key is not None
                              and requested_key is not None and final_key != requested_key
                              and final_key in {key for value in progress.excluded_urls
                                                if (key := safe_identity(value)) is not None})
        if not page.problem and not duplicate_redirect and admission is not None:
            candidate_registry, source_review = admission.admit(page, registry, budget)
        source = verify_publisher(page, candidate_registry)
        if duplicate_redirect:
            row = {"case_id": case["case_id"], "decision": "pending", "reason": "previously_tried_url_redirect",
                   "external_reference": None}
        elif page.problem:
            row = {"case_id": case["case_id"], "decision": "pending", "reason": page.problem,
                   "processing_status": "failed", "external_reference": None}
        elif source.get("problem"):
            reason = source_review.get("reason") if source_review else source["problem"]
            row = {"case_id": case["case_id"], "decision": "pending", "reason": reason, "external_reference": None}
            if source_review and source_review["status"] == "failed":
                row["processing_status"] = "failed"
        else:
            row = judge_case(case, "deepseek", candidate_registry, budget, "new_extract", execute=True, blocked=blocked)
        if source_review:
            row["source_review"] = source_review
        row.update(project_id=target["project_id"], event_id=target["event_id"], candidate_url=url, url=page.final_url, origin=origin)
        rows.append(row)
        deferred |= row.get("reason") == "trial_budget_exhausted"
        blocked |= service_blocked(row.get("reason"))
        if (deferred or blocked) and stop_event is not None:
            stop_event.set()
        if target["pool"] == "capacity_missing" and target["context"]["event"].get("capacity_mw_raw") not in (None, ""):
            capacity_audit.append({"case_id": case["case_id"], "capacity_status": "pending", "reasons": ["existing_raw_capacity_preserved"]})
        elif target["pool"] == "capacity_missing" and row.get("extraction"):
            result = assess_capacity_row(target, card, case, row, candidate_registry, target["context"]["event"])
            capacity_audit.append(result)
            capacities.extend(result["candidates"])

    if bounded_retry:
        while progress.phase < 3 and not any(row.get("external_reference") for row in rows):
            if stop_event is not None and stop_event.is_set():
                deferred = True
                break
            if progress.phase and progress.round_urls is None:
                try:
                    search = search_once(target, card, generation, progress.phase, budget, fresh=True,
                                         excluded_urls=progress.excluded_urls)
                except ValueError as exc:
                    if str(exc) != "trial_budget_exhausted":
                        raise
                    deferred = True
                    break
                if search.get("error"):
                    blocked = service_blocked(search["error"])
                    diagnostics.append(search["error"])
                    if blocked:
                        break
                    progress.round_urls = []
                else:
                    excluded = {key for url in progress.excluded_urls
                                if (key := safe_identity(url)) is not None}
                    urls = []
                    for chunk in search.get("grounding", {}).get("groundingChunks", []):
                        web = chunk.get("web")
                        if not web or not web.get("uri") or len(urls) >= 4:
                            continue
                        try:
                            url = resolve_citation(web["uri"], fetcher)
                        except (requests.RequestException, ValueError, OSError) as exc:
                            diagnostics.append(type(exc).__name__ + ":candidate_resolve_failed")
                            continue
                        key = identity(url)
                        if key not in excluded:
                            urls.append(url)
                            excluded.add(key)
                    progress.round_urls = urls
                    progress.excluded_urls = sorted(set(progress.excluded_urls + urls))
                save_progress()
            urls = progress.old_urls if progress.phase == 0 else progress.round_urls
            if progress.index >= len(urls):
                progress.phase += 1
                progress.index, progress.round_urls = 0, None
                save_progress()
                continue
            url = urls[progress.index]
            try:
                evaluate(url, "retry_old" if progress.phase == 0 else f"fresh_round_{progress.phase}")
            except (requests.RequestException, ValueError, OSError) as exc:
                diagnostics.append(type(exc).__name__ + ":candidate_fetch_or_validation_failed")
            if deferred or blocked:
                break  # Leave this cursor unchanged; provider failures do not exhaust sources.
            progress.excluded_urls = sorted(set(progress.excluded_urls + [r["url"] for r in rows if r.get("url")]))
            progress.index += 1
            save_progress()
        if (deferred or blocked) and stop_event is not None:
            stop_event.set()

    for seed in ([] if bounded_retry else target.get("candidates", [])[:cfg.max_candidates_per_event]):
        try:
            evaluate(seed["url"], "legacy")
        except (requests.RequestException, ValueError, OSError) as exc:
            diagnostics.append(type(exc).__name__ + ":legacy_candidate_failed")
        if deferred or blocked or any(row.get("external_reference") for row in rows):
            break
    for round_number in range(1, (0 if seeds_only or bounded_retry else cfg.search_rounds) + 1):
        if stop_event is not None and stop_event.is_set():
            deferred = True
        if deferred or blocked or len(visited) >= cfg.max_candidates_per_event or any(row.get("external_reference") for row in rows):
            break
        try:
            search = search_once(target, card, generation, round_number, budget)
        except ValueError as exc:
            if str(exc) != "trial_budget_exhausted":
                raise
            deferred = True
            if stop_event is not None:
                stop_event.set()
            break
        if search.get("error"):
            diagnostics.append(search["error"])
            blocked |= service_blocked(search["error"])
            if blocked and stop_event is not None:
                stop_event.set()
            continue
        for chunk in search.get("grounding", {}).get("groundingChunks", []):
            web = chunk.get("web")
            if not web or not web.get("uri") or len(visited) >= cfg.max_candidates_per_event:
                continue
            try:
                url = resolve_citation(web["uri"], fetcher)
                evaluate(url, f"google_round_{round_number}")
            except (requests.RequestException, ValueError, OSError) as exc:
                diagnostics.append(type(exc).__name__ + ":candidate_fetch_or_validation_failed")
            if deferred or blocked or any(row.get("external_reference") for row in rows):
                break
    return {"target": target, "rows": rows, "capacity_candidates": capacities, "capacity_audit": capacity_audit,
            "diagnostics": diagnostics, "deferred": deferred, "service_blocked": blocked,
            "retry_exhausted": bool(progress is not None and progress.phase == 3 and not any(r.get("external_reference") for r in rows)),
            "technical_failure": bool(diagnostics) or any(r.get("processing_status") == "failed" and r.get("reason") not in
                {"http_404", "http_410", "not_chinese", "unsafe_url", "goalfore_not_external", "redirect_to_homepage"} for r in rows)}


def _run_incremental(main_db: Path, raw_db: Path, config_path: Path | None = None, *, execute: bool = True):
    """Queue the complete authorized population, then process bounded pending work."""
    from .recurring_targets import build_targets, summarize_targets
    from .recurring_store import persist_results
    main_db, raw_db = Path(main_db).resolve(strict=True), Path(raw_db).resolve(strict=True)
    if config_path is None:
        if main_db != DEFAULT_DB.resolve() or not DEFAULT_CONFIG.exists():
            return {"status": "awaiting_budget", "reason": "source_finder_configuration_required", "reference_snapshot": None}
        config_path = DEFAULT_CONFIG
    cfg = read_config(Path(config_path).resolve(strict=True), main_db, raw_db)
    if not cfg.enabled:
        return {"status": "completed", "reason": "disabled", "reference_snapshot": None}
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    registry = PublisherRegistry.model_validate_json(cfg.publisher_registry.read_text(encoding="utf-8"))
    targets = build_targets(main_db, raw_db)
    rule_hash = digest(["recurring-reference-v1", file_hash(Path(structured.__file__)),
                        file_hash(Path(structured_client.__file__)), file_hash(cfg.publisher_registry)])
    queue = cfg.state_dir / "queue.sqlite"
    jobs = sync_queue(queue, targets, rule_hash, cfg.refresh_days, source_policy_upgrade=cfg.source_review_enabled)
    admission = None
    from .source_admission import prior_candidates
    source_candidates = prior_candidates(main_db)
    if cfg.source_review_enabled:
        from .source_admission import SourceAdmission, VERSION as source_version, prepare_source_retries
        jobs = prepare_source_retries(queue, source_candidates)
    jobs = prepare_bounded_retries(queue, source_candidates, refresh_days=cfg.refresh_days)
    LOG.info("Recurring scope source=%s targets=%s pending=%s budget=%s", main_db, len(targets), len(jobs), cfg.budget_usd)
    summary = {"targets": len(targets), "projects": len({t["project_id"] for t in targets}),
               "pools": dict(Counter(t["pool"] for t in targets)), "pending": len(jobs), "processed": 0,
               "reference_snapshot": None, "external_calls": 0}
    summary["population"] = summarize_targets(targets)
    summary["search_policy"] = SEARCH_POLICY
    summary["request_bounds"] = {"search": sum(2 if job["attempts"] else cfg.search_rounds for job in jobs),
                                 "extraction": sum(cfg.max_candidates_per_event + (8 if job["attempts"] else 0) for job in jobs)}
    snapshot = cfg.state_dir / "external-references.json"
    if snapshot.exists():
        summary["reference_snapshot"] = str(snapshot)
    if not execute or cfg.budget_usd == 0 or cfg.max_search_calls == 0 or cfg.max_model_calls == 0:
        summary["status"] = "awaiting_budget" if jobs else "completed"
        (cfg.state_dir / "latest-report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
    load_dotenv(ROOT / ".env", override=False)
    budget = RecurringBudget(cfg.state_dir / "calls.sqlite", ceiling=cfg.budget_usd,
                             search_limit=cfg.max_search_calls, extraction_limit=cfg.max_model_calls)
    if cfg.source_review_enabled:
        admission = SourceAdmission(cfg.state_dir / "source-admission.sqlite", min_urls=cfg.source_review_min_urls)
        admission.observe([row["candidate_url"] for row in source_candidates if row["candidate_url"]])
    start_calls = sum(budget.summary()["requests"].values())
    results, completed, attempted, outcomes, stop = [], [], [], [], False
    stop_event = Event()
    def work(job):
        if stop_event.is_set():
            return job, None
        fetcher = PublicFetcher(dns_mode=cfg.dns_mode, allow_english=True)
        if cfg.local_proxy:
            fetcher.session.proxies.update({"http": cfg.local_proxy, "https": cfg.local_proxy})
        try:
            target = json.loads(job["target_json"])
            policy_suffix = "-" + SEARCH_POLICY + ("-" + source_version if cfg.source_review_enabled else "")
            result_path = cfg.state_dir / "results" / f"{job['event_id']}-{job['generation']}-{job['attempts']}{policy_suffix}.json"
            if result_path.exists():
                result = json.loads(result_path.read_text(encoding="utf-8"))
            else:
                result = process_target(target, job["generation"], cfg, registry, budget, fetcher,
                                        attempt=job["attempts"], stop_event=stop_event, admission=admission,
                                        bounded_retry=bool(job["attempts"]))
                # Every attempt is an audit record. Queue attempt numbers advance
                # only after persistence; requests have an independent paid ledger.
                result_path.parent.mkdir(parents=True, exist_ok=True)
                result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            return job, result
        finally:
            fetcher.close()
    with ThreadPoolExecutor(max_workers=cfg.workers) as pool:
        futures = [pool.submit(work, job) for job in jobs[:cfg.max_targets_per_run]]
        for future in as_completed(futures):
            job, result = future.result()
            if result is None:
                stop = True
                continue
            results.append(result)
            attempted.append(job["event_id"])
            qualified = any(row.get("external_reference") for row in result["rows"])
            exhausted = result.get("retry_exhausted", False) and not result["deferred"] and not result["service_blocked"]
            outcome = "qualified" if qualified else "no_reference_found" if exhausted else "retry_pending"
            outcomes.append((json.dumps({"search_policy": SEARCH_POLICY, "outcome": outcome}), job["event_id"]))
            if qualified or exhausted:
                completed.append(job["event_id"])
            print(json.dumps({"source_finder_event": job["event_id"], "references": sum(bool(r.get("external_reference")) for r in result["rows"]),
                              "deferred": result["deferred"]}), flush=True)
            if result["deferred"] or result["service_blocked"]:
                stop = True
    if results:
        stored = persist_results(main_db, results, backup_dir=cfg.state_dir / "backups", snapshot_path=snapshot,
                                 legacy_snapshot=cfg.legacy_snapshot)
        summary["persistence"] = stored
        summary["reference_snapshot"] = str(snapshot)
        with closing(sqlite3.connect(queue)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany("UPDATE jobs SET attempts=attempts+1 WHERE event_id=?", [(event_id,) for event_id in attempted])
            conn.executemany("UPDATE jobs SET result_json=? WHERE event_id=?", outcomes)
            conn.executemany("UPDATE jobs SET status='completed',checked_at=? WHERE event_id=?",
                             [(datetime.now(timezone.utc).isoformat(), event_id) for event_id in completed])
            if cfg.source_review_enabled:
                conn.executemany("UPDATE jobs SET source_policy=? WHERE event_id=?",
                                 [(source_version, r["target"]["event_id"]) for r in results if not r["deferred"]])
            conn.commit()
    summary.update(processed=len(completed), pending=len(jobs)-len(completed), budget=budget.summary(),
                   found_reference=sum(json.loads(value)["outcome"] == "qualified" for value, _ in outcomes),
                   exhausted=sum(json.loads(value)["outcome"] == "no_reference_found" for value, _ in outcomes),
                   status="partial" if stop or len(completed) < len(jobs) else "completed",
                   external_calls=sum(budget.summary()["requests"].values())-start_calls)
    summary["stop_reason"] = ("service_blocked" if any(r["service_blocked"] for r in results) else
                              "budget_exhausted" if any(r["deferred"] for r in results) else
                              "batch_limit_or_pending_failures" if summary["pending"] else "completed")
    (cfg.state_dir / "latest-report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.info("Recurring finished status=%s processed=%s pending=%s calls=%s", summary["status"], len(completed), summary["pending"], summary["external_calls"])
    return summary


def run_incremental(main_db: Path, raw_db: Path, config_path: Path | None = None, *, execute: bool = True):
    """Public stage entrypoint; keep a file log for both CLI and pipeline callers."""
    path = Path(config_path) if config_path else DEFAULT_CONFIG if Path(main_db).resolve() == DEFAULT_DB.resolve() else None
    if path is None or not path.exists():
        return _run_incremental(main_db, raw_db, config_path, execute=execute)
    cfg = read_config(path.resolve(), Path(main_db), Path(raw_db))
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(cfg.state_dir / "recurring.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    module_logger = logging.getLogger("s02e_source_finder")
    old_level = module_logger.level
    module_logger.setLevel(logging.INFO)
    module_logger.addHandler(handler)
    try:
        return _run_incremental(main_db, raw_db, path, execute=execute)
    finally:
        module_logger.removeHandler(handler)
        module_logger.setLevel(old_level)
        handler.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    path = args.config.resolve(strict=True)
    raw = RecurringConfig.model_validate_json(path.read_text(encoding="utf-8"))
    cfg = read_config(path, path.parent / raw.source_db, path.parent / raw.raw_db)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    print(json.dumps(run_incremental(cfg.source_db, cfg.raw_db, path, execute=args.execute), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
