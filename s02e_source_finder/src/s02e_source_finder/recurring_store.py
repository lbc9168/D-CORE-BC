"""Transactional reference persistence and user-authorized, evidence-gated capacity fills.

This module makes no external calls. Full fetched bodies remain in the work corpus;
the main database stores reference metadata, decisions, hashes and quoted evidence.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from .capacity_apply import (
    AUDIT_TABLE, CAPACITY_FIELDS, Candidate, _after, _backup, _check_integrity,
    _connect, _json, _snapshot, _validate_target, manifest_hash,
)
from .contracts import EventContext

logger = logging.getLogger(__name__)
TABLE = "external_reference_candidates"
ACCEPTED = {"event_reference", "project_reference"}
OWN_TABLES = {TABLE, AUDIT_TABLE, "capacity_review_conflicts"}
REFERENCE_FIELDS = ("reference_id", "url", "title", "publisher", "source_type", "scope",
                    "event_id", "stage", "stage_en", "event_date", "checked_at", "valid_until",
                    "is_reprint", "original_publisher")


def _private_metadata(value):
    """Do not duplicate fetched articles or raw provider responses in the main DB."""
    if isinstance(value, dict):
        return {k: _private_metadata(v) for k, v in value.items()
                if k not in {"content_text", "text", "content", "source_articles", "page", "raw_response"}}
    if isinstance(value, list):
        return [_private_metadata(v) for v in value]
    return value


def _url_key(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("candidate_url_invalid")
    return urlunsplit(parsed._replace(scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower(), fragment=""))


def _table_exists(conn, name):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _validate_context(conn, context: EventContext, capacities: list[Candidate]):
    project = conn.execute("SELECT * FROM canonical_projects WHERE id=?", (context.project_id,)).fetchone()
    event = conn.execute("SELECT * FROM project_events WHERE id=?", (context.event_id,)).fetchone()
    if project is None or event is None or event["canonical_id"] != context.project_id:
        raise ValueError("reference_event_project_mismatch")
    if dict(project) != context.project or context.event.get("id") != context.event_id:
        raise ValueError("reference_frozen_project_changed")
    live = dict(event)
    if live != context.event:
        # Exact repeat of our own previously applied fill is safe; other drift is not.
        audit = conn.execute(f"SELECT candidate_id,before_json,after_json FROM {AUDIT_TABLE} WHERE event_id=?",
                             (context.event_id,)).fetchone() if _table_exists(conn, AUDIT_TABLE) else None
        if (audit is None or audit["candidate_id"] not in {c.candidate_id for c in capacities}
                or json.loads(audit["before_json"]) != context.event or json.loads(audit["after_json"]) != live):
            raise ValueError("reference_frozen_event_changed")
    if _table_exists(conn, "chinese_entities"):
        entities = [dict(r) for r in conn.execute("SELECT * FROM chinese_entities WHERE event_id=?", (context.event_id,))]
        if sorted(entities, key=_json) != sorted(context.entities, key=_json):
            raise ValueError("reference_frozen_participation_changed")
    elif context.entities:
        raise ValueError("reference_participation_table_missing")


def _candidate(context, row):
    if not isinstance(row, dict) or row.get("project_id") != context.project_id or row.get("event_id") != context.event_id:
        raise ValueError("reference_result_target_mismatch")
    ref = row.get("external_reference")
    if ref is not None:
        if not isinstance(ref, dict) or any(key not in ref for key in REFERENCE_FIELDS):
            raise ValueError("reference_metadata_incomplete")
        ref = {key: ref[key] for key in REFERENCE_FIELDS}
        if row.get("decision") != ref["scope"] + "_reference":
            raise ValueError("reference_decision_scope_mismatch")
        if ref["scope"] == "event" and (ref["event_id"] != context.event_id or ref["stage"] != context.event.get("project_stage")):
            raise ValueError("reference_event_claim_mismatch")
    url = row.get("candidate_url") or row.get("url") or (row.get("page") or {}).get("final_url") or (ref or {}).get("url")
    if url is not None and not isinstance(url, str):
        raise ValueError("candidate_url_invalid")
    key = _url_key(url) if url else "case:" + str(row.get("case_id") or manifest_hash(_private_metadata(row)))
    projected = {"case_id": str(row.get("case_id", manifest_hash([context.event_id, key])[:20])),
                 "project_id": context.project_id, "event_id": context.event_id,
                 "target": {"project_name": context.project.get("project_name"), "country_iso": context.project.get("country_iso")},
                 "decision": row.get("decision", "pending"), "reason": row.get("reason", ""),
                 "reference": ref if row.get("decision") in ACCEPTED else None}
    if row.get("evidence_language") is not None:
        projected["evidence_language"] = row["evidence_language"]
    return key, url, projected


def _schema(conn):
    conn.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE} (
        event_id TEXT NOT NULL REFERENCES project_events(id),
        candidate_key TEXT NOT NULL, project_id TEXT NOT NULL REFERENCES canonical_projects(id),
        candidate_url TEXT, context_sha256 TEXT NOT NULL, candidate_json TEXT NOT NULL,
        result_json TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(event_id,candidate_key))""")


def _capacity_schema(conn):
    conn.execute(f"""CREATE TABLE IF NOT EXISTS {AUDIT_TABLE} (
        event_id TEXT PRIMARY KEY REFERENCES project_events(id), project_id TEXT NOT NULL,
        approved_manifest_hash TEXT NOT NULL, candidate_id TEXT NOT NULL,
        before_json TEXT NOT NULL, after_json TEXT NOT NULL, evidence_json TEXT NOT NULL,
        approval_json TEXT NOT NULL, approved_at TEXT NOT NULL, applied_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS capacity_review_conflicts (
        conflict_hash TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES project_events(id),
        approved_manifest_hash TEXT NOT NULL, incoming_json TEXT NOT NULL,
        preserved_json TEXT NOT NULL, observed_at TEXT NOT NULL)""")


def _fill_capacities(conn, capacities, now):
    grouped = {}
    for candidate in capacities:
        grouped.setdefault(candidate.event_id, {})[candidate.candidate_id] = candidate
    applied, conflicts = {}, []
    for event_id, by_id in grouped.items():
        candidates = list(by_id.values())
        signatures = {(c.capacity_mw, c.raw_unit, c.scope, c.phase) for c in candidates}
        if len(signatures) != 1:
            conflicts.append(event_id)
            continue
        candidate = sorted(candidates, key=lambda c: c.candidate_id)[0]
        audit = conn.execute(f"SELECT candidate_id,after_json FROM {AUDIT_TABLE} WHERE event_id=?", (event_id,)).fetchone() if _table_exists(conn, AUDIT_TABLE) else None
        if audit:
            current = conn.execute("SELECT * FROM project_events WHERE id=?", (event_id,)).fetchone()
            if current is None or any(current[k] != json.loads(audit["after_json"])[k] for k in CAPACITY_FIELDS):
                raise ValueError("Previously applied capacity has drifted")
            continue  # Never replace a nonempty, previously audited capacity.
        _validate_target(conn, candidate)
        _capacity_schema(conn)
        after = _after(candidate)
        # Keep the established manual/source marker for S02A protection, but state
        # accurately that this is workflow authorization, not individual human review.
        after["capacity_details"] = after["capacity_details"].replace(
            "reviewed generation capacity fill-null", "evidence-validated generation capacity fill-null; user-authorized recurring workflow")
        updated = conn.execute("UPDATE project_events SET capacity_mw=?,capacity_mw_raw=?,capacity_details=?,capacity_source=? WHERE id=? AND capacity_mw IS NULL",
                              tuple(after[k] for k in CAPACITY_FIELDS) + (event_id,)).rowcount
        if updated != 1:
            raise ValueError("Capacity fill did not update exactly one event")
        authorization = {"approval_kind": "workflow_authorized", "human_candidate_review": False,
                         "rule_version": "recurring-capacity-v1", "note": "User authorized incidental evidence-gated empty-capacity fills; no per-candidate human approval inferred"}
        digest = manifest_hash({"candidate": candidate.model_dump(), "authorization": authorization})
        conn.execute(f"INSERT INTO {AUDIT_TABLE} VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (event_id, candidate.project_id, digest, candidate.candidate_id, _json(candidate.before), _json(after),
                      _json(_private_metadata(candidate.model_dump())), _json(authorization), now, now))
        applied[event_id] = after
    return applied, conflicts


def _legacy(path):
    if path is None or not path.exists():
        return []
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("version") != "external-references-v1" or not isinstance(document.get("candidates"), list):
        raise ValueError("legacy_reference_snapshot_invalid")
    return document["candidates"]


def _snapshot_candidates(conn, previous):
    merged = {}
    stored = list(conn.execute(f"SELECT event_id,candidate_key,candidate_json FROM {TABLE} ORDER BY event_id,candidate_key"))
    owned_cases = {(r["event_id"], json.loads(r["candidate_json"]).get("case_id")) for r in stored}
    owned_events = {r["event_id"] for r in stored}
    for candidate in previous:
        if not isinstance(candidate, dict):
            continue
        if not candidate.get("reference") and candidate.get("event_id") in owned_events:
            continue  # The DB owns pending decisions; an old case hash must not resurrect them.
        if (candidate.get("event_id"), candidate.get("case_id")) in owned_cases:
            continue  # Metadata-only pending rows have no URL; do not duplicate them under a case key.
        ref = candidate.get("reference") or {}
        url = ref.get("url")
        key = (candidate.get("event_id"), _url_key(url) if url else "case:" + str(candidate.get("case_id")))
        # Public handoff has a strict whitelist, even when a legacy file contains extras.
        merged[key] = {k: candidate.get(k) for k in ("case_id", "project_id", "event_id", "target", "decision", "reason", "reference")}
        if "evidence_language" in candidate:
            merged[key]["evidence_language"] = candidate["evidence_language"]
    for row in stored:
        merged[(row["event_id"], row["candidate_key"])] = json.loads(row["candidate_json"])
    owners = {r["id"]: dict(r) for r in conn.execute("SELECT e.id,e.canonical_id,e.project_stage,p.project_name,p.country_iso FROM project_events e JOIN canonical_projects p ON p.id=e.canonical_id")}
    for candidate in merged.values():
        if candidate.get("evidence_language") is None and candidate.get("decision") not in ACCEPTED:
            candidate.pop("evidence_language", None)  # Unknown language is not an assertion of non-Chinese evidence.
        owner = owners.get(candidate.get("event_id"))
        target = candidate.get("target") or {}
        ref = candidate.get("reference") or {}
        if (owner is None or owner["canonical_id"] != candidate.get("project_id")
                or any(target.get(k) != owner.get(k) for k in ("project_name", "country_iso"))
                or ref.get("scope") == "event" and ref.get("stage") != owner["project_stage"]):
            candidate.update(decision="pending", reason="reference_current_target_changed", reference=None)
        elif ref:
            candidate["reference"] = {k: ref.get(k) for k in REFERENCE_FIELDS}
    return list(merged.values())


def _verify_changes(before, after, replacements):
    expected = json.loads(_json(before))
    expected["project_events"]["rows"] = sorted([replacements.get(r["id"], r) for r in expected["project_events"]["rows"]], key=_json)
    for table, value in expected.items():
        if table not in OWN_TABLES | {"$schema"} and after.get(table) != value:
            raise ValueError(f"Unexpected database mutation: {table}")
    if set(after) - set(before) - OWN_TABLES:
        raise ValueError("Unexpected table created during reference persistence")
    if ([r for r in before["$schema"] if r["tbl_name"] not in OWN_TABLES]
            != [r for r in after["$schema"] if r["tbl_name"] not in OWN_TABLES]):
        raise ValueError("Unexpected schema mutation during reference persistence")


def persist_results(main_db: Path, results: list[dict], *, backup_dir: Path,
                    snapshot_path: Path, legacy_snapshot: Path | None = None) -> dict:
    """Persist completed outcomes atomically; preserve untouched references and IDs.

    Callers must supply frozen targets and only capacity candidates that already
    passed assess_capacity_row. Candidate/body/unit/binding validation runs again.
    """
    main_db, backup_dir, snapshot_path = Path(main_db).resolve(strict=True), Path(backup_dir).resolve(), Path(snapshot_path).resolve()
    if snapshot_path == main_db or snapshot_path.is_relative_to(backup_dir):
        raise ValueError("Snapshot must be separate from database and backups")
    if not isinstance(results, list):
        raise ValueError("results_must_be_list")
    prepared, capacities = [], []
    seen_candidates = {}
    for result in results:
        context = EventContext.model_validate(result["target"]["context"])
        local = [Candidate.model_validate(c) for c in result.get("capacity_candidates", [])]
        for c in local:
            if (c.project_id, c.event_id) != (context.project_id, context.event_id) or c.before != context.event:
                raise ValueError("capacity_result_target_mismatch")
            if c.candidate_id in seen_candidates and seen_candidates[c.candidate_id] != c.model_dump():
                raise ValueError("conflicting_capacity_candidate_id")
            seen_candidates[c.candidate_id] = c.model_dump()
        capacities.extend(local)
        incoming_rows = result.get("rows", [])
        if not isinstance(incoming_rows, list):
            raise ValueError("reference_rows_must_be_list")
        if not incoming_rows:
            incoming_rows = [{"case_id": "discovery-attempt", "project_id": context.project_id,
                              "event_id": context.event_id, "decision": "pending",
                              "reason": result.get("reason") or "no_reference_candidates"}]
        rows = []
        for row in incoming_rows:
            key, url, projected = _candidate(context, row)
            metadata = row | {"capacity_candidates": [c.model_dump() for c in local]}
            rows.append((metadata, key, url, projected))
        prepared.append((context, local, rows))
    previous = _legacy(Path(legacy_snapshot) if legacy_snapshot else None) + _legacy(snapshot_path)
    now = datetime.now(timezone.utc).isoformat()
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    staged = snapshot_path.with_name(snapshot_path.name + "." + uuid4().hex + ".tmp")
    try:
        with closing(_connect(main_db)) as conn:
            _check_integrity(conn)
            before = _snapshot(conn)
            before_hash = manifest_hash(before)
            for context, local, _ in prepared:
                _validate_context(conn, context, local)
            backup = backup_dir / ("before-references-" + uuid4().hex + ".db")
            _backup(main_db, backup, before_hash)
            backup_manifest = {"database": str(main_db), "backup": str(backup), "created_at": now,
                               "logical_sha256": before_hash, "backup_sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
                               "integrity_check": "ok", "foreign_key_check": "ok"}
            with backup.with_suffix(".manifest.json").open("x", encoding="utf-8") as handle:
                handle.write(_json(backup_manifest))
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                if manifest_hash(_snapshot(conn)) != before_hash:
                    raise ValueError("Database drift while obtaining reference write lock")
                for context, local, _ in prepared:
                    _validate_context(conn, context, local)
                _schema(conn)
                changed = 0
                for context, _, rows in prepared:
                    for row, key, url, projected in rows:
                        values = (context.event_id, key, context.project_id, url, context.fingerprint(),
                                  _json(projected), _json(_private_metadata(row)), now)
                        changed += conn.execute(f"""INSERT INTO {TABLE} VALUES (?,?,?,?,?,?,?,?)
                            ON CONFLICT(event_id,candidate_key) DO UPDATE SET
                            project_id=excluded.project_id,candidate_url=excluded.candidate_url,
                            context_sha256=excluded.context_sha256,candidate_json=excluded.candidate_json,
                            result_json=excluded.result_json,updated_at=excluded.updated_at
                            WHERE candidate_json<>excluded.candidate_json OR result_json<>excluded.result_json
                               OR context_sha256<>excluded.context_sha256""", values).rowcount
                replacements, conflicts = _fill_capacities(conn, capacities, now)
                after = _snapshot(conn)
                _verify_changes(before, after, replacements)
                _check_integrity(conn)
                document = {"version": "external-references-v1", "provenance": {"source_db": str(main_db), "source_snapshot_sha256": manifest_hash(after)},
                            "candidates": _snapshot_candidates(conn, previous)}
                with staged.open("x", encoding="utf-8") as handle:
                    handle.write(_json(document))
            staged.replace(snapshot_path)
        logger.info("References persisted: changed=%s capacity_filled=%s conflicts=%s backup=%s", changed, len(replacements), len(conflicts), backup)
        return {"status": "persisted", "reference_rows_changed": changed, "capacity_filled": len(replacements),
                "capacity_conflict_events": conflicts, "snapshot_candidates": len(document["candidates"]),
                "backup": str(backup), "snapshot": str(snapshot_path)}
    finally:
        staged.unlink(missing_ok=True)
