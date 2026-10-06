"""Source-side identity holds: business ambiguity is neither imported nor failed.

No review rows live in canonical/event tables. Source membership is unique so an
ordinary retry cannot create another task or silently release an unresolved row.
"""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3


class CoordinationRequired(Exception):
    def __init__(self, evidence):
        self.evidence = evidence
        super().__init__(evidence.get("reason", "Unresolved project identity"))


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def init_queue(conn, schema="main"):
    if schema not in {"main", "extracted"}:
        raise ValueError("Invalid queue schema")
    conn.execute(f"""CREATE TABLE IF NOT EXISTS {schema}.s02a_coordination (
        task_id TEXT PRIMARY KEY, status TEXT NOT NULL CHECK(status IN ('pending','resolved')),
        project_json TEXT NOT NULL, source_rows_json TEXT NOT NULL,
        evidence_json TEXT NOT NULL, created_at TEXT NOT NULL,
        resolution_json TEXT, resolved_at TEXT)""")
    conn.execute(f"""CREATE TABLE IF NOT EXISTS {schema}.s02a_coordination_sources (
        source_id INTEGER PRIMARY KEY REFERENCES extracted_projects(id),
        task_id TEXT NOT NULL REFERENCES s02a_coordination(task_id))""")


def held_ids(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='s02a_coordination_sources'").fetchone():
        return set()
    # Resolved rows are imported atomically; never auto-release a manually reset flag.
    return {r[0] for r in conn.execute("SELECT source_id FROM s02a_coordination_sources")}


def validate_source_urls(source_urls):
    """None retains the legacy scope; an explicit empty selection means no rows."""
    if source_urls is None:
        return None
    if not isinstance(source_urls, list) or any(not isinstance(url, str) or not url.strip() for url in source_urls):
        raise ValueError("source_urls must be a list of non-empty strings")
    return sorted(set(source_urls))


def pending_count(path, source_urls=None):
    source_urls = validate_source_urls(source_urls)
    if source_urls == []:
        return 0
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='s02a_coordination'").fetchone():
            return 0
        if source_urls is not None:
            placeholders = ",".join("?" for _ in source_urls)
            return conn.execute(
                "SELECT COUNT(DISTINCT q.task_id) FROM s02a_coordination q "
                "JOIN s02a_coordination_sources s ON s.task_id=q.task_id "
                "JOIN extracted_projects p ON p.id=s.source_id "
                f"WHERE q.status='pending' AND p.source_link IN ({placeholders})", source_urls
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM s02a_coordination WHERE status='pending'").fetchone()[0]


def save_hold(conn, project, evidence):
    """Inside the attached write transaction; preserve exact source rows and verdict."""
    ids = sorted(set(project.get("_source_record_ids", [])))
    if not ids:
        raise ValueError("Cannot persist an identity hold without source row IDs")
    init_queue(conn, "extracted")
    task_id = "identity-" + hashlib.sha256(dumps(ids).encode()).hexdigest()[:24]
    cursor = conn.execute(f"SELECT * FROM extracted.extracted_projects WHERE id IN ({','.join('?' for _ in ids)}) ORDER BY id", ids)
    names = [c[0] for c in cursor.description]
    rows = [dict(zip(names, r)) for r in cursor]
    if len(rows) != len(ids) or any(r["imported_to_s02a"] for r in rows):
        raise ValueError("Identity hold source rows missing or already imported")
    conn.execute("INSERT OR IGNORE INTO extracted.s02a_coordination VALUES (?, 'pending', ?, ?, ?, ?, NULL, NULL)",
                 (task_id, dumps(project), dumps(rows), dumps(evidence), datetime.now(timezone.utc).isoformat()))
    conn.executemany("INSERT OR IGNORE INTO extracted.s02a_coordination_sources VALUES (?,?)", [(i, task_id) for i in ids])
    actual = {r[0] for r in conn.execute("SELECT source_id FROM extracted.s02a_coordination_sources WHERE task_id=?", (task_id,))}
    if actual != set(ids):
        raise ValueError("Source rows already belong to another coordination task")
    return task_id


@contextmanager
def attached_transaction(input_db, main_db):
    """Trusted single operator: DELETE journals make source + main commit atomic.

Refuse active WAL readers rather than accept a cross-database partial commit.
The caller prepares schema before entry; all business rows/flags commit together.
"""
    paths = [Path(input_db).resolve(), Path(main_db).resolve()]
    if paths[0] == paths[1] or paths[0].samefile(paths[1]):
        raise ValueError("Input and main databases must be different files")
    modes = []
    try:
        for path in paths:
            with closing(sqlite3.connect(path, timeout=1)) as check:
                mode = check.execute("PRAGMA journal_mode").fetchone()[0].lower()
                modes.append((path, mode))
                if check.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower() != "delete":
                    raise RuntimeError("Cannot acquire offline atomic write window")
        with closing(sqlite3.connect(paths[1])) as conn, conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("ATTACH DATABASE ? AS extracted", (str(paths[0]),))
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE extracted.extracted_projects SET id=id WHERE 0")
            yield conn
    finally:
        for path, mode in modes:
            if mode not in {"delete", "wal", "truncate", "persist", "memory", "off"}:
                raise RuntimeError("Unknown journal mode")
            with closing(sqlite3.connect(path, timeout=1)) as check:
                if check.execute("PRAGMA journal_mode=" + mode).fetchone()[0].lower() != mode:
                    raise RuntimeError("Could not restore original journal mode")
