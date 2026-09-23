"""Limited Chinese Serper candidate discovery; snippets are never acceptance evidence."""

import argparse
import json
import logging
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from .contracts import EventContext
from .fetch import public_url
from .pilot import digest, private_path
from .sample import ROOT, file_hash, write_json

logger = logging.getLogger(__name__)


class SearchItem(BaseModel):
    """Validate each external result before its URL can become a candidate."""
    model_config = ConfigDict(extra="ignore")
    title: str = ""
    link: str = Field(min_length=1)
    snippet: str = ""


class SearchResponse(BaseModel):
    """Accept only an explicit list of search results from the provider."""
    model_config = ConfigDict(extra="ignore")
    organic: list[SearchItem] = Field(default_factory=list)


def queries(context: EventContext) -> list[str]:
    """Generate three finite Chinese queries from facts already in the target record."""
    name = str(context.project.get("project_name") or "").strip()
    country = str(context.project.get("country") or "").strip()
    stage = str(context.event.get("project_stage") or "").strip()
    stage = "" if stage == "Unknown" else stage
    entity = context.entities[0] if context.entities else {}
    participant = str(entity.get("subsidiary_name") or entity.get("implementing_entity") or "").strip()
    participant = "" if participant == "Unknown" else participant
    year = str(context.event.get("publish_date") or "")[:4]
    alias = str(context.project.get("project_name_en") or "").strip()
    source_title = next((str(article.get("title")) for article in context.source_articles if article.get("title")), name)
    suffix = " -site:goalfore.cn -site:news.goalfore.cn"
    return [f"{name} {participant} {stage}".strip() + suffix, f"{source_title} {country} {year}".strip() + suffix, f"{country} {alias or name} {participant} 项目 官方".strip() + suffix]


def search(query: str, key: str, *, sleeper=time.sleep) -> tuple[list[dict], int]:
    """Fetch at most five results with finite 5xx/429 retries and explicit HTTP errors."""
    attempts = 0
    for attempt in range(3):
        attempts += 1
        with requests.post("https://google.serper.dev/search", headers={"X-API-KEY": key}, json={"q": query, "num": 5, "hl": "zh-cn"}, timeout=(10, 30)) as response:
            status = response.status_code
            if status == 429 or 500 <= status < 600:
                if attempt == 2:
                    raise RuntimeError(f"serper_http_{status};attempts={attempts}")
                if status == 429:
                    value = response.headers.get("Retry-After", "30")
                    if not value.isdigit() or int(value) > 60:
                        raise RuntimeError(f"serper_rate_limit_deferred;attempts={attempts}")
                    sleeper(max(30, int(value)))
                else:
                    sleeper(2 ** attempt)
                continue
            if status != 200:
                raise RuntimeError(f"serper_http_{status};attempts={attempts}")
            body = SearchResponse.model_validate(response.json())
        candidates = []
        for item in body.organic[:5]:
            try:
                url = public_url(item.link, resolve=False)
            except ValueError:
                # Search can return excluded sources; retain the raw response in provider logs only.
                continue
            candidates.append({"url": url, "title": item.title, "snippet": item.snippet, "origin": "serper"})
        return candidates, attempts
    raise RuntimeError("serper_attempts_exhausted")


def run(selected_path: Path, *, execute: bool = False, round_number: int = 1, max_queries: int = 7, event_ids: list[str] | None = None, query_override: str | None = None) -> dict:
    """Search selected gaps once per query and preserve cached results on resume."""
    selected_path = private_path(selected_path, existing=True)
    if round_number not in {1, 2, 3} or not 0 <= max_queries <= 30:
        raise ValueError("invalid_pilot_search_budget")
    if query_override is not None and (not event_ids or len(event_ids) != 1 or not 2 <= len(query_override.strip()) <= 300):
        raise ValueError("query_override_requires_one_explicit_event")
    selected = json.loads(selected_path.read_text(encoding="utf-8"))
    if not isinstance(selected, dict) or not isinstance(selected.get("events"), list) or len(selected["events"]) != 30:
        raise ValueError("search_requires_fixed_30_event_selection")
    planned = []
    for row in selected["events"]:
        context = EventContext.model_validate(row["context"])
        if event_ids is not None:
            if context.event_id not in event_ids:
                continue
        elif row["candidates"]:
            continue
        query = (query_override.strip() + " -site:goalfore.cn -site:news.goalfore.cn") if query_override else queries(context)[round_number - 1]
        planned.append({"event_id": context.event_id, "project_id": context.project_id, "query": query, "round": round_number})
    report = {"planned_queries": len(planned), "max_queries": max_queries, "max_http_attempts": 3 * max_queries, "round": round_number, "execute": execute, "queries": planned}
    if not execute:
        return report
    load_dotenv(ROOT / ".env", override=False)
    key = os.environ.get("SERPER_API_KEY", "")
    if not key:
        raise ValueError("serper_key_missing")
    directory = selected_path.parent / "searches"
    directory.mkdir(exist_ok=True)
    calls = 0
    attempts = 0
    for item in planned:
        prior_round = [json.loads(path.read_text(encoding="utf-8")) for pattern in ("*.json", "*.reserved") for path in directory.glob(pattern)]
        if any(row.get("event_id") == item["event_id"] and row.get("round") == round_number for row in prior_round):
            continue
        output = directory / (digest([item["event_id"], item["query"]]) + ".json")
        if output.exists():
            continue
        if calls >= max_queries:
            break
        # Reserve on disk before the paid request; interrupted searches are not auto-replayed.
        reservation = output.with_suffix(".reserved")
        if reservation.exists():
            continue
        write_json(reservation, item)
        calls += 1
        try:
            candidates, used = search(item["query"], key)
            attempts += used
            result = {**item, "candidates": candidates, "http_attempts": used, "error": None, "selected_sha256": file_hash(selected_path)}
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            logger.error("pilot_search_failed event=%s type=%s", item["event_id"], type(exc).__name__)
            # No raw requests exception: it may include request headers or proxy details.
            reason = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            result = {**item, "candidates": [], "error": reason, "http_attempts": None, "selected_sha256": file_hash(selected_path)}
        write_json(output, result)
        if result["error"]:
            report["stopped"] = result["error"]
            break
    return {**report, "new_queries": calls, "known_http_attempts": attempts, "directory": str(directory)}


def main() -> None:
    """Preview is free; --execute is required for explicitly capped Serper queries."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selected", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--round", dest="round_number", type=int, default=1)
    parser.add_argument("--max-queries", type=int, default=7)
    parser.add_argument("--event-id", action="append")
    parser.add_argument("--query", dest="query_override")
    args = parser.parse_args()
    if args.execute:
        directory = private_path(args.selected, existing=True).parent
        logging.basicConfig(filename=directory / "search.log", level=logging.INFO, encoding="utf-8")
    print(json.dumps(run(args.selected, execute=args.execute, round_number=args.round_number, max_queries=args.max_queries, event_ids=args.event_id, query_override=args.query_override), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
