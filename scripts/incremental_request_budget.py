"""Durable SDK-attempt accounting for normal incremental workflow runs.

This module supplies no extraction, merge or database-writing algorithm. Callers
bind each SDK request to frozen raw IDs and keep the same ledger across resumes.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager, ExitStack
import functools
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from unittest.mock import patch
from urllib.parse import urlparse


class BudgetStop(BaseException):
    """Stop the batch even through optional-field Exception fallback handlers."""


def write_json(path: Path, value) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def visible_tokens(usage: dict | None) -> int:
    usage = usage or {}
    total = usage.get("total_tokens", usage.get("total_token_count"))
    if isinstance(total, int):
        return total
    return sum(usage.get(name, 0) or 0 for name in (
        "prompt_tokens", "completion_tokens", "prompt_token_count",
        "candidates_token_count", "thoughts_token_count",
    ))


class RequestLedger:
    """Reserve before sending; failures and interrupted attempts consume budget.

    Concurrent or pairwise requests charge every contributing article. Resume
    cannot reset the caps. Exact successful SDK payloads may be reused separately;
    a reuse is recorded, and does not masquerade as a new billed request.
    """

    def __init__(self, directory: Path, *, raw_ids: list[int], limits: dict,
                 per_article: int, token_stop: int, allowed: set[tuple[str, str]]):
        if not raw_ids or any(type(i) is not int or i <= 0 for i in raw_ids):
            raise ValueError("raw_ids must be positive integers")
        if not limits or any(type(v) is not int or v <= 0 for v in limits.values()):
            raise ValueError("category limits must be positive integers")
        if type(per_article) is not int or per_article <= 0 or type(token_stop) is not int or token_stop <= 0:
            raise ValueError("per-article and token caps must be positive integers")
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "responses").mkdir(exist_ok=True)
        self.ids, self.limits = set(raw_ids), dict(limits)
        self.per_article, self.token_stop, self.allowed = per_article, token_stop, set(allowed)
        self.lock = threading.Lock()
        self.path = self.directory / "calls.json"
        self.reuse_path = self.directory / "cache-reuse.json"
        contract = {"raw_ids": sorted(self.ids), "limits": limits, "per_article": per_article,
                    "token_stop": token_stop, "allowed": sorted(allowed)}
        contract_path = self.directory / "budget-contract.json"
        if contract_path.exists():
            if json.loads(contract_path.read_text(encoding="utf-8")) != json.loads(json.dumps(contract)):
                raise ValueError("Existing ledger has a different budget; refuse reset or enlargement")
        else:
            if self.path.exists() or self.reuse_path.exists():
                raise ValueError("Ledger without its frozen budget contract")
            write_json(contract_path, contract)
        self.calls = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []
        self.reuses = json.loads(self.reuse_path.read_text(encoding="utf-8")) if self.reuse_path.exists() else []
        if not self.path.exists():
            write_json(self.path, self.calls)
        if not self.reuse_path.exists():
            write_json(self.reuse_path, self.reuses)
        self._served_cache = set()

    def _key(self, category, ids, provider, model, stage, request):
        value = [category, ids, provider, model, stage, request]
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                        separators=(",", ":")).encode()).hexdigest()

    def invoke(self, category: str, raw_ids: list[int], provider: str, model: str,
               request: dict, invoke, *, stage: str, restore=None, allow_cache=True):
        ids = sorted(set(raw_ids))
        if not ids or set(ids) - self.ids or category not in self.limits or (provider, model) not in self.allowed:
            raise BudgetStop("Unattributed or out-of-scope model request")
        key = self._key(category, ids, provider, model, stage, request)
        with self.lock:
            # Only an SDK payload saved by this independent batch can be restored.
            matches = [c for c in self.calls if c.get("cache_key") == key and c["status"] == "completed"]
            if allow_cache and restore and matches and key not in self._served_cache:
                hit = matches[-1]
                response_path = self.directory / hit["response_path"]
                if hashlib.sha256(response_path.read_bytes()).hexdigest() != hit["response_sha256"]:
                    raise BudgetStop("Saved response hash changed")
                self._served_cache.add(key)
                self.reuses.append({"stage": stage, "raw_ids": ids, "original_request": hit["number"],
                                    "at": time.time(), "cache_key": key})
                write_json(self.reuse_path, self.reuses)
                return restore(json.loads(response_path.read_text(encoding="utf-8")))
            counts = Counter(c["category"] for c in self.calls)
            if len(self.calls) >= sum(self.limits.values()) or counts[category] >= self.limits[category]:
                raise BudgetStop("Cumulative request limit exhausted")
            if any(sum(i in c["raw_ids"] for c in self.calls) >= self.per_article for i in ids):
                raise BudgetStop("Cumulative per-article request limit exhausted")
            if sum(visible_tokens(c.get("usage")) for c in self.calls) >= self.token_stop:
                raise BudgetStop("Visible token stopping threshold reached")
            number = len(self.calls) + 1
            request_path = self.directory / "responses" / f"request-{number:04d}.json"
            write_json(request_path, request)
            record = {"number": number, "category": category, "stage": stage, "raw_ids": ids,
                      "provider": provider, "model": model, "status": "started", "started": time.time(),
                      "cache_key": key, "request_path": str(request_path.relative_to(self.directory)),
                      "request_sha256": hashlib.sha256(request_path.read_bytes()).hexdigest()}
            self.calls.append(record)
            write_json(self.path, self.calls)
        try:
            response = invoke()
            payload = response.model_dump(mode="json")
            response_path = self.directory / "responses" / f"response-{number:04d}.json"
            write_json(response_path, payload)
            usage = response.usage_metadata if provider == "gemini" else response.usage
            with self.lock:
                record.update(status="completed", usage=usage.model_dump(mode="json") if usage else None,
                              response_path=str(response_path.relative_to(self.directory)),
                              response_sha256=hashlib.sha256(response_path.read_bytes()).hexdigest())
            return response
        except BaseException as exc:
            # Audit boundary only: preserve every failure and propagate unchanged.
            with self.lock:
                record.update(status="failed", error_type=type(exc).__name__)
            raise
        finally:
            with self.lock:
                record["finished"] = time.time()
                write_json(self.path, self.calls)

    def usage_report(self) -> dict:
        return {"requests": len(self.calls), "by_category": dict(Counter(c["category"] for c in self.calls)),
                "failed_requests": sum(c["status"] == "failed" for c in self.calls),
                "unfinished_requests": sum(c["status"] == "started" for c in self.calls),
                "visible_tokens": sum(visible_tokens(c.get("usage")) for c in self.calls),
                "sdk_cache_reuses": len(self.reuses),
                "billing": "Provider response usage retained. Billed currency unavailable unless returned by provider; not inferred as zero.",
                "request_details": self.calls}


@contextmanager
def audit_pipeline_requests(ledger: RequestLedger, articles: list[dict]):
    """Instrument SDK calls without replacing any extraction/merge business result.

    The normal workflow must explicitly close unscoped global LLM backfills and
    use offline S03. An unexpected/unbound call stops before network disclosure.
    """
    from google import genai
    from google.genai import models, types
    import openai
    from openai.resources.chat.completions import Completions
    from openai.types.chat import ChatCompletion
    from s01b_project_extractor.llm.client import LLMClient
    from s01b_project_extractor.tools import project_extractor_tool as writer
    from s02a_duplicate_merger.tools import duplicate_merger_tool as merger

    local = threading.local()
    url_ids = {a["url"]: a["id"] for a in articles}
    original_load = writer._load_recorded_results
    original_gemini, original_chat = models.Models.generate_content, Completions.create
    original_genai, original_openai = genai.Client, openai.OpenAI.__init__
    original_complete = LLMClient.complete

    class BoundRecordedResults(dict):
        def get(self, url, default=None):
            # This normal writer lookup runs for cached and new articles alike,
            # before subsequent per-article translation on the same worker.
            if url not in url_ids:
                raise BudgetStop("Article outside frozen batch")
            local.ids, local.category, local.stage = [url_ids[url]], "translation", "name_translation"
            return super().get(url, default)

    def load(path):
        return BoundRecordedResults(original_load(path))

    def complete(self, prompt, *, stage, json_mode=True):
        old = (local.category, local.stage)
        local.category, local.stage = "extraction", stage
        try:
            return original_complete(self, prompt, stage=stage, json_mode=json_mode)
        finally:
            local.category, local.stage = old

    def gemini(self, **kw):
        config = kw.get("config")
        request = {"model": kw["model"], "contents": kw.get("contents"),
                   "config": config.model_dump(mode="json") if hasattr(config, "model_dump") else config}
        return ledger.invoke(getattr(local, "category", ""), getattr(local, "ids", []),
                             "gemini", kw["model"], request, lambda: original_gemini(self, **kw),
                             stage=getattr(local, "stage", ""), restore=types.GenerateContentResponse.model_validate)

    def chat(self, *args, **kw):
        endpoint = urlparse(str(self._client.base_url))
        if endpoint.scheme != "https" or endpoint.hostname != "api.deepseek.com":
            raise BudgetStop("Unexpected DeepSeek endpoint")
        return ledger.invoke(getattr(local, "category", ""), getattr(local, "ids", []),
                             "deepseek", kw["model"], kw, lambda: original_chat(self, *args, **kw),
                             stage=getattr(local, "stage", ""), restore=ChatCompletion.model_validate)

    def genai_client(*args, **kw):
        if args or kw.get("vertexai") or kw.get("project") or any(os.getenv(name) for name in (
            "GOOGLE_GEMINI_BASE_URL", "GOOGLE_VERTEX_BASE_URL", "GOOGLE_GENAI_USE_VERTEXAI",
        )):
            raise BudgetStop("Unapproved Gemini endpoint configuration")
        opts = kw.get("http_options") or types.HttpOptions(timeout=90000)
        if getattr(opts, "base_url", None):
            raise BudgetStop("Unapproved Gemini base URL")
        opts.retry_options = types.HttpRetryOptions(attempts=1)
        kw["http_options"] = opts
        return original_genai(*args, **kw)

    def openai_init(self, *args, **kw):
        kw["max_retries"] = 0
        return original_openai(self, *args, **kw)

    def bound(fn, project_selector):
        @functools.wraps(fn)
        def wrapper(*args, **kw):
            records = project_selector(args, kw)
            urls = {u for record in records for u in record.get("source_links", [])}
            ids = sorted({url_ids[u] for u in urls if u in url_ids})
            if not ids:
                # Zero-input paths do not send a request; the SDK guard will reject
                # any unexpected call inside them before network.
                ids = []
            old = (getattr(local, "ids", []), getattr(local, "category", ""), getattr(local, "stage", ""))
            local.ids, local.category, local.stage = ids, "dedup", fn.__name__
            try:
                return fn(*args, **kw)
            finally:
                local.ids, local.category, local.stage = old
        return wrapper

    with ExitStack() as stack:
        for obj, name, replacement in (
            (models.Models, "generate_content", gemini), (Completions, "create", chat),
            (genai, "Client", genai_client), (openai.OpenAI, "__init__", openai_init),
            (writer, "_load_recorded_results", load), (LLMClient, "complete", complete),
            (merger, "DEDUP_WORKERS", 2),
            (merger, "_infer_location_from_name", bound(merger._infer_location_from_name, lambda a, k: a[2])),
            (merger, "_apply_location_norm", bound(merger._apply_location_norm, lambda a, k: a[0])),
            (merger, "_llm_confirm_raising", bound(merger._llm_confirm_raising, lambda a, k: [a[2], a[3]])),
            (merger, "_llm_confirm_canonical", bound(merger._llm_confirm_canonical, lambda a, k: [a[2]])),
            (merger, "_merge_group", bound(merger._merge_group, lambda a, k: a[2])),
        ):
            stack.enter_context(patch.object(obj, name, replacement))
        yield
