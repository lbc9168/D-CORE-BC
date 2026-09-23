"""Carry field-level extraction decisions through deterministic S02B backfills.

Evidence lives on entity rows already linked to an event, so this needs no schema
migration. Explicitly withheld values cannot be recreated from a generated summary.
"""
from __future__ import annotations

import json
import logging
import sqlite3

logger = logging.getLogger(__name__)


def extraction_field_state(common_info: dict) -> dict:
    """Separate current publishing values from retained extraction error history."""
    withheld = common_info.get("withheld_capacity_fields", [])
    fields = {
        "capacity_mw": "capacity_mw_parsed",
        "storage_power_mw": "storage_power_mw_parsed",
        "storage_energy_mwh": "storage_energy_mwh_parsed",
        "voltage_kv": "voltage_kv_parsed",
        "line_length_km": "line_length_km_parsed",
        "transmission_capacity_mw": "transmission_capacity_mw_parsed",
        "production_capacity_value": "production_capacity_value_parsed",
        "production_capacity_unit": "production_capacity_unit_normalized",
    }
    return {
        field: {"status": "withheld" if field in withheld else
                "accepted" if common_info.get(key) is not None else "unknown",
                "value": common_info.get(key), "source": "extraction"}
        for field, key in fields.items() if key in common_info or field in withheld
    }


def _evidence_rows(connection: sqlite3.Connection) -> list[tuple]:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(chinese_entities)")}
    if not {"id", "event_id", "entity_evidence_json"}.issubset(columns):
        return []
    return connection.execute(
        "SELECT id,event_id,entity_evidence_json FROM chinese_entities "
        "WHERE entity_evidence_json IS NOT NULL"
    ).fetchall()


def _decode(value: str, row_id: int) -> dict:
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        logger.warning("Invalid field evidence on entity %s: %s", row_id, exc)
        return {}
    return payload if isinstance(payload, dict) else {}


def withheld_fields(connection: sqlite3.Connection) -> dict[str, set[str]]:
    """Union explicit field vetoes across source relations for each event."""
    result: dict[str, set[str]] = {}
    for row_id, event_id, value in _evidence_rows(connection):
        payload = _decode(value, row_id)
        fields = payload.get("withheld_capacity_fields", [])
        if isinstance(fields, list):
            result.setdefault(event_id, set()).update(f for f in fields if isinstance(f, str))
    return result


def record_repairs(connection: sqlite3.Connection, plan: list[dict], source: str) -> None:
    """Update current field status in the caller's transaction, retaining prior audit."""
    by_event = {change["event_id"]: change for change in plan}
    updates = []
    for row_id, event_id, value in _evidence_rows(connection):
        change = by_event.get(event_id)
        if not change:
            continue
        payload = _decode(value, row_id)
        if not payload:
            continue
        current = payload.setdefault("field_validation", {})
        if not isinstance(current, dict):
            logger.warning("Invalid field_validation on entity %s; retaining evidence", row_id)
            continue
        audit = payload.setdefault("field_validation_audit", [])
        if not isinstance(audit, list):
            logger.warning("Invalid field_validation_audit on entity %s; retaining evidence", row_id)
            continue
        for field, repaired in change.items():
            if field == "event_id":
                continue
            if field in current:
                audit.append({"field": field, "previous": current[field]})
            current[field] = {"status": "repaired", "source": source, "value": repaired}
        updates.append((json.dumps(payload, ensure_ascii=False), row_id))
    if updates:
        connection.executemany("UPDATE chinese_entities SET entity_evidence_json=? WHERE id=?", updates)
