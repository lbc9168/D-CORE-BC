"""Audit, verify, and safely promote ignored SQLite databases across Git worktrees."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
import bri_enums  # noqa: E402


LOGGER = logging.getLogger("database_worktree_guard")
STATE_SCHEMA_VERSION = "database-state-v1"
CORE_TABLES = ("canonical_projects", "project_events", "chinese_entities")
REQUIRED_COLUMNS = {
    "canonical_projects": {"id", "category", "technology_tags_json"},
    "project_events": {
        "id",
        "canonical_id",
        "generation_sources_json",
        "storage_status",
        "storage_power_mw",
        "storage_energy_mwh",
    },
    "project_classification_shadow": {
        "canonical_id",
        "technology_tags_json",
        "classification_version",
        "needs_review",
    },
}


def _quote_identifier(value: str) -> str:
    """Quote one SQLite identifier without accepting executable SQL fragments."""
    return '"' + value.replace('"', '""') + '"'


def _encoded_value(value: Any) -> list[Any]:
    """Encode a SQLite scalar with an explicit type marker for stable hashing."""
    if value is None:
        return ["null", None]
    if isinstance(value, bytes):
        return ["blob", base64.b64encode(value).decode("ascii")]
    if isinstance(value, float):
        return ["float", repr(value)]
    if isinstance(value, int):
        return ["int", value]
    return ["text", str(value)]


def _open_readonly(path: Path) -> sqlite3.Connection:
    """Open an existing SQLite database in immutable read-only query mode."""
    resolved = path.resolve(strict=True)
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    """Return declared column names for one SQLite table in schema order."""
    return [str(row[1]) for row in connection.execute(f"PRAGMA table_info({_quote_identifier(table)})")]


def _logical_sha256(connection: sqlite3.Connection) -> str:
    """Hash schema and ordered typed rows while ignoring SQLite physical page layout."""
    digest = hashlib.sha256()
    objects = list(
        connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL ORDER BY type,name"
        )
    )
    digest.update(json.dumps(objects, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    tables = [row[1] for row in objects if row[0] == "table"]
    for table in sorted(tables):
        table_info = list(connection.execute(f"PRAGMA table_info({_quote_identifier(table)})"))
        columns = [str(row[1]) for row in table_info]
        primary_key = [
            str(row[1])
            for row in sorted((row for row in table_info if int(row[5]) > 0), key=lambda row: int(row[5]))
        ]
        order_columns = primary_key or columns
        select_sql = "SELECT " + ",".join(_quote_identifier(column) for column in columns)
        select_sql += " FROM " + _quote_identifier(table)
        if order_columns:
            select_sql += " ORDER BY " + ",".join(_quote_identifier(column) for column in order_columns)
        digest.update(f"\nTABLE:{table}\n".encode("utf-8"))
        for row in connection.execute(select_sql):
            payload = [_encoded_value(value) for value in row]
            digest.update(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    """Return the physical file SHA-256 for diagnostics without using it as identity."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_database(path: Path) -> dict[str, Any]:
    """Inspect database health, logical identity, core counts, schema, and taxonomy state."""
    resolved = path.resolve(strict=True)
    connection = _open_readonly(resolved)
    try:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {_quote_identifier(table)}").fetchone()[0]
            for table in CORE_TABLES
            if table in tables
        }
        missing_schema = {
            table: sorted(columns - set(_table_columns(connection, table)))
            for table, columns in REQUIRED_COLUMNS.items()
            if table not in tables or columns - set(_table_columns(connection, table))
        }
        taxonomy: dict[str, Any] = {}
        if "project_classification_shadow" in tables:
            taxonomy.update(
                {
                    "shadow_rows": connection.execute(
                        "SELECT COUNT(*) FROM project_classification_shadow"
                    ).fetchone()[0],
                    "shadow_resolved": connection.execute(
                        "SELECT COUNT(*) FROM project_classification_shadow WHERE needs_review=0"
                    ).fetchone()[0],
                    "shadow_needs_review": connection.execute(
                        "SELECT COUNT(*) FROM project_classification_shadow WHERE needs_review=1"
                    ).fetchone()[0],
                    "classification_versions": dict(
                        connection.execute(
                            "SELECT classification_version,COUNT(*) FROM project_classification_shadow "
                            "GROUP BY classification_version ORDER BY classification_version"
                        )
                    ),
                }
            )
        if "canonical_projects" in tables and "technology_tags_json" in _table_columns(
            connection, "canonical_projects"
        ):
            tag_counts: dict[str, int] = {}
            tagged_projects = 0
            malformed_tags = 0
            invalid_tag_rows = 0
            for _project_id, category, raw_tags in connection.execute(
                "SELECT id,category,technology_tags_json FROM canonical_projects ORDER BY id"
            ):
                try:
                    tags = json.loads(raw_tags or "[]")
                except (TypeError, json.JSONDecodeError):
                    malformed_tags += 1
                    continue
                if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
                    malformed_tags += 1
                    continue
                tagged_projects += int(bool(tags))
                if any(
                    tag not in bri_enums.valid_technology_tags()
                    or not bri_enums.technology_tag_allowed(category, tag)
                    for tag in tags
                ):
                    invalid_tag_rows += 1
                for tag in tags:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
            taxonomy.update(
                {
                    "tagged_projects": tagged_projects,
                    "technology_tag_counts": dict(sorted(tag_counts.items())),
                    "malformed_tag_rows": malformed_tags,
                    "invalid_tag_rows": invalid_tag_rows,
                }
            )
        return {
            "path": str(resolved),
            "physical_sha256": _file_sha256(resolved),
            "logical_sha256": _logical_sha256(connection),
            "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_errors": [list(row) for row in connection.execute("PRAGMA foreign_key_check")],
            "counts": counts,
            "missing_schema": missing_schema,
            "taxonomy": taxonomy,
        }
    finally:
        connection.close()


def state_from_report(report: dict[str, Any], database_path: str) -> dict[str, Any]:
    """Build the tracked stable-state contract from a verified database report."""
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "database_path": database_path.replace("\\", "/"),
        "logical_sha256": report["logical_sha256"],
        "counts": report["counts"],
        "missing_schema": report["missing_schema"],
        "taxonomy": report["taxonomy"],
    }


def _health_errors(report: dict[str, Any]) -> list[str]:
    """Return fail-closed health errors from one inspection report."""
    errors = []
    if report["integrity"] != "ok":
        errors.append(f"integrity={report['integrity']}")
    if report["foreign_key_errors"]:
        errors.append(f"foreign_key_errors={len(report['foreign_key_errors'])}")
    if report["missing_schema"]:
        errors.append(f"missing_schema={report['missing_schema']}")
    missing_tables = sorted(set(CORE_TABLES) - set(report["counts"]))
    if missing_tables:
        errors.append(f"missing_core_tables={missing_tables}")
    if report["taxonomy"].get("malformed_tag_rows"):
        errors.append(f"malformed_tag_rows={report['taxonomy']['malformed_tag_rows']}")
    if report["taxonomy"].get("invalid_tag_rows"):
        errors.append(f"invalid_tag_rows={report['taxonomy']['invalid_tag_rows']}")
    return errors


def verify_database(path: Path, manifest_path: Path) -> dict[str, Any]:
    """Verify a database against the tracked logical state manifest and health gates."""
    report = inspect_database(path)
    manifest = json.loads(manifest_path.resolve(strict=True).read_text(encoding="utf-8"))
    if manifest.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError(f"unsupported manifest schema: {manifest.get('schema_version')}")
    current = state_from_report(report, str(manifest.get("database_path") or ""))
    mismatches = {
        key: {"expected": manifest.get(key), "actual": current.get(key)}
        for key in ("logical_sha256", "counts", "missing_schema", "taxonomy")
        if manifest.get(key) != current.get(key)
    }
    health_errors = _health_errors(report)
    if health_errors or mismatches:
        raise RuntimeError(json.dumps({"health_errors": health_errors, "mismatches": mismatches}, ensure_ascii=False))
    return report


def _sqlite_backup(source_path: Path, destination_path: Path, *, allow_existing: bool = False) -> None:
    """Copy a consistent SQLite snapshot, optionally replacing an existing database transactionally."""
    if destination_path.exists() and not allow_existing:
        raise FileExistsError(destination_path)
    source = sqlite3.connect(f"file:{source_path.resolve(strict=True).as_posix()}?mode=ro", uri=True)
    destination = sqlite3.connect(str(destination_path))
    try:
        source.backup(destination)
    finally:
        source.close()
        destination.close()


def promote_database(candidate_path: Path, stable_path: Path, confirmation: str) -> dict[str, Any]:
    """Backup and transactionally replace the stable database after identity and health checks."""
    candidate = candidate_path.resolve(strict=True)
    stable = stable_path.resolve(strict=True)
    if candidate == stable:
        raise ValueError("candidate and stable database paths must differ")
    candidate_report = inspect_database(candidate)
    health_errors = _health_errors(candidate_report)
    if health_errors:
        raise RuntimeError(f"candidate health gate failed: {health_errors}")
    if confirmation.lower() != candidate_report["logical_sha256"].lower():
        raise ValueError("confirmation does not match candidate logical_sha256")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    backup = stable.with_name(f"{stable.name}.bak.pre-promotion.{stamp}")
    staging = stable.with_name(f".{stable.name}.promoting.{stamp}")
    receipt = stable.with_name(f"{stable.name}.promotion.{stamp}.json")
    before_report = inspect_database(stable)
    _sqlite_backup(stable, backup)
    _sqlite_backup(candidate, staging)
    staging_report = inspect_database(staging)
    if staging_report["logical_sha256"] != candidate_report["logical_sha256"]:
        staging.unlink(missing_ok=True)
        raise RuntimeError("staging logical fingerprint differs from candidate")

    write_started = False
    try:
        write_started = True
        _sqlite_backup(staging, stable, allow_existing=True)
        after_report = inspect_database(stable)
        if after_report["logical_sha256"] != candidate_report["logical_sha256"]:
            raise RuntimeError("promoted stable database differs from candidate")
        health_errors = _health_errors(after_report)
        if health_errors:
            raise RuntimeError(f"promoted stable health gate failed: {health_errors}")
    except (OSError, RuntimeError, sqlite3.Error):
        LOGGER.exception("Database promotion failed; restoring the verified pre-promotion backup")
        if write_started:
            _sqlite_backup(backup, stable, allow_existing=True)
        staging.unlink(missing_ok=True)
        raise
    staging.unlink(missing_ok=True)

    payload = {
        "schema_version": "database-promotion-receipt-v1",
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "candidate": candidate_report,
        "stable_before": before_report,
        "stable_after": after_report,
        "backup": str(backup),
    }
    receipt.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"backup": str(backup), "receipt": str(receipt), "stable": after_report}


def audit_worktree_database(candidate_path: Path, stable_path: Path) -> dict[str, Any]:
    """Compare ignored candidate and stable databases and flag unsafe worktree removal."""
    candidate_report = inspect_database(candidate_path)
    stable_report = inspect_database(stable_path)
    return {
        "same_logical_database": candidate_report["logical_sha256"] == stable_report["logical_sha256"],
        "candidate": candidate_report,
        "stable": stable_report,
    }


def _configure_logging() -> None:
    """Configure concise console logging for command-line failures and recovery events."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main() -> None:
    """Parse database state, verification, audit, and promotion commands."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    inspect_parser = commands.add_parser("inspect")
    inspect_parser.add_argument("--db", type=Path, required=True)

    state_parser = commands.add_parser("write-state")
    state_parser.add_argument("--db", type=Path, required=True)
    state_parser.add_argument("--database-path", required=True)
    state_parser.add_argument("--output", type=Path, required=True)

    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--db", type=Path, required=True)
    verify_parser.add_argument("--manifest", type=Path, required=True)

    audit_parser = commands.add_parser("audit")
    audit_parser.add_argument("--candidate-db", type=Path, required=True)
    audit_parser.add_argument("--stable-db", type=Path, required=True)

    promote_parser = commands.add_parser("promote")
    promote_parser.add_argument("--candidate-db", type=Path, required=True)
    promote_parser.add_argument("--stable-db", type=Path, required=True)
    promote_parser.add_argument("--confirm-logical-sha256", required=True)

    args = parser.parse_args()
    _configure_logging()
    if args.command == "inspect":
        result = inspect_database(args.db)
    elif args.command == "write-state":
        report = inspect_database(args.db)
        health_errors = _health_errors(report)
        if health_errors:
            raise RuntimeError(f"database health gate failed: {health_errors}")
        result = state_from_report(report, args.database_path)
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif args.command == "verify":
        result = verify_database(args.db, args.manifest)
    elif args.command == "audit":
        result = audit_worktree_database(args.candidate_db, args.stable_db)
        if not result["same_logical_database"]:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            raise SystemExit(2)
    else:
        result = promote_database(args.candidate_db, args.stable_db, args.confirm_logical_sha256)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, FileExistsError, ValueError, RuntimeError, sqlite3.Error, OSError) as error:
        LOGGER.error("database guard failed: %s", error)
        raise SystemExit(1) from error
