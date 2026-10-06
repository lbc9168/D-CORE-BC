"""Persistent authorization envelope shared by every recurring discovery request."""
import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .pilot import digest
from .trial_budget import reservation_usd, usage_cost

# A transport failure can occur after the provider received the request. Retain
# its full charge reservation and prevent paid replay without a new decision.
UNKNOWN_OUTCOME_ERRORS = {"ReadTimeout", "Timeout", "ConnectTimeout", "ConnectionError",
                          "ProxyError", "SSLError", "ChunkedEncodingError", "ContentDecodingError"}


class RecurringBudget:
    """A changed ceiling never resets spent money or unresolved reservations."""

    def __init__(self, path: Path, *, ceiling: float | None, search_limit: int | None, extraction_limit: int | None):
        if (ceiling is not None and (not math.isfinite(ceiling) or ceiling < 0)
                or any(limit is not None and limit < 0 for limit in (search_limit, extraction_limit))):
            raise ValueError("invalid_recurring_budget")
        self.path, self.ceiling = path, ceiling
        self.limits = {"search": search_limit, "new_extract": extraction_limit}
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS calls (
                request_hash TEXT PRIMARY KEY, bucket TEXT NOT NULL, provider TEXT NOT NULL,
                reserved_usd REAL NOT NULL, charged_usd REAL NOT NULL, status TEXT NOT NULL,
                started_at TEXT NOT NULL, result_json TEXT, known_usage INTEGER NOT NULL DEFAULT 0)""")
            conn.commit()

    def cached(self, spec):
        with closing(sqlite3.connect(self.path)) as conn:
            row = conn.execute("SELECT result_json,status,known_usage FROM calls WHERE request_hash=?", (digest(spec),)).fetchone()
        if row is None:
            return None
        result = json.loads(row[0]) if row[0] else {"error": "reserved_request_unresolved"}
        if row[1] == "reserved" or not row[2] and result.get("error") in UNKNOWN_OUTCOME_ERRORS:
            result["request_outcome_unknown"] = True
        return result

    def reserve(self, spec, bucket, provider):
        if bucket not in self.limits or (bucket == "search") != (provider == "search"):
            raise ValueError("invalid_recurring_request_class")
        amount = reservation_usd(spec, provider)
        with closing(sqlite3.connect(self.path, timeout=30)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM calls WHERE request_hash=?", (digest(spec),)).fetchone():
                conn.rollback()
                return False
            spent = conn.execute("SELECT COALESCE(sum(charged_usd),0) FROM calls").fetchone()[0]
            count = conn.execute("SELECT count(*) FROM calls WHERE bucket=?", (bucket,)).fetchone()[0]
            if ((self.ceiling is not None and spent + amount > self.ceiling)
                    or (self.limits[bucket] is not None and count >= self.limits[bucket])):
                raise ValueError("trial_budget_exhausted")  # shared judge_case boundary
            conn.execute("INSERT INTO calls VALUES(?,?,?,?,?,'reserved',?,NULL,0)",
                         (digest(spec), bucket, provider, amount, amount, datetime.now(timezone.utc).isoformat()))
            conn.commit()
        return True

    def finish(self, spec, result, *, failed=False):
        with closing(sqlite3.connect(self.path, timeout=30)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT provider,reserved_usd,status FROM calls WHERE request_hash=?", (digest(spec),)).fetchone()
            if row is None or row[2] != "reserved":
                raise ValueError("request_not_reserved_or_already_finished")
            known = usage_cost(result.get("call", result), row[0])
            conn.execute("UPDATE calls SET charged_usd=?,status=?,result_json=?,known_usage=? WHERE request_hash=?",
                         (row[1] if known is None else known, "failed" if failed else "completed",
                          json.dumps(result, ensure_ascii=False), int(known is not None), digest(spec)))
            conn.commit()

    def summary(self):
        with closing(sqlite3.connect(self.path)) as conn:
            counts = dict(conn.execute("SELECT bucket,count(*) FROM calls GROUP BY bucket"))
            total, unknown, unresolved = conn.execute("SELECT COALESCE(sum(charged_usd),0),COALESCE(sum(known_usage=0),0),COALESCE(sum(status='reserved'),0) FROM calls").fetchone()
        return {"requests": counts, "charged_upper_usd": round(total, 8), "ceiling_usd": self.ceiling,
                "usage_unknown_requests": unknown, "unresolved_requests": unresolved,
                "limits": self.limits, "cost_basis": "conservative accounting, not an invoice"}
