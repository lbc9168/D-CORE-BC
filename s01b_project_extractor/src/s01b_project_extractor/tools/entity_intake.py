"""Export one entity intake queue and re-resolve stored evidence after registry review.

No model calls and no registry writes. Refresh defaults to dry-run; legacy rows without
original extraction evidence require re-extraction rather than fabricated recovery.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from s01b_project_extractor.prompts.policy_fragments import ENTITY_POLICY_VERSION
from s01b_project_extractor.stages.orchestrator import _apply_entity_publication_gate
from s01b_project_extractor.stages.s4_entity import (
    enforce_entity_role_evidence, normalize_and_verify_entities,
)

logger = logging.getLogger(__name__)
_UPDATE_COLUMNS = (
    "implementing_entity", "subsidiary_name", "involvement_type", "entity_id",
    "entity_name", "group_entity_id", "raw_entity_name", "identity_status",
    "entity_evidence_json", "publication_status", "excluded", "exclude_reason",
    "exclude_source", "needs_review", "imported_to_s02a",
)
_OPTIONAL_UPDATE_COLUMNS = ("implementing_entity_en", "subsidiary_name_en", "is_central_soe")


def _fingerprint(path: Path) -> dict[str, str]:
    """Hash both SQLite base and WAL so read-only checks include committed WAL data."""
    result = {}
    for item in (path, Path(str(path) + "-wal")):
        if item.exists():
            # SQLite may create a zero-byte WAL on a read-only open of a WAL-mode
            # database. It contains no pages; absence and emptiness are equivalent.
            if item != path and item.stat().st_size == 0:
                continue
            with item.open("rb") as handle:
                digest = hashlib.sha256()
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                result[item.name] = digest.hexdigest()
    return result


def _readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _rows(path: Path) -> list[dict]:
    connection = _readonly(path)
    try:
        return [dict(row) for row in connection.execute("SELECT * FROM extracted_projects ORDER BY id")]
    finally:
        connection.close()


def _payload(row: dict) -> dict:
    value = row.get("entity_evidence_json")
    if not value:
        return {}
    try:
        result = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        logger.warning("Invalid stored entity evidence: row=%s error=%s", row["id"], exc)
        return {}
    return result if isinstance(result, dict) else {}


def _scope_excluded(row: dict) -> bool:
    # Only the specific identity gate may be undone. Unknown/manual exclusions stay protected.
    return bool(row.get("excluded")) and row.get("exclude_source") != "entity_verification"


def _raw_name(row: dict, payload: dict) -> str:
    entity = payload.get("entity")
    entity = entity if isinstance(entity, dict) else {}
    return str(row.get("raw_entity_name") or entity.get("_entity_raw_name")
               or row.get("subsidiary_name") or row.get("implementing_entity") or "Unknown").strip()


def export_intake(db_path: str | Path, out_path: str | Path) -> dict:
    """Read-only, grouped queue including legacy evidence gaps; never overwrites output."""
    database, output = Path(db_path).resolve(), Path(out_path).resolve()
    if output in {database, *(Path(str(database) + suffix) for suffix in ("-wal", "-shm", "-journal"))}:
        raise ValueError("Output must not overwrite the source database")
    before = _fingerprint(database)
    rows = _rows(database)
    grouped: dict[str, dict] = {}
    skipped_scope = 0
    for row in rows:
        if _scope_excluded(row):
            skipped_scope += 1
            continue
        payload = _payload(row)
        entity = payload.get("entity")
        legacy = (not isinstance(entity, dict) or bool(entity.get("_requires_reextraction"))
                  or not (entity.get("Participation Assessment") or entity.get("Participation Evidence")))
        pending = row.get("publication_status") == "pending_review"
        review_signal = pending or bool(row.get("needs_review")) or (
            bool(row.get("excluded")) and row.get("exclude_source") == "entity_verification"
        )
        # Historical imported rows predate structured identity metadata. Its absence alone
        # is not a new issue and must not flood every incremental intake with the old corpus.
        if not isinstance(entity, dict) and row.get("imported_to_s02a") and not review_signal:
            continue
        if not review_signal and row.get("entity_id") and not legacy:
            continue
        if row.get("identity_status") == "verified_non_chinese":
            continue
        name = _raw_name(row, payload)
        group = grouped.setdefault(name, {"raw_name": name, "row_ids": [], "records": []})
        group["row_ids"].append(row["id"])
        group["records"].append({
            "row_id": row["id"], "project_name": row.get("project_name"),
            "source_link": row.get("source_link"), "entity_id": row.get("entity_id"),
            "identity_status": row.get("identity_status"),
            "publication_status": row.get("publication_status"),
            "action": ("standardize_identity" if row.get("publication_status") == "eligible"
                       and not row.get("entity_id") else
                       "requires_reextraction" if legacy else "review_participation_or_identity"),
            "evidence": payload,
        })
    if _fingerprint(database) != before:
        raise RuntimeError("Source database changed during read-only intake export")
    report = {"policy_version": ENTITY_POLICY_VERSION, "source_db": str(database),
              "source_fingerprint": before, "source_row_count": len(rows),
              "candidate_count": len(grouped), "skipped_scope_exclusions": skipped_scope,
              "entities": sorted(grouped.values(), key=lambda item: item["raw_name"])}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return report


def _source_map(source_db: Path | None) -> dict[str, tuple[str, str]]:
    if source_db is None:
        return {}
    connection = _readonly(source_db)
    result = {}
    try:
        for row in connection.execute("SELECT url, title, content FROM raw_news"):
            value = (str(row["title"] or ""), str(row["content"] or ""))
            if row["url"] in result and result[row["url"]] != value:
                raise ValueError(f"Conflicting original articles for URL: {row['url']}")
            result[row["url"]] = value
    finally:
        connection.close()
    return result


def _plan_row(row: dict, sources: dict) -> tuple[dict | None, str]:
    if _scope_excluded(row):
        return None, "protected_scope_exclusion"
    payload = _payload(row)
    original = payload.get("entity")
    if not isinstance(original, dict) or not (original.get("Participation Assessment") or original.get("Participation Evidence")):
        return None, "requires_reextraction"
    name = _raw_name(row, payload)
    if name in ("", "Unknown"):
        return None, "requires_reextraction"
    source = payload.get("source")
    if isinstance(source, dict) and isinstance(source.get("title"), str) and isinstance(source.get("content"), str):
        title, content = source["title"], source["content"]
    elif row.get("source_link") in sources:
        title, content = sources[row["source_link"]]
    else:
        return None, "requires_reextraction"
    entity = copy.deepcopy(original)
    entity["Implementing Chinese Entity"] = name
    enforce_entity_role_evidence([entity], title, content, row.get("project_name", ""))
    normalize_and_verify_entities([entity], title, content)
    if entity.get("_requires_reextraction"):
        return None, "requires_reextraction"
    project = {"chinese_entities": [entity]}
    _apply_entity_publication_gate(project)
    status = project["publication_status"]
    if row.get("imported_to_s02a") and status != "eligible":
        # Refresh cannot retract existing downstream facts. Surface this for explicit review.
        return None, "requires_downstream_review"
    identity = entity["_entity_verification_status"]
    if identity == "unmatched_needs_review" and entity.get("_identity_evidence_status") == "verified":
        identity = "source_chinese_pending_registry"
    refreshed = copy.deepcopy(payload)
    refreshed["policy_version"] = ENTITY_POLICY_VERSION
    refreshed["entity"] = entity
    candidates = refreshed.get("candidates", [])
    if isinstance(candidates, list):
        refreshed["candidates"] = [entity if candidate == original else candidate for candidate in candidates]
    updates = {
        "implementing_entity": entity["Implementing Chinese Entity"],
        "subsidiary_name": entity["Subsidiary Name"],
        "involvement_type": entity["Type of involvement of Chinese Entities"],
        "entity_id": entity.get("_entity_id"), "entity_name": entity.get("_entity_name") or name,
        "group_entity_id": entity.get("_group_entity_id"), "raw_entity_name": name,
        "identity_status": identity,
        "entity_evidence_json": json.dumps(refreshed, ensure_ascii=False),
        "publication_status": status, "excluded": int(status != "eligible"),
        "exclude_reason": project.get("entity_review_reason") if status != "eligible" else None,
        "exclude_source": "entity_verification" if status != "eligible" else None,
        "needs_review": int(bool(project.get("needs_review"))),
        "imported_to_s02a": row.get("imported_to_s02a", 0),
    }
    # Translations/central-SOE labels belong to the corresponding Chinese identity.
    # A registry re-link cannot safely retain a label translated for the old company.
    for field in _OPTIONAL_UPDATE_COLUMNS:
        if field in row:
            updates[field] = row[field]
    if row.get("implementing_entity") != updates["implementing_entity"]:
        for field in ("implementing_entity_en", "is_central_soe"):
            if field in row:
                updates[field] = None
    if row.get("subsidiary_name") != updates["subsidiary_name"] and "subsidiary_name_en" in row:
        updates["subsidiary_name_en"] = None
    if row.get("imported_to_s02a"):
        # Downstream upserts cannot retract an existing role/identity relation. Only
        # first-time null→ID linking is supported by the replay bridge; other changes
        # need a coordinated downstream correction instead of creating a second fact.
        changed_role = row.get("involvement_type") != updates["involvement_type"]
        changed_existing_identity = bool(row.get("entity_id")) and any(
            row.get(field) != updates[field] for field in (
                "entity_id", "implementing_entity", "subsidiary_name", "entity_name", "group_entity_id",
            )
        )
        if changed_role or changed_existing_identity:
            return None, "requires_downstream_review"
    # Replaying an already imported row updates the same source row, never creates a new ID.
    if any(row.get(key) != value for key, value in updates.items() if key != "imported_to_s02a"):
        updates["imported_to_s02a"] = 0
        return updates, "refresh"
    return None, "unchanged"


def refresh_entities(db_path: str | Path, *, source_db: str | Path | None = None,
                     apply: bool = False, backup_path: str | Path | None = None,
                     log_path: str | Path | None = None,
                     row_ids: list[int] | None = None) -> dict:
    """Revalidate original evidence and resolve reviewed names; dry-run by default.

    Apply uses a non-overwriting SQLite backup and one transaction. Project and row IDs,
    developer, finance, and unrelated fields are untouched. No missing evidence is inferred.
    """
    database = Path(db_path).resolve()
    original_db = Path(source_db).resolve() if source_db is not None else None
    before = _fingerprint(database)
    source_before = _fingerprint(original_db) if original_db else None
    rows, sources = _rows(database), _source_map(original_db)
    ids = [row["id"] for row in rows]
    selected = set(row_ids) if row_ids is not None else set(ids)
    if not selected <= set(ids):
        raise ValueError("Requested row IDs do not exist")
    plans, results = [], []
    for row in rows:
        if row["id"] not in selected:
            continue
        updates, action = _plan_row(row, sources)
        results.append({"row_id": row["id"], "raw_name": _raw_name(row, _payload(row)),
                        "action": action, "before_status": row.get("publication_status"),
                        "after_status": updates["publication_status"] if updates else row.get("publication_status")})
        if updates:
            plans.append((row, updates))
    if _fingerprint(database) != before or (original_db and _fingerprint(original_db) != source_before):
        raise RuntimeError("Source database changed during refresh planning")
    report = {"policy_version": ENTITY_POLICY_VERSION, "apply": apply, "source_db": str(database),
              "source_fingerprint": before, "row_count": len(rows), "changed_rows": len(plans),
              "rows": results, "backup": None}
    if not apply or not plans:
        return report
    missing = set(_UPDATE_COLUMNS) - set(rows[0])
    if missing:
        raise ValueError(f"Extraction schema needs migration before apply: {sorted(missing)}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = Path(backup_path).resolve() if backup_path else database.with_name(f"{database.stem}.entity-refresh-{stamp}.bak.db")
    logfile = Path(log_path).resolve() if log_path else database.parent / "logs" / f"entity-refresh-{stamp}.log"
    protected = {path for source in (database, original_db) if source is not None
                 for path in (source, *(Path(str(source) + suffix) for suffix in ("-wal", "-shm", "-journal")))}
    if backup in protected or logfile in protected or logfile == backup:
        raise ValueError("Backup and log paths must be separate from source databases")
    backup.parent.mkdir(parents=True, exist_ok=True)
    with backup.open("xb"):
        pass
    source_connection = _readonly(database)
    backup_connection = sqlite3.connect(backup)
    try:
        source_connection.backup(backup_connection)
    finally:
        backup_connection.close()
        source_connection.close()
    if _rows(backup) != rows or _fingerprint(database) != before:
        raise RuntimeError("Backup verification failed or source changed; no refresh applied")
    logfile.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(logfile, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.INFO)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN IMMEDIATE")
        current = [dict(row) for row in connection.execute("SELECT * FROM extracted_projects ORDER BY id")]
        if current != rows:
            raise RuntimeError("Rows changed after refresh planning")
        logger.info("Entity refresh starting rows=%d backup=%s", len(plans), backup)
        update_columns = _UPDATE_COLUMNS + tuple(field for field in _OPTIONAL_UPDATE_COLUMNS if field in rows[0])
        columns = ", ".join(f"{key}=?" for key in update_columns)
        connection.executemany(f"UPDATE extracted_projects SET {columns} WHERE id=?", [
            tuple(updates[key] for key in update_columns) + (row["id"],) for row, updates in plans
        ])
        after = [dict(row) for row in connection.execute("SELECT * FROM extracted_projects ORDER BY id")]
        if [row["id"] for row in after] != ids:
            raise RuntimeError("Refresh changed row count or IDs")
        expected = {row["id"]: row for row in rows}
        expected.update({row["id"]: dict(row, **updates) for row, updates in plans})
        for row in after:
            if row != expected[row["id"]]:
                raise RuntimeError(f"Refresh result verification failed: row={row['id']}")
        connection.commit()
        logger.info("Entity refresh committed unchanged_row_ids=%d updated_ids=%s", len(ids), [row["id"] for row, _ in plans])
    except (sqlite3.Error, RuntimeError, ValueError):
        connection.rollback()
        logger.exception("Entity refresh rolled back")
        raise
    finally:
        connection.close()
        logger.removeHandler(handler)
        handler.close()
        logger.setLevel(old_level)
    report.update(backup=str(backup), log=str(logfile), result_fingerprint=_fingerprint(database))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--db", required=True)
    export.add_argument("--out", required=True)
    refresh = commands.add_parser("refresh")
    refresh.add_argument("--db", required=True)
    refresh.add_argument("--source-db")
    refresh.add_argument("--row-id", type=int, action="append")
    refresh.add_argument("--apply", action="store_true")
    refresh.add_argument("--backup")
    refresh.add_argument("--log")
    args = parser.parse_args()
    if args.command == "export":
        report = export_intake(args.db, args.out)
        print(json.dumps({"output": str(Path(args.out).resolve()), "candidate_count": report["candidate_count"]}, ensure_ascii=False))
    else:
        print(json.dumps(refresh_entities(args.db, source_db=args.source_db, apply=args.apply,
                                         backup_path=args.backup, log_path=args.log, row_ids=args.row_id), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
