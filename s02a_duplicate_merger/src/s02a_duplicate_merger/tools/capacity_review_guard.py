"""Preserve only capacities carrying the reviewed-capacity audit contract."""
import hashlib
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
FIELDS = ("capacity_mw", "capacity_mw_raw", "capacity_details", "capacity_source")


def capture_reviewed_capacity(conn, event_id, incoming):
    """Fail closed on audit drift; retain conflicting incoming evidence privately."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='capacity_review_audit'").fetchone():
        return None
    audit = conn.execute("SELECT approved_manifest_hash, after_json FROM capacity_review_audit WHERE event_id=?", (event_id,)).fetchone()
    if audit is None:
        return None
    after = json.loads(audit[1])
    current = conn.execute("SELECT capacity_mw, capacity_mw_raw, capacity_details, capacity_source FROM project_events WHERE id=?", (event_id,)).fetchone()
    if current is None or tuple(current) != tuple(after[k] for k in FIELDS) or after["capacity_source"] != "manual":
        raise ValueError(f"Reviewed capacity has drifted: {event_id}")
    preserved = {k: after[k] for k in FIELDS}
    # Preserve all incoming capacity evidence, including differing AC/DC bases,
    # even where a numeric parser would make the scalar appear equal.
    supplied = incoming.get("capacity_mw")
    if supplied not in (None, "") or incoming.get("capacity_details"):
        payload = json.dumps(incoming, ensure_ascii=False, sort_keys=True, default=str)
        digest = hashlib.sha256((event_id + audit[0] + payload).encode("utf-8")).hexdigest()
        conn.execute("""INSERT OR IGNORE INTO capacity_review_conflicts
            (conflict_hash,event_id,approved_manifest_hash,incoming_json,preserved_json,observed_at)
            VALUES (?,?,?,?,?,?)""", (digest, event_id, audit[0], payload,
            json.dumps(preserved, ensure_ascii=False, sort_keys=True), datetime.now(timezone.utc).isoformat()))
        logger.warning("Reviewed capacity retained; incoming capacity evidence queued: %s", event_id)
    return preserved


def restore_reviewed_capacity(conn, event_id, preserved):
    """Protect against later normalizers as well as full field arbitration."""
    if preserved is not None:
        conn.execute("UPDATE project_events SET capacity_mw=?,capacity_mw_raw=?,capacity_details=?,capacity_source=? WHERE id=?",
                     tuple(preserved[k] for k in FIELDS) + (event_id,))
