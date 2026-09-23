"""Restore only the 13 historical projects affected by the 2026-09-17 entity gate.

Default is read-only. --apply accepts isolated copies; the exact production path
additionally requires --allow-production after reviewing the dry-run.
Only excluded/excluded_reason are restored, with all other data verified unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPAIR_OUTPUT_ROOT = Path(__file__).resolve().parents[2] / "output" / "entity-identity-repair"
TARGET_IDS = frozenset({
    "EGY-HB-0001", "AZE-PV-0001", "SGP-TH-0002", "ZMB-PV-0011", "SAU-ES-0002",
    "ARE-HB-0002", "ZAF-HB-0004", "TZA-HY-0002", "SAU-TH-0025", "UZB-BM-0004",
    "ZMB-PV-0024", "KGZ-PV-0012", "UNK-PV-0009",
})
GATE_REASONS = frozenset({
    "中企名称未命中受控登记表，待人工复核",
    "没有通过目标项目—具体企业—参与角色证据绑定的中企关系",
})
PLACEHOLDERS = {"", "Unknown", "unknown", "未具名中企", "N/A"}


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def connect_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def database_fingerprint(connection: sqlite3.Connection, mutable_ids: set[str] | frozenset[str] = frozenset()) -> dict:
    """Hash every table, IDs and schema; mask only the two authorized cells per target."""
    result = {}
    schema = [tuple(row) for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
    )]
    result["schema_sha256"] = hashlib.sha256(json.dumps(schema, ensure_ascii=False).encode()).hexdigest()
    for name, in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        cursor = connection.execute(f"SELECT * FROM {_quoted(name)}")
        columns = [col[0] for col in cursor.description]
        rows, ids = [], []
        for raw in cursor:
            row = dict(zip(columns, raw))
            if "id" in row:
                ids.append(row["id"])
            if name == "canonical_projects" and row["id"] in mutable_ids:
                row["excluded"] = "AUTHORIZED_CHANGE"
                row["excluded_reason"] = "AUTHORIZED_CHANGE"
            rows.append(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                   default=lambda value: {"blob_hex": value.hex()}))
        result[name] = {
            "rows": len(rows),
            "data_sha256": hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest(),
            "id_sha256": hashlib.sha256(json.dumps(sorted(ids, key=str), ensure_ascii=False).encode()).hexdigest(),
        }
    return result


def build_plan(current: sqlite3.Connection, baseline: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """Require historical inclusion, exact project identity and surviving evidence IDs."""
    candidates, skipped = [], []
    for canonical_id in sorted(TARGET_IDS):
        now = current.execute("SELECT * FROM canonical_projects WHERE id=?", (canonical_id,)).fetchone()
        before = baseline.execute("SELECT * FROM canonical_projects WHERE id=?", (canonical_id,)).fetchone()
        reason = None
        if now is None or before is None:
            reason = "missing_in_current_or_baseline"
        elif now["excluded"] != 1:
            reason = "already_included_or_not_excluded"
        elif before["excluded"] not in (None, 0):
            reason = "baseline_already_excluded"
        elif now["excluded_reason"] not in GATE_REASONS:
            reason = "not_exact_legacy_entity_gate_reason"
        elif not before["project_name"] or now["project_name"] != before["project_name"]:
            reason = "project_name_changed"
        if reason:
            skipped.append({"canonical_id": canonical_id, "reason": reason})
            continue
        baseline_events = {row[0] for row in baseline.execute(
            "SELECT id FROM project_events WHERE canonical_id=?", (canonical_id,)
        )}
        current_events = {row[0] for row in current.execute(
            "SELECT id FROM project_events WHERE canonical_id=?", (canonical_id,)
        )}
        evidence = [dict(row) for row in baseline.execute(
            "SELECT pe.id AS event_id, ce.implementing_entity, ce.subsidiary_name, ce.involvement_type "
            "FROM project_events pe JOIN chinese_entities ce ON ce.event_id=pe.id "
            "WHERE pe.canonical_id=? ORDER BY pe.id, ce.id", (canonical_id,)
        ) if str(row["implementing_entity"] or "").strip() not in PLACEHOLDERS
             and row["event_id"] in current_events]
        if not evidence or not baseline_events.issubset(current_events):
            skipped.append({"canonical_id": canonical_id, "reason": "historical_participation_or_events_missing"})
            continue
        candidates.append({
            "canonical_id": canonical_id, "project_name": now["project_name"],
            "before_excluded": now["excluded"], "before_reason": now["excluded_reason"],
            "restore_excluded": before["excluded"], "restore_reason": before["excluded_reason"],
            "baseline_event_ids": sorted(baseline_events), "baseline_participation": evidence,
        })
    return candidates, skipped


def _validate_paths(db: Path, baseline: Path, out: Path, apply: bool,
                    allow_production: bool = False) -> None:
    if not db.is_file() or not baseline.is_file():
        raise ValueError("Current database and baseline must both exist")
    if db == baseline or db.samefile(baseline):
        raise ValueError("Current database and baseline must be different files")
    if out in (db, baseline) or out.exists():
        raise FileExistsError("Report cannot overwrite an input or existing file")
    production = Path(__file__).resolve().parents[2] / "s02a_duplicate_merger/data/db/bri_re_database.db"
    if apply and not db.is_relative_to(REPAIR_OUTPUT_ROOT.resolve()) and not (
        allow_production and db == production.resolve()
    ):
        raise ValueError("--apply only accepts isolated copies under output/entity-identity-repair")
    for suffix in (".log", ".before.sqlite"):
        destination = out.with_suffix(suffix)
        if destination in (db, baseline) or destination.exists():
            raise FileExistsError(f"Refusing to overwrite audit artifact: {destination}")


def run_repair(db: Path, baseline: Path, out: Path, *, apply: bool = False,
               allow_production: bool = False) -> dict[str, Any]:
    db, baseline, out = db.resolve(), baseline.resolve(), out.resolve()
    _validate_paths(db, baseline, out, apply, allow_production)
    out.parent.mkdir(parents=True, exist_ok=True)
    log_path = out.with_suffix(".log")
    handler = logging.FileHandler(log_path, mode="x", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger(f"entity_gate_repair.{out.stem}")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    source_before = {"current": file_hash(db), "baseline": file_hash(baseline)}
    current = connect_readonly(db)
    original = connect_readonly(baseline)
    try:
        candidates, skipped = build_plan(current, original)
        full_before = database_fingerprint(current)
        candidate_ids = {row["canonical_id"] for row in candidates}
        immutable_before = database_fingerprint(current, candidate_ids)
        baseline_before = database_fingerprint(original)
        report = {
            "operation": "historical_entity_gate_exclusion_repair", "mode": "apply" if apply else "dry_run",
            "created_at": datetime.now(timezone.utc).isoformat(), "db": str(db), "baseline": str(baseline),
            "candidate_count": len(candidates), "candidates": candidates, "skipped": skipped,
            "source_hashes_before": source_before, "before_fingerprint": full_before,
            "changed_rows": 0, "backup": None, "log": str(log_path),
        }
        logger.info("mode=%s candidate_count=%s db=%s baseline=%s", report["mode"], len(candidates), db, baseline)
        if apply and candidates:
            backup_path = out.with_suffix(".before.sqlite")
            with backup_path.open("xb"):
                pass
            backup_connection = sqlite3.connect(backup_path)
            try:
                current.backup(backup_connection)
            finally:
                backup_connection.close()
            backup_check = connect_readonly(backup_path)
            try:
                if database_fingerprint(backup_check) != full_before:
                    raise RuntimeError("Backup does not match planned database")
            finally:
                backup_check.close()
            report["backup"] = str(backup_path)
            report["backup_sha256"] = file_hash(backup_path)
            write = sqlite3.connect(db.as_uri() + "?mode=rw", uri=True)
            write.row_factory = sqlite3.Row
            try:
                write.execute("BEGIN IMMEDIATE")
                if database_fingerprint(write) != full_before:
                    raise RuntimeError("Database changed after planning; refusing stale repair")
                for candidate in candidates:
                    cursor = write.execute(
                        "UPDATE canonical_projects SET excluded=?, excluded_reason=? "
                        "WHERE id=? AND project_name=? AND excluded=1 AND excluded_reason=?",
                        (candidate["restore_excluded"], candidate["restore_reason"], candidate["canonical_id"],
                         candidate["project_name"], candidate["before_reason"]),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError(f"Exact old-state check failed: {candidate['canonical_id']}")
                    report["changed_rows"] += cursor.rowcount
                    logger.info("restore %s excluded=1 -> %s", candidate["canonical_id"], candidate["restore_excluded"])
                if database_fingerprint(write, candidate_ids) != immutable_before:
                    raise RuntimeError("Non-target data, schema or ID sets changed")
                integrity = [row[0] for row in write.execute("PRAGMA integrity_check")]
                foreign_keys = [tuple(row) for row in write.execute("PRAGMA foreign_key_check")]
                if integrity != ["ok"] or foreign_keys:
                    raise RuntimeError(f"Database validation failed: {integrity}, {foreign_keys}")
                for candidate in candidates:
                    restored = write.execute("SELECT excluded,excluded_reason FROM canonical_projects WHERE id=?",
                                             (candidate["canonical_id"],)).fetchone()
                    if tuple(restored) != (candidate["restore_excluded"], candidate["restore_reason"]):
                        raise RuntimeError("Written state does not match baseline")
                write.commit()
                report["integrity_check"] = integrity
                report["foreign_key_check"] = foreign_keys
            except (sqlite3.Error, RuntimeError):
                write.rollback()
                raise
            finally:
                write.close()
        source_after = {"current": file_hash(db), "baseline": file_hash(baseline)}
        if source_after["baseline"] != source_before["baseline"] or database_fingerprint(original) != baseline_before:
            raise RuntimeError("Baseline changed during repair")
        if not apply and source_after != source_before:
            raise RuntimeError("Read-only source hash changed")
        after_check = connect_readonly(db)
        try:
            if database_fingerprint(after_check, candidate_ids) != immutable_before:
                raise RuntimeError("Post-run immutable data verification failed")
            report["after_fingerprint"] = database_fingerprint(after_check)
        finally:
            after_check.close()
        report["source_hashes_after"] = source_after
        report["non_target_data_unchanged"] = True
        report["baseline_unchanged"] = True
        with out.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        logger.info("completed changed_rows=%s immutable_data_verified=true", report["changed_rows"])
        return report
    except (sqlite3.Error, OSError, ValueError, RuntimeError):
        logger.exception("Repair failed")
        raise
    finally:
        current.close()
        original.close()
        logger.removeHandler(handler)
        handler.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-production", action="store_true")
    args = parser.parse_args()
    report = run_repair(args.db, args.baseline, args.out, apply=args.apply,
                        allow_production=args.allow_production)
    print(json.dumps({"mode": report["mode"], "candidate_count": report["candidate_count"],
                      "changed_rows": report["changed_rows"], "report": str(args.out.resolve())}))


if __name__ == "__main__":
    main()
