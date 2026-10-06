"""Read s02e metadata without coupling normal exports to search, models, or coverage."""

import ipaddress
import json
import logging
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)
FIELDS = ("reference_id", "url", "title", "publisher", "source_type", "scope", "event_id", "stage", "stage_en", "event_date", "checked_at", "valid_until", "is_reprint", "original_publisher")
WORKSPACE = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE_DB = WORKSPACE / "s02a_duplicate_merger/data/db/bri_re_database.db"
DEFAULT_REFERENCE_CONFIG = WORKSPACE / "supporting_docs/external_references_config.json"


def resolve_reference_snapshot(source_db: Path | None, external_references: Path | None = None,
                               reference_config: Path | None = None) -> Path | None:
    """Use the project pointer only for its bound source, never for arbitrary isolated databases.

    Pointer paths are relative to the config directory. Explicit snapshots override the pointer;
    both are validated before any export output is changed. This does not fetch or refresh evidence.
    """
    if external_references is not None and reference_config is not None:
        raise ValueError("Specify external_references or reference_config, not both")
    if external_references is not None:
        return Path(external_references).resolve()
    if reference_config is None:
        if source_db is None or Path(source_db).resolve() != DEFAULT_SOURCE_DB.resolve():
            return None
        reference_config = DEFAULT_REFERENCE_CONFIG
    config_path = Path(reference_config).resolve()
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"reference_config_unavailable: {config_path}") from exc
    if (not isinstance(config, dict) or config.get("version") != "external-reference-config-v1"
            or not _text(config.get("source_db")) or not _text(config.get("snapshot"))):
        raise ValueError(f"reference_config_format_invalid: {config_path}")
    bound_source = (config_path.parent / config["source_db"]).resolve()
    if source_db is None or Path(source_db).resolve() != bound_source:
        raise ValueError(f"reference_config_source_mismatch: {config_path}")
    recurring = config.get("recurring_snapshot")
    if recurring is not None:
        if not _text(recurring):
            raise ValueError(f"reference_config_recurring_snapshot_invalid: {config_path}")
        recurring_path = (config_path.parent / recurring).resolve()
        if recurring_path.exists():
            return recurring_path
    return (config_path.parent / config["snapshot"]).resolve()


def summarize_reference_audit(audit: list[dict]) -> dict:
    """Return explicit hidden-candidate counts without leaking private evidence to public files."""
    summary = {"total": len(audit), "success": 0, "pending": 0, "excluded": 0, "reasons": {}}
    for item in audit:
        summary[item["status"]] += 1
        reason = item["reason"]
        summary["reasons"][reason] = summary["reasons"].get(reason, 0) + 1
    return summary


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _reference(value: object, now: datetime) -> dict:
    """Validate the existing s04 display contract; never export arbitrary audit fields."""
    if not isinstance(value, dict) or any(key not in value for key in FIELDS):
        raise ValueError("reference_format_invalid")
    ref = {key: value[key] for key in FIELDS}
    if any(not _text(ref[key]) for key in ("reference_id", "url", "title", "publisher")):
        raise ValueError("reference_text_missing")
    if any(ref[key] is not None and not _text(ref[key]) for key in ("event_id", "stage", "stage_en", "original_publisher")):
        raise ValueError("reference_nullable_field_invalid")
    if ref["source_type"] not in ("official", "media") or ref["scope"] not in ("event", "project") or type(ref["is_reprint"]) is not bool:
        raise ValueError("reference_format_invalid")
    if ref["is_reprint"] and not _text(ref["original_publisher"]):
        raise ValueError("reference_origin_missing")
    if ref["scope"] == "project" and any(ref[key] is not None for key in ("event_id", "stage", "stage_en", "event_date")):
        raise ValueError("project_reference_has_event_claims")
    if ref["scope"] == "event" and (not _text(ref["event_id"]) or not _text(ref["stage"])):
        raise ValueError("event_reference_identity_missing")
    if ref["event_date"] is not None:
        event_date = ref["event_date"]
        if not isinstance(event_date, str) or not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", event_date):
            raise ValueError("reference_date_invalid")
        parts = [int(part) for part in event_date.split("-")]
        date(*(parts + [1] * (3 - len(parts))))
    try:
        checked = datetime.fromisoformat(ref["checked_at"].replace("Z", "+00:00"))
        until = datetime.fromisoformat(ref["valid_until"].replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("reference_timestamp_invalid") from exc
    if checked.tzinfo is None or until.tzinfo is None or checked > now or not checked < until <= checked + timedelta(days=90) or now >= until:
        raise ValueError("reference_stale_or_invalid_window")
    url = ref["url"]
    if re.search(r"[\s\\\x00-\x1f\x7f]", url):
        raise ValueError("reference_url_invalid")
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password or not host:
        raise ValueError("reference_url_invalid")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass  # DNS-shaped public URLs only; this export never resolves or requests them.
    else:
        raise ValueError("reference_url_invalid")
    labels = host.split(".")
    if len(labels) < 2 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels) or not re.fullmatch(r"[a-z][a-z0-9-]*", labels[-1]) or labels[-1] in {"localhost", "local", "internal", "lan", "home", "test", "invalid", "example", "onion", "arpa"} or host == "goalfore.cn" or host.endswith(".goalfore.cn"):
        raise ValueError("reference_url_invalid")
    _ = parsed.port  # Reject malformed/out-of-range ports locally.
    return ref


def load_references(path: Path | None, source_db: Path | None, *, now: datetime | None = None,
                    strict: bool = False) -> tuple[dict[str, list[dict]], list[dict]]:
    """Isolate bad candidates; export entrypoints require available configured snapshot files."""
    if path is None:
        return {}, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != "external-references-v1" or not isinstance(data.get("candidates"), list):
            raise ValueError("reference_snapshot_format_invalid")
    except (OSError, ValueError) as exc:
        if strict:
            raise ValueError(f"reference_snapshot_unavailable: {path}") from exc
        logger.warning("external_references_unavailable type=%s", type(exc).__name__)
        return {}, [{"status": "pending", "reason": "reference_snapshot_unavailable"}]
    current = now or datetime.now(timezone.utc)
    if source_db is None or not source_db.is_file():
        if strict:
            raise ValueError("reference_source_db_missing")
        return {}, [{"status": "pending", "reason": "reference_source_db_missing"}]
    with closing(sqlite3.connect(source_db.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        owners = {row[0]: row[1:] for row in conn.execute("SELECT e.id,e.canonical_id,e.project_stage,p.project_name,p.country_iso FROM project_events e JOIN canonical_projects p ON p.id=e.canonical_id")}
    accepted, audit, seen = {}, [], set()
    for index, candidate in enumerate(data["candidates"]):
        item = {"case_id": candidate.get("case_id", str(index)) if isinstance(candidate, dict) else str(index)}
        try:
            if not isinstance(candidate, dict):
                raise ValueError("candidate_format_invalid")
            # v1 verified snapshots predate this field; explicit new language metadata is binding.
            if "evidence_language" in candidate and candidate["evidence_language"] not in ("zh", "zh-CN", "zh-TW"):
                raise ValueError("reference_evidence_not_chinese")
            decision = candidate.get("decision")
            if decision not in ("event_reference", "project_reference"):
                audit.append(item | {"status": "excluded" if decision == "rejected" else "pending", "reason": candidate.get("reason") or "candidate_not_qualified"})
                continue
            project_id, event_id = candidate.get("project_id"), candidate.get("event_id")
            if not _text(project_id) or not _text(event_id):
                raise ValueError("candidate_identity_missing")
            owner = owners.get(event_id)
            if owner is None or owner[0] != project_id:
                raise ValueError("reference_event_project_mismatch")
            target = candidate.get("target")
            if not isinstance(target, dict) or not _text(target.get("project_name")) or not _text(target.get("country_iso")) or target.get("project_name") != owner[2] or target.get("country_iso") != owner[3]:
                raise ValueError("reference_target_identity_changed")
            ref = _reference(candidate.get("reference"), current)
            if decision != ref["scope"] + "_reference" or ref["scope"] == "event" and (ref["event_id"] != event_id or ref["stage"] != owner[1]):
                raise ValueError("reference_event_claim_mismatch")
            url = urlsplit(ref["url"])
            dedup = (project_id, ref["scope"], ref["event_id"], urlunsplit(url._replace(fragment="")))
            if dedup in seen:
                audit.append(item | {"status": "excluded", "reason": "duplicate_reference"})
                continue
            seen.add(dedup)
            record = item | {"status": "success", "reason": "qualified_reference"}
            accepted.setdefault(project_id, []).append({"reference": ref, "target": target, "audit": record})
            audit.append(record)
        except (TypeError, ValueError) as exc:
            logger.warning("external_reference_hidden case=%s reason=%s", item["case_id"], str(exc))
            audit.append(item | {"status": "pending", "reason": str(exc)})
    return accepted, audit


def references_for_row(row: dict, references: dict[str, list[dict]], audit: list[dict]) -> list[dict]:
    """Check the actual exported identity too, so a stale flat database cannot misattach links."""
    result = []
    for item in references.get(row["project_id"], []):
        if all(row.get(key) == item["target"].get(key) for key in ("project_name", "country_iso")):
            result.append(item["reference"])
        else:
            item["audit"].update(status="pending", reason="exported_project_identity_changed")
    return result
