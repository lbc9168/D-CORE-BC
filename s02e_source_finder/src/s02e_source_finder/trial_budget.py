"""Durable, shared request and dollar caps for the explicitly authorized small pilot.

Unknown or interrupted requests consume their entire reservation. A replay never
reissues them, and failures are not silently retried under a different batch key.
"""

import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .pilot import digest, private_path


PRICES = {
    "openai": {"input": .125, "output": .5},  # include the higher cache-write input rate
    "deepseek": {"input": 1.0, "output": 4.0},  # conservative ceiling, not a claimed tariff
    "search": {"input": .1, "output": .4, "grounding": .035},
}
LIMITS = {"old_extract": 26, "new_extract": 80, "search": 20}


def reservation_usd(spec: dict, provider: str) -> float:
    """UTF-8 bytes bound text token count, with envelope margin and maximum output."""
    rates = PRICES[provider]
    request = spec.get("request", spec)
    size = len(json.dumps(request, ensure_ascii=False).encode("utf-8")) + 2048
    if size > 180000:
        raise ValueError("trial_input_too_large")
    output = request.get("max_completion_tokens", request.get("max_tokens", request.get("generationConfig", {}).get("maxOutputTokens", 4096)))
    if not isinstance(output, int) or not 1 <= output <= 8192:
        raise ValueError("trial_output_unbounded")
    return round(size * rates["input"] / 1e6 + output * rates["output"] / 1e6 + rates.get("grounding", 0) + .002, 8)


def usage_cost(call: dict, provider: str) -> float | None:
    usage = call.get("usage", {})
    inp = usage.get("promptTokenCount" if provider == "search" else "prompt_tokens")
    out = usage.get("candidatesTokenCount" if provider == "search" else "completion_tokens")
    thoughts = usage.get("thoughtsTokenCount", 0) if provider == "search" else 0
    if any(type(value) is not int or value < 0 for value in (inp, out, thoughts)):
        return None
    out += thoughts
    rates = PRICES[provider]
    return round((inp * rates["input"] + out * rates["output"]) / 1e6 + rates.get("grounding", 0), 8)


class TrialBudget:
    """All workers reserve through a single SQLite transaction before HTTP dispatch."""

    def __init__(self, path: Path, *, ceiling: float = 5.0):
        self.path = private_path(path)
        if not math.isfinite(ceiling) or not 0 < ceiling <= 5:
            raise ValueError("invalid_trial_budget")
        self.ceiling = ceiling
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), ceiling REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS calls (
                    request_hash TEXT PRIMARY KEY, bucket TEXT NOT NULL, provider TEXT NOT NULL,
                    reserved_usd REAL NOT NULL, charged_usd REAL NOT NULL, status TEXT NOT NULL,
                    started_at TEXT NOT NULL, finished_at TEXT, request_json TEXT NOT NULL,
                    result_json TEXT, known_usage INTEGER NOT NULL DEFAULT 0);
            """)
            conn.execute("INSERT OR IGNORE INTO settings VALUES(1,?)", (ceiling,))
            if conn.execute("SELECT ceiling FROM settings WHERE id=1").fetchone()[0] != ceiling:
                raise ValueError("trial_budget_configuration_changed")
            conn.commit()

    def cached(self, spec: dict) -> dict | None:
        with closing(sqlite3.connect(self.path)) as conn:
            row = conn.execute("SELECT status,result_json FROM calls WHERE request_hash=?", (digest(spec),)).fetchone()
        if row is None:
            return None
        return json.loads(row[1]) if row[1] else {"error": "reserved_request_unresolved", "status": row[0]}

    def reserve(self, spec: dict, bucket: str, provider: str) -> bool:
        if bucket not in LIMITS or provider not in PRICES or (bucket == "search") != (provider == "search"):
            raise ValueError("invalid_trial_request_class")
        reserve = reservation_usd(spec, provider)
        with closing(sqlite3.connect(self.path, timeout=30)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM calls WHERE request_hash=?", (digest(spec),)).fetchone():
                conn.rollback()
                return False
            count = conn.execute("SELECT count(*) FROM calls WHERE bucket=?", (bucket,)).fetchone()[0]
            charged = conn.execute("SELECT COALESCE(sum(charged_usd),0) FROM calls").fetchone()[0]
            if count >= LIMITS[bucket] or charged + reserve > self.ceiling:
                conn.rollback()
                raise ValueError("trial_budget_exhausted")
            conn.execute("INSERT INTO calls(request_hash,bucket,provider,reserved_usd,charged_usd,status,started_at,request_json) VALUES(?,?,?,?,?,'reserved',?,?)",
                         (digest(spec), bucket, provider, reserve, reserve, datetime.now(timezone.utc).isoformat(), json.dumps(spec, ensure_ascii=False)))
            conn.commit()
        return True

    def finish(self, spec: dict, result: dict, *, failed: bool = False) -> None:
        with closing(sqlite3.connect(self.path, timeout=30)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT provider,reserved_usd,status FROM calls WHERE request_hash=?", (digest(spec),)).fetchone()
            if row is None or row[2] != "reserved":
                raise ValueError("request_not_reserved_or_already_finished")
            known = usage_cost(result.get("call", result), row[0])
            charge = known if known is not None else row[1]
            # Keep unexpected higher usage charged, and block subsequent dispatch via the ceiling.
            result = result | {"budget_usage_known": known is not None, "budget_reservation_exceeded": charge > row[1]}
            conn.execute("UPDATE calls SET status=?,charged_usd=?,known_usage=?,finished_at=?,result_json=? WHERE request_hash=?",
                         ("failed" if failed else "completed", charge, int(known is not None), datetime.now(timezone.utc).isoformat(), json.dumps(result, ensure_ascii=False), digest(spec)))
            conn.commit()

    def summary(self) -> dict:
        with closing(sqlite3.connect(self.path)) as conn:
            counts = dict(conn.execute("SELECT bucket,count(*) FROM calls GROUP BY bucket"))
            total, unknown, reserved = conn.execute("SELECT COALESCE(sum(charged_usd),0),COALESCE(sum(known_usage=0),0),COALESCE(sum(status='reserved'),0) FROM calls").fetchone()
        return {"limits": LIMITS, "requests": counts, "charged_upper_usd": round(total, 8), "ceiling_usd": self.ceiling, "usage_unknown_requests": unknown, "unresolved_requests": reserved, "pricing": PRICES, "cost_basis": "conservative accounting; unknown attempts fully reserved; not an invoice"}
