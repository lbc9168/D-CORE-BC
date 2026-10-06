"""Offline, reviewed fill-null generation capacity patches; no fetching or model calls.

Approval is a human-supplied artifact, never inferred from candidate eligibility.
English evidence stays in the private audit; public link columns are never touched.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

AUDIT_TABLE = "capacity_review_audit"
CAPACITY_FIELDS = ("capacity_mw", "capacity_mw_raw", "capacity_details", "capacity_source")
GENERATION_CATEGORIES = {"光伏", "风电", "水电", "地热及余热", "生物质", "综合能源"}
# Reject energy/throughput suffixes without mistaking "MW hydro" for MWh.
POWER_UNIT_END = r"(?![A-Za-z时時]|\s*(?:[·⋅•.]?\s*(?:h|hours?)\b|[·⋅•.]?\s*(?:小时|小時|时|時)|/\s*(?:年|year)|每年|per\s+(?:year|annum)))"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def manifest_hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _norm(value: str) -> str:
    return " ".join(value.split())


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Evidence(StrictModel):
    url: str
    language: str = Field(min_length=2)
    publisher: str = Field(min_length=1)
    source_type: Literal["official", "media"]
    quote: str = Field(min_length=1)
    content_text: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verify_frozen_text(self):
        url = urlparse(self.url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError("Evidence requires an HTTP(S) URL without credentials")
        if hashlib.sha256(self.content_text.encode("utf-8")).hexdigest() != self.content_sha256:
            raise ValueError("Evidence body hash mismatch")
        if not _norm(self.quote) or _norm(self.quote) not in _norm(self.content_text):
            raise ValueError("Evidence quote is absent from frozen body")
        return self


class EventBinding(StrictModel):
    project_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    project_stage: str = Field(min_length=1)
    phase: str = Field(min_length=1)
    evidence_quote: str = Field(min_length=1)
    reason: str = ""


class Candidate(StrictModel):
    candidate_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    capacity_mw: float = Field(gt=0, allow_inf_nan=False)
    raw_value: str = Field(pattern=r"^[0-9]+(?:\.[0-9]+)?$")
    raw_unit: Literal["MW", "MWac", "MWp", "MWdc", "kW", "GW", "兆瓦", "吉瓦", "千瓦", "万千瓦"]
    scope: Literal["project", "phase"]
    phase: str = Field(min_length=1)
    evidence: Evidence
    event_binding: EventBinding
    before: dict[str, Any]
    status: Literal["eligible"]
    conflict: Literal[False]

    @model_validator(mode="after")
    def verify_binding_and_units(self):
        factor = {"kW": .001, "GW": 1000, "千瓦": .001, "万千瓦": 10, "吉瓦": 1000}.get(self.raw_unit, 1)
        if not math.isclose(float(self.raw_value) * factor, self.capacity_mw, rel_tol=1e-12):
            raise ValueError("Raw unit/value does not equal normalized MW")
        raw_match = re.search(r"(?<![\d.])" + re.escape(self.raw_value) + r"\s*" + re.escape(self.raw_unit)
                              + POWER_UNIT_END, self.evidence.quote, re.I)
        if not raw_match:
            raise ValueError("Evidence quote does not contain the exact raw capacity and unit")
        clause = re.split(r"[。！？；;\n]", self.evidence.quote[:raw_match.start()])[-1]
        if re.search(r"年产|每年|annual\s+(?:production|capacity)|per\s+(?:year|annum)", clause, re.I):
            raise ValueError("Annual production cannot be generation capacity")
        b = self.event_binding
        if (b.project_id, b.event_id, b.phase) != (self.project_id, self.event_id, self.phase):
            raise ValueError("Candidate and event binding disagree")
        if not _norm(b.evidence_quote) or _norm(b.evidence_quote) not in _norm(self.evidence.content_text):
            raise ValueError("Event binding quote is absent from frozen body")
        if self.scope == "project" and self.phase != "whole_project":
            raise ValueError("Project scope requires whole_project phase")
        if self.scope == "phase" and (self.phase == "whole_project" or _norm(self.phase) not in _norm(b.evidence_quote)):
            raise ValueError("Named phase must occur in binding evidence")
        return self


class Candidates(StrictModel):
    schema_version: Literal["capacity-candidates-v1"]
    candidates: list[Candidate]


class Approval(StrictModel):
    schema_version: Literal["capacity-approval-v1"]
    candidate_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_candidate_ids: list[str] = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    reviewed_at: str
    review_note: str = Field(min_length=1)
    approval_kind: Literal["human", "developer_rehearsal"]

    @model_validator(mode="after")
    def verify_review(self):
        parsed = datetime.fromisoformat(self.reviewed_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None or not self.reviewer.strip() or not self.review_note.strip():
            raise ValueError("Review requires reviewer, note and timezone-aware timestamp")
        if len(set(self.approved_candidate_ids)) != len(self.approved_candidate_ids):
            raise ValueError("Duplicate approved candidate IDs")
        return self


def _connect(path: Path, *, readonly: bool = False):
    path = path.resolve(strict=True)
    conn = sqlite3.connect(path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"), uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _snapshot(conn: sqlite3.Connection) -> dict:
    """Logical snapshot also detects WAL-backed changes; does not checkpoint source."""
    result = {"$schema": [dict(row) for row in conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]}
    for name, sql in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"):
        quoted = '"' + name.replace('"', '""') + '"'
        rows = [dict(row) for row in conn.execute(f"SELECT * FROM {quoted}")]
        result[name] = {"sql": sql, "rows": sorted(rows, key=_json)}
    return result


def _check_integrity(conn):
    if [r[0] for r in conn.execute("PRAGMA integrity_check")] != ["ok"]:
        raise ValueError("Database integrity check failed")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("Database foreign key check failed")


def _validate_target(conn, candidate: Candidate):
    row = conn.execute("SELECT * FROM project_events WHERE id=?", (candidate.event_id,)).fetchone()
    project = conn.execute("SELECT * FROM canonical_projects WHERE id=?", (candidate.project_id,)).fetchone()
    if row is None or project is None or row["canonical_id"] != candidate.project_id:
        raise ValueError("Event owner does not match approved project")
    current, parent = dict(row), dict(project)
    if current != candidate.before:
        raise ValueError("Current event differs from approved before snapshot")
    if current.get("capacity_mw") is not None or current.get("capacity_mw_raw") not in (None, ""):
        raise ValueError("Capacity fill-null requires empty numeric and raw fields")
    if parent.get("category") not in GENERATION_CATEGORIES or parent.get("excluded") not in (None, 0):
        raise ValueError("Unknown, excluded or non-generation project")
    if current.get("project_stage") in (None, "", "Unknown") or current["project_stage"] != candidate.event_binding.project_stage:
        raise ValueError("Event stage differs from reviewed binding")
    for record in (parent, current):
        if record.get("publication_status") not in (None, "", "eligible"):
            raise ValueError("Pending or excluded target cannot be patched")
    if not set(CAPACITY_FIELDS).issubset(current):
        raise ValueError("Capacity columns require a separate reviewed schema migration")


def _after(candidate: Candidate) -> dict:
    result = dict(candidate.before)
    raw = f"{candidate.raw_value} {candidate.raw_unit}"
    # Trace is public-safe: no private evidence URL or English source text.
    trace_id = hashlib.sha256(candidate.candidate_id.encode("utf-8")).hexdigest()[:16]
    trace = f"[capacity_review:{trace_id}] {raw}; scope={candidate.scope}; reviewed generation capacity fill-null"
    result.update(capacity_mw=candidate.capacity_mw, capacity_mw_raw=raw,
                  capacity_details="\n".join(x for x in (result.get("capacity_details"), trace) if x),
                  capacity_source="manual")
    return result


def prepare_plan(db: str | Path, candidates: dict, approval: dict) -> dict:
    """Validate explicitly selected candidates without modifying any database."""
    manifest = Candidates.model_validate(candidates)
    review = Approval.model_validate(approval)
    if manifest_hash(candidates) != review.candidate_manifest_sha256:
        raise ValueError("Approval does not bind this candidate manifest")
    by_id = {c.candidate_id: c for c in manifest.candidates}
    if len(by_id) != len(manifest.candidates) or not set(review.approved_candidate_ids).issubset(by_id):
        raise ValueError("Duplicate or unapproved candidate ID")
    selected = [by_id[key] for key in review.approved_candidate_ids]
    if len({c.event_id for c in selected}) != len(selected):
        raise ValueError("Multiple capacity candidates for one event")
    with closing(_connect(Path(db), readonly=True)) as conn:
        conn.execute("BEGIN")
        _check_integrity(conn)
        for candidate in selected:
            _validate_target(conn, candidate)
        fingerprint = manifest_hash(_snapshot(conn))
    plan = {"schema_version": "capacity-plan-v1", "source_database": str(Path(db).resolve()),
            "source_snapshot_sha256": fingerprint, "candidate_manifest": candidates,
            "approval": approval, "selected": [c.model_dump() for c in selected]}
    plan["approved_manifest_hash"] = manifest_hash(plan)
    return plan


def _validate_plan(plan: dict, approved_hash: str) -> tuple[list[Candidate], Approval]:
    expected_keys = {"schema_version", "source_database", "source_snapshot_sha256", "candidate_manifest", "approval", "selected", "approved_manifest_hash"}
    if set(plan) != expected_keys or plan["schema_version"] != "capacity-plan-v1":
        raise ValueError("Unsupported capacity plan")
    unsigned = {k: v for k, v in plan.items() if k != "approved_manifest_hash"}
    if manifest_hash(unsigned) != approved_hash or plan["approved_manifest_hash"] != approved_hash:
        raise ValueError("Exact approved plan hash required")
    review = Approval.model_validate(plan["approval"])
    manifest = Candidates.model_validate(plan["candidate_manifest"])
    if review.candidate_manifest_sha256 != manifest_hash(plan["candidate_manifest"]):
        raise ValueError("Candidate approval hash mismatch")
    by_id = {c.candidate_id: c for c in manifest.candidates}
    if len(by_id) != len(manifest.candidates) or not set(review.approved_candidate_ids).issubset(by_id):
        raise ValueError("Invalid approved IDs")
    selected = [by_id[key] for key in review.approved_candidate_ids]
    if [c.model_dump() for c in selected] != plan["selected"] or len({c.event_id for c in selected}) != len(selected):
        raise ValueError("Selected candidates differ from approval")
    return selected, review


def _already_applied(conn, selected, approved_hash):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (AUDIT_TABLE,)).fetchone():
        return False
    audits = conn.execute(f"SELECT event_id, after_json FROM {AUDIT_TABLE} WHERE approved_manifest_hash=?", (approved_hash,)).fetchall()
    if not audits:
        return False
    if {r["event_id"] for r in audits} != {c.event_id for c in selected}:
        raise ValueError("Partial application audit found")
    for row in audits:
        live = conn.execute("SELECT * FROM project_events WHERE id=?", (row["event_id"],)).fetchone()
        after = json.loads(row["after_json"])
        if live is None or any(live[k] != after[k] for k in CAPACITY_FIELDS):
            raise ValueError("Previously approved capacity has drifted")
    return True


def _backup(source: Path, destination: Path, expected_hash: str):
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive reservation prevents accidentally replacing a prior backup.
    with destination.open("xb"):
        pass
    with closing(_connect(source, readonly=True)) as src, closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)
    with closing(_connect(destination, readonly=True)) as copied:
        _check_integrity(copied)
        if manifest_hash(_snapshot(copied)) != expected_hash:
            raise ValueError("Verified backup differs from approved database")


def apply_plan(db: str | Path, plan: dict, approved_hash: str, backup_dir: str | Path,
               *, allow_production: bool = False, rehearsal: bool = False) -> dict:
    """Apply all selected fills atomically. Rehearsal only targets its marked copy."""
    selected, approval = _validate_plan(plan, approved_hash)
    db = Path(db).resolve(strict=True)
    if rehearsal:
        marker = db.parent / "capacity-rehearsal.json"
        if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")) != {
            "database": str(db), "approved_manifest_hash": approved_hash, "source_database": plan["source_database"]
        } or db == Path(plan["source_database"]).resolve():
            raise ValueError("Rehearsal requires an isolated marked copy")
    elif not allow_production or approval.approval_kind != "human" or db != Path(plan["source_database"]).resolve():
        raise ValueError("Production apply requires human approval and explicit production opt-in at approved path")
    with closing(_connect(db)) as conn:
        _check_integrity(conn)
        if _already_applied(conn, selected, approved_hash):
            return {"status": "already_applied", "updated": 0, "approved_manifest_hash": approved_hash}
        if manifest_hash(_snapshot(conn)) != plan["source_snapshot_sha256"]:
            raise ValueError("Database drift since reviewed plan")
        backup = Path(backup_dir).resolve() / "before-capacity.db"
        _backup(db, backup, plan["source_snapshot_sha256"])
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            before = _snapshot(conn)
            if manifest_hash(before) != plan["source_snapshot_sha256"]:
                raise ValueError("Database drift while obtaining write lock")
            for candidate in selected:
                _validate_target(conn, candidate)
            conn.execute(f"""CREATE TABLE IF NOT EXISTS {AUDIT_TABLE} (
                event_id TEXT PRIMARY KEY REFERENCES project_events(id), project_id TEXT NOT NULL,
                approved_manifest_hash TEXT NOT NULL, candidate_id TEXT NOT NULL,
                before_json TEXT NOT NULL, after_json TEXT NOT NULL, evidence_json TEXT NOT NULL,
                approval_json TEXT NOT NULL, approved_at TEXT NOT NULL, applied_at TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS capacity_review_conflicts (
                conflict_hash TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES project_events(id),
                approved_manifest_hash TEXT NOT NULL, incoming_json TEXT NOT NULL,
                preserved_json TEXT NOT NULL, observed_at TEXT NOT NULL)""")
            now = datetime.now(timezone.utc).isoformat()
            for candidate in selected:
                after = _after(candidate)
                conn.execute("UPDATE project_events SET capacity_mw=?, capacity_mw_raw=?, capacity_details=?, capacity_source=? WHERE id=? AND capacity_mw IS NULL",
                             tuple(after[k] for k in CAPACITY_FIELDS) + (candidate.event_id,))
                conn.execute(f"INSERT INTO {AUDIT_TABLE} VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (candidate.event_id, candidate.project_id, approved_hash, candidate.candidate_id,
                              _json(candidate.before), _json(after), _json(candidate.model_dump()),
                              _json(approval.model_dump()), approval.reviewed_at, now))
            after_snapshot = _snapshot(conn)
            expected = json.loads(_json(before))
            replacements = {c.event_id: _after(c) for c in selected}
            expected["project_events"]["rows"] = sorted(
                [replacements.get(r["id"], r) for r in expected["project_events"]["rows"]], key=_json)
            for table, contents in expected.items():
                if table in {"$schema", AUDIT_TABLE, "capacity_review_conflicts"}:
                    continue
                if after_snapshot.get(table) != contents:
                    raise ValueError(f"Unexpected database mutation: {table}")
            allowed_new = {AUDIT_TABLE, "capacity_review_conflicts"}
            if set(after_snapshot) - set(before) - allowed_new:
                raise ValueError("Unexpected table created during apply")
            old_schema = [row for row in after_snapshot["$schema"] if row["tbl_name"] not in allowed_new]
            expected_schema = [row for row in before["$schema"] if row["tbl_name"] not in allowed_new]
            if old_schema != expected_schema:
                raise ValueError("Unexpected schema mutation during apply")
            _check_integrity(conn)
        return {"status": "applied_rehearsal" if rehearsal else "applied", "updated": len(selected),
                "approved_manifest_hash": approved_hash, "backup": str(backup)}


def rehearse_plan(source_db: str | Path, plan: dict, output_dir: str | Path) -> dict:
    """Copy an unchanged source, apply privately, verify source remains unchanged."""
    _validate_plan(plan, plan["approved_manifest_hash"])
    source_db, output = Path(source_db).resolve(strict=True), Path(output_dir).resolve()
    if source_db != Path(plan["source_database"]).resolve():
        raise ValueError("Rehearsal source differs from plan")
    output.mkdir(parents=True, exist_ok=False)
    target = output / "rehearsal.db"
    _backup(source_db, target, plan["source_snapshot_sha256"])
    marker = {"database": str(target), "approved_manifest_hash": plan["approved_manifest_hash"], "source_database": plan["source_database"]}
    (output / "capacity-rehearsal.json").write_text(_json(marker), encoding="utf-8")
    result = apply_plan(target, plan, plan["approved_manifest_hash"], output / "backup", rehearsal=True)
    with closing(_connect(source_db, readonly=True)) as conn:
        if manifest_hash(_snapshot(conn)) != plan["source_snapshot_sha256"]:
            raise ValueError("Source changed during rehearsal")
    result["database"] = str(target)
    result["approval_kind"] = plan["approval"]["approval_kind"]
    (output / "receipt.json").write_text(_json(result), encoding="utf-8")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    for flag in ("db", "candidates", "approval", "out"):
        prepare.add_argument("--" + flag, required=True, type=Path)
    rehearse = sub.add_parser("rehearse")
    for flag in ("db", "plan", "out"):
        rehearse.add_argument("--" + flag, required=True, type=Path)
    apply = sub.add_parser("apply")
    for flag in ("db", "plan", "backup-dir"):
        apply.add_argument("--" + flag, required=True, type=Path)
    apply.add_argument("--approved-hash", required=True)
    apply.add_argument("--allow-production", action="store_true")
    args = parser.parse_args(argv)
    read = lambda path: json.loads(path.read_text(encoding="utf-8-sig"))
    if args.command == "prepare":
        result = prepare_plan(args.db, read(args.candidates), read(args.approval))
        with args.out.open("x", encoding="utf-8") as out:
            out.write(json.dumps(result, ensure_ascii=False, indent=2))
        print(result["approved_manifest_hash"])
    elif args.command == "rehearse":
        print(_json(rehearse_plan(args.db, read(args.plan), args.out)))
    else:
        print(_json(apply_plan(args.db, read(args.plan), args.approved_hash, args.backup_dir,
                               allow_production=args.allow_production)))


if __name__ == "__main__":
    main()
