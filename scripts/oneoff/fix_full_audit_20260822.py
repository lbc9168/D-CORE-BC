"""Apply the evidence-backed corrections from the August 2026 full web audit."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[2]
MAIN_DB = WORKSPACE / "s02a_duplicate_merger/data/db/bri_re_database.db"
SOURCE_DB = WORKSPACE / "s01b_project_extractor/data/projects.db"
AUDIT_JSON = WORKSPACE / "tmp/web_visible_financing_html_20260821/financing_audit_171.json"
LOG_DIR = Path(__file__).resolve().parent / "logs"

EXPECTED_PROJECT_TRANSITIONS = {
    ("信贷融资", "Unknown"): 5,
    ("信贷融资", "其他"): 2,
    ("混合融资", "Unknown"): 11,
    ("混合融资", "信贷融资"): 36,
    ("混合融资", "债券融资"): 1,
    ("混合融资", "其他"): 2,
    ("股权融资", "Unknown"): 33,
    ("股权融资", "信贷融资"): 1,
    ("股权融资", "其他"): 2,
}

PROJECT_EXCLUSIONS = {
    "KOR-WD-0007": "no_chinese_entity_posco_equinor_and_ls_cable_are_non_chinese",
    "IDN-BM-0003": "no_chinese_entity_deere_is_a_us_company",
    "IDN-PV-0009": "out_of_scope_source_is_2x225mw_coal_not_200kw_solar",
}

MAIN_ENTITY_DELETIONS = {
    164: "ZAF-HB-0001-01",
    3330: "ZAF-PV-0001-02",
    3331: "ZAF-PV-0001-02",
    6216: "ARE-HB-0015-01",
    6308: "HRV-PV-0003-03",
}

DBS_MAIN_ENTITY_IDS = {
    1557: "UZB-WD-0009-01",
    1558: "UZB-WD-0009-01",
    1616: "LAO-PV-0006-01",
    1617: "LAO-PV-0006-01",
    4195: "GBR-ES-0021-02",
}

SOURCE_EXCLUSIONS = {
    223: "entity_mismatch_ceec_not_supported_powerchina_bureau11_supported",
    1115: "no_chinese_entity_deere_is_a_us_company",
    176: "entity_mismatch_ceec_equipment_not_supported_15mcc_supported",
    2367: "dbs_is_a_singapore_bank_not_a_chinese_entity",
    2368: "dbs_is_a_singapore_bank_not_a_chinese_entity",
    2453: "dbs_is_a_singapore_bank_not_a_chinese_entity",
    2454: "dbs_is_a_singapore_bank_not_a_chinese_entity",
    2468: "out_of_scope_source_is_2x225mw_coal_not_200kw_solar",
    2556: "no_chinese_entity_in_this_project_record",
    5328: "no_chinese_entity_posco_is_a_korean_company",
    5475: "entity_mismatch_ceec_equipment_not_supported_powerchina_supported",
    5476: "entity_mismatch_ceec_equipment_not_supported_powerchina_supported",
    7139: "dbs_is_a_singapore_bank_not_a_chinese_entity",
    10975: "entity_mismatch_cpecc_not_supported_powerchina_supported",
    10976: "entity_mismatch_ceec_east_china_not_supported_powerchina_supported",
    11012: "entity_mismatch_ceec_equipment_not_supported_goldwind_supported",
    11147: "entity_mismatch_ceec_not_supported_norinco_and_spic_supported",
}

AMBIGUOUS_SOURCE_FINANCE_EVENTS = {
    "AUS-ES-0012-02",
    "BRA-PV-0004-01",
    "BRA-PV-0004-02",
    "BRA-PV-0030-01",
    "BRA-PV-0031-01",
    "KAZ-PV-0003-02",
    "KAZ-WD-0033-01",
    "MEX-HB-0003-01",
    "OMN-HB-0003-01",
    "ROU-HB-0004-01",
    "ROU-PV-0005-01",
    "ROU-PV-0006-01",
    "SAU-HB-0024-02",
    "SAU-PV-0054-01",
    "SAU-PV-0055-01",
    "SRB-WD-0002-02",
    "UZB-HB-0023-01",
    "UZB-HB-0024-01",
    "UZB-HB-0025-01",
    "UZB-HB-0029-01",
    "ZAF-PV-0011-01",
}


def sha256(path: Path) -> str:
    """Return an uppercase SHA-256 digest for one file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def validate_path(path: Path, suffix: str) -> Path:
    """Resolve an input and require it to be a workspace file with the expected suffix."""
    resolved = path.resolve()
    if not resolved.is_relative_to(WORKSPACE.resolve()):
        raise ValueError(f"Path is outside the workspace: {resolved}")
    if not resolved.is_file() or resolved.suffix.lower() != suffix:
        raise ValueError(f"Missing valid {suffix} file: {resolved}")
    return resolved


def configure_logging(timestamp: str) -> Path:
    """Configure a UTF-8 file log and return its path."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"fix_full_audit_{timestamp}.log"
    logging.basicConfig(
        filename=path,
        level=logging.INFO,
        encoding="utf-8",
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return path


def backup_file(path: Path, timestamp: str) -> Path:
    """Create a recoverable copy beside a database before mutation."""
    directory = path.parent / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{path.stem}.before_full_audit_fix_{timestamp}{path.suffix}"
    shutil.copy2(path, target)
    logging.info("Backup created: %s", target)
    return target


def execute_exact(
    connection: sqlite3.Connection,
    sql: str,
    params: tuple[object, ...],
    expected: int,
    label: str,
) -> None:
    """Execute one statement and require its affected-row count."""
    cursor = connection.execute(sql, params)
    if cursor.rowcount != expected:
        raise RuntimeError(
            f"{label}: expected {expected} affected row(s), got {cursor.rowcount}"
        )
    logging.info("%s: %s row(s)", label, cursor.rowcount)


def load_financing_plan() -> list[dict[str, str]]:
    """Load and validate the final 93-project/100-event financing plan."""
    payload = json.loads(AUDIT_JSON.read_text(encoding="utf-8"))
    if payload.get("meta", {}).get("verdicts") != {
        "confirmed_error": 93,
        "supported": 78,
    }:
        raise RuntimeError("Audit verdict totals are not the reviewed 93/78 result")
    transitions: Counter[tuple[str, str]] = Counter()
    plan: list[dict[str, str]] = []
    for project in payload["projects"]:
        if project["verdict"] != "confirmed_error":
            continue
        old_label = str(project["current_label"])
        new_label = str(project["recommended_label"])
        transitions[(old_label, new_label)] += 1
        for event in project["events"]:
            plan.append(
                {
                    "project_id": str(project["project_id"]),
                    "event_id": str(event["event_id"]),
                    "old_label": old_label,
                    "new_label": new_label,
                }
            )
    if dict(transitions) != EXPECTED_PROJECT_TRANSITIONS:
        raise RuntimeError(f"Unexpected financing transitions: {dict(transitions)}")
    if len(plan) != 100 or len({row["event_id"] for row in plan}) != 100:
        raise RuntimeError("Financing plan must contain 100 distinct event IDs")
    return plan


def split_links(raw: object) -> list[str]:
    """Parse the canonical JSON source-link array used for exact upstream matching."""
    if not raw:
        return []
    parsed = json.loads(str(raw))
    if not isinstance(parsed, list):
        raise ValueError(f"Expected source_links JSON array, got: {raw!r}")
    return [str(value).strip() for value in parsed if str(value).strip()]


def build_source_financing_plan(
    connection: sqlite3.Connection,
    main_plan: list[dict[str, str]],
) -> list[dict[str, object]]:
    """Map 79 unambiguous canonical events to exact upstream extraction rows."""
    safe_events = [
        row for row in main_plan if row["event_id"] not in AMBIGUOUS_SOURCE_FINANCE_EVENTS
    ]
    if len(safe_events) != 79:
        raise RuntimeError(f"Expected 79 safe source events, got {len(safe_events)}")
    source_rows: dict[int, dict[str, object]] = {}
    owners: dict[int, str] = {}
    for row in safe_events:
        source_links_raw = connection.execute(
            "SELECT source_links FROM project_events WHERE id = ?", (row["event_id"],)
        ).fetchone()
        if source_links_raw is None:
            raise RuntimeError(f"Missing main event for source mapping: {row['event_id']}")
        links = split_links(source_links_raw[0])
        if not links:
            raise RuntimeError(f"No source links for safe event: {row['event_id']}")
        placeholders = ",".join("?" for _ in links)
        candidates = connection.execute(
            f"""
                SELECT id
                FROM source_db.extracted_projects
                WHERE source_link IN ({placeholders})
                  AND financing_model = ?
                  AND excluded = 0
                ORDER BY id
            """,
            (*links, row["old_label"]),
        ).fetchall()
        if not candidates:
            raise RuntimeError(f"No upstream financing rows for safe event: {row['event_id']}")
        for (source_id,) in candidates:
            previous = source_rows.get(source_id)
            if previous and previous["new_label"] != row["new_label"]:
                raise RuntimeError(
                    f"Conflicting source targets for {source_id}: "
                    f"{previous['new_label']} vs {row['new_label']}"
                )
            previous_owner = owners.get(source_id)
            if previous_owner and previous_owner != row["event_id"]:
                raise RuntimeError(
                    f"Source row {source_id} maps to multiple supposedly safe events: "
                    f"{previous_owner}, {row['event_id']}"
                )
            owners[source_id] = row["event_id"]
            source_rows[source_id] = {
                "source_id": source_id,
                "event_id": row["event_id"],
                "old_label": row["old_label"],
                "new_label": row["new_label"],
            }
    return list(source_rows.values())


def table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    """Return core main/source table counts for before/after verification."""
    return {
        "canonical_projects": connection.execute(
            "SELECT COUNT(*) FROM canonical_projects"
        ).fetchone()[0],
        "project_events": connection.execute(
            "SELECT COUNT(*) FROM project_events"
        ).fetchone()[0],
        "chinese_entities": connection.execute(
            "SELECT COUNT(*) FROM chinese_entities"
        ).fetchone()[0],
        "source_extracted_projects": connection.execute(
            "SELECT COUNT(*) FROM source_db.extracted_projects"
        ).fetchone()[0],
    }


def validate_targets(connection: sqlite3.Connection, financing_plan: list[dict[str, str]]) -> None:
    """Require every target and expected old value before starting the transaction."""
    for row in financing_plan:
        actual = connection.execute(
            "SELECT canonical_id, financing_model FROM project_events WHERE id = ?",
            (row["event_id"],),
        ).fetchone()
        if actual != (row["project_id"], row["old_label"]):
            raise RuntimeError(
                f"Financing precondition failed for {row['event_id']}: {actual!r}"
            )
    for project_id in PROJECT_EXCLUSIONS:
        actual = connection.execute(
            "SELECT excluded FROM canonical_projects WHERE id = ?", (project_id,)
        ).fetchone()
        if actual != (0,):
            raise RuntimeError(f"Project exclusion precondition failed: {project_id}={actual!r}")
    for entity_id, event_id in {**MAIN_ENTITY_DELETIONS, **DBS_MAIN_ENTITY_IDS}.items():
        actual = connection.execute(
            "SELECT event_id FROM chinese_entities WHERE id = ?", (entity_id,)
        ).fetchone()
        if actual != (event_id,):
            raise RuntimeError(f"Entity precondition failed: {entity_id}={actual!r}")
    expected_source_ids = set(SOURCE_EXCLUSIONS) | {41, 74, 177, 11013, 11153}
    placeholders = ",".join("?" for _ in expected_source_ids)
    found = {
        row[0]
        for row in connection.execute(
            f"SELECT id FROM source_db.extracted_projects WHERE id IN ({placeholders})",
            tuple(expected_source_ids),
        )
    }
    if found != expected_source_ids:
        raise RuntimeError(f"Missing source IDs: {sorted(expected_source_ids - found)}")


def apply_financing(connection: sqlite3.Connection, plan: list[dict[str, str]]) -> None:
    """Apply event-level financing corrections without touching other events in a project."""
    for row in plan:
        if row["new_label"] == "Unknown":
            sql = """
                UPDATE project_events
                SET financing_model = ?, financing_model_details = ''
                WHERE id = ? AND canonical_id = ? AND financing_model = ?
            """
        else:
            sql = """
                UPDATE project_events
                SET financing_model = ?
                WHERE id = ? AND canonical_id = ? AND financing_model = ?
            """
        execute_exact(
            connection,
            sql,
            (
                row["new_label"],
                row["event_id"],
                row["project_id"],
                row["old_label"],
            ),
            1,
            f"financing {row['event_id']} {row['old_label']}->{row['new_label']}",
        )


def apply_source_financing(
    connection: sqlite3.Connection,
    plan: list[dict[str, object]],
) -> None:
    """Apply only the upstream financing mappings proven unambiguous by source and event."""
    for row in plan:
        if row["new_label"] == "Unknown":
            sql = """
                UPDATE source_db.extracted_projects
                SET financing_model = ?, financing_model_details = ''
                WHERE id = ? AND financing_model = ? AND excluded = 0
            """
        else:
            sql = """
                UPDATE source_db.extracted_projects
                SET financing_model = ?
                WHERE id = ? AND financing_model = ? AND excluded = 0
            """
        execute_exact(
            connection,
            sql,
            (row["new_label"], row["source_id"], row["old_label"]),
            1,
            f"source financing {row['source_id']} via {row['event_id']}",
        )


def apply_project_exclusions(connection: sqlite3.Connection) -> None:
    """Exclude three confirmed out-of-scope or no-Chinese-entity projects."""
    for project_id, reason in PROJECT_EXCLUSIONS.items():
        execute_exact(
            connection,
            """
                UPDATE canonical_projects
                SET excluded = 1, excluded_reason = ?
                WHERE id = ? AND excluded = 0
            """,
            (reason, project_id),
            1,
            f"exclude project {project_id}",
        )


def apply_main_entity_corrections(connection: sqlite3.Connection) -> None:
    """Remove or replace confirmed false entity-to-event mappings in the canonical DB."""
    for entity_id, event_id in MAIN_ENTITY_DELETIONS.items():
        execute_exact(
            connection,
            "DELETE FROM chinese_entities WHERE id = ? AND event_id = ?",
            (entity_id, event_id),
            1,
            f"delete wrong entity {entity_id}",
        )
    for entity_id, event_id in DBS_MAIN_ENTITY_IDS.items():
        execute_exact(
            connection,
            "DELETE FROM chinese_entities WHERE id = ? AND event_id = ?",
            (entity_id, event_id),
            1,
            f"remove DBS from Chinese entities {entity_id}",
        )

    replacements = {
        25: (
            "中国电力建设集团有限公司",
            "PowerChina",
            "",
            "",
            "EPC总承包",
            1,
        ),
        53: (
            "广州工业投资控股集团有限公司",
            "Guangzhou Industrial Investment Holdings Group Co., Ltd.",
            "广州广重企业集团有限公司",
            "Guangzhou Guangzhong Enterprise Group Co., Ltd.",
            "设备供应商",
            0,
        ),
        136: (
            "中国有色矿业集团有限公司",
            "China Nonferrous Metal Mining Group Co., Ltd. (CNMC)",
            "十五冶对外工程有限公司",
            "15MCC Overseas Engineering Co., Ltd.",
            "EPC总承包",
            1,
        ),
        6248: (
            "金风科技股份有限公司",
            "Goldwind",
            "",
            "",
            "设备供应商",
            0,
        ),
        6313: (
            "中国北方工业有限公司",
            "China North Industries Corporation (NORINCO)",
            "北方国际合作股份有限公司",
            "NORINCO International Cooperation Ltd.",
            "EPC总承包",
            1,
        ),
    }
    for entity_id, values in replacements.items():
        execute_exact(
            connection,
            """
                UPDATE chinese_entities
                SET implementing_entity = ?, implementing_entity_en = ?,
                    subsidiary_name = ?, subsidiary_name_en = ?,
                    involvement_type = ?, is_central_soe = ?,
                    entity_en_quality = 'manual_verified'
                WHERE id = ?
            """,
            (*values, entity_id),
            1,
            f"replace entity mapping {entity_id}",
        )


def apply_source_corrections(connection: sqlite3.Connection) -> None:
    """Correct exact upstream entity rows that would otherwise recreate confirmed errors."""
    for row_id, reason in SOURCE_EXCLUSIONS.items():
        execute_exact(
            connection,
            """
                UPDATE source_db.extracted_projects
                SET excluded = 1, exclude_reason = ?,
                    exclude_source = 'manual_full_audit_2026-08-22'
                WHERE id = ? AND excluded = 0
            """,
            (reason, row_id),
            1,
            f"exclude source row {row_id}",
        )

    replacements = {
        41: (
            "中国电力建设集团有限公司",
            "PowerChina",
            "",
            "",
            "EPC总承包",
            1,
        ),
        74: (
            "广州工业投资控股集团有限公司",
            "Guangzhou Industrial Investment Holdings Group Co., Ltd.",
            "广州广重企业集团有限公司",
            "Guangzhou Guangzhong Enterprise Group Co., Ltd.",
            "设备供应商",
            0,
        ),
        177: (
            "中国有色矿业集团有限公司",
            "China Nonferrous Metal Mining Group Co., Ltd. (CNMC)",
            "十五冶对外工程有限公司",
            "15MCC Overseas Engineering Co., Ltd.",
            "EPC总承包",
            1,
        ),
        11013: (
            "金风科技股份有限公司",
            "Goldwind",
            "",
            "",
            "设备供应商",
            0,
        ),
        11153: (
            "中国北方工业有限公司",
            "China North Industries Corporation (NORINCO)",
            "北方国际合作股份有限公司",
            "NORINCO International Cooperation Ltd.",
            "EPC总承包",
            1,
        ),
    }
    for row_id, values in replacements.items():
        execute_exact(
            connection,
            """
                UPDATE source_db.extracted_projects
                SET implementing_entity = ?, implementing_entity_en = ?,
                    subsidiary_name = ?, subsidiary_name_en = ?,
                    involvement_type = ?, is_central_soe = ?
                WHERE id = ? AND excluded = 0
            """,
            (*values, row_id),
            1,
            f"replace source entity {row_id}",
        )


def verify_result(
    connection: sqlite3.Connection,
    plan: list[dict[str, str]],
    source_plan: list[dict[str, object]],
) -> None:
    """Verify every requested correction inside the still-open transaction."""
    for row in plan:
        actual = connection.execute(
            "SELECT financing_model FROM project_events WHERE id = ?",
            (row["event_id"],),
        ).fetchone()
        if actual != (row["new_label"],):
            raise RuntimeError(f"Financing verification failed: {row['event_id']}={actual!r}")
    for project_id in PROJECT_EXCLUSIONS:
        if connection.execute(
            "SELECT excluded FROM canonical_projects WHERE id = ?", (project_id,)
        ).fetchone() != (1,):
            raise RuntimeError(f"Project exclusion verification failed: {project_id}")
    for row in source_plan:
        actual = connection.execute(
            "SELECT financing_model FROM source_db.extracted_projects WHERE id = ?",
            (row["source_id"],),
        ).fetchone()
        if actual != (row["new_label"],):
            raise RuntimeError(
                f"Source financing verification failed: {row['source_id']}={actual!r}"
            )
    remaining = connection.execute(
        f"SELECT id FROM chinese_entities WHERE id IN ({','.join('?' for _ in DBS_MAIN_ENTITY_IDS)})",
        tuple(DBS_MAIN_ENTITY_IDS),
    ).fetchall()
    if remaining:
        raise RuntimeError(f"DBS entity rows remain: {remaining}")


def parse_args() -> argparse.Namespace:
    """Parse the explicit apply gate for this one-off correction."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit changes; without this flag the transaction is rolled back.",
    )
    return parser.parse_args()


def main() -> None:
    """Validate, optionally back up, apply atomically, and report hashes/counts."""
    args = parse_args()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = configure_logging(timestamp)
    main_db = validate_path(MAIN_DB, ".db")
    source_db = validate_path(SOURCE_DB, ".db")
    validate_path(AUDIT_JSON, ".json")
    plan = load_financing_plan()
    before_hashes = {"main": sha256(main_db), "source": sha256(source_db)}
    backups: dict[str, str] = {}
    if args.apply:
        backups = {
            "main": str(backup_file(main_db, timestamp)),
            "source": str(backup_file(source_db, timestamp)),
        }

    connection = sqlite3.connect(main_db)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("ATTACH DATABASE ? AS source_db", (str(source_db),))
        before_counts = table_counts(connection)
        validate_targets(connection, plan)
        source_financing_plan = build_source_financing_plan(connection, plan)
        connection.execute("BEGIN IMMEDIATE")
        apply_financing(connection, plan)
        apply_source_financing(connection, source_financing_plan)
        apply_project_exclusions(connection)
        apply_main_entity_corrections(connection)
        apply_source_corrections(connection)
        verify_result(connection, plan, source_financing_plan)
        after_counts = table_counts(connection)
        expected_counts = dict(before_counts)
        expected_counts["chinese_entities"] -= len(MAIN_ENTITY_DELETIONS) + len(
            DBS_MAIN_ENTITY_IDS
        )
        if after_counts != expected_counts:
            raise RuntimeError(
                f"Unexpected table counts: expected={expected_counts}, actual={after_counts}"
            )
        if args.apply:
            connection.commit()
        else:
            connection.rollback()
    except (sqlite3.Error, RuntimeError, ValueError) as exc:
        connection.rollback()
        logging.error("Full audit correction failed: %s", exc, exc_info=True)
        raise
    finally:
        connection.close()

    after_hashes = {"main": sha256(main_db), "source": sha256(source_db)}
    if args.apply and after_hashes == before_hashes:
        raise RuntimeError("Apply mode completed without changing database hashes")
    if not args.apply and after_hashes != before_hashes:
        raise RuntimeError("Dry-run changed a database file")
    print(
        json.dumps(
            {
                "mode": "apply" if args.apply else "dry-run",
                "financing_projects": 93,
                "financing_event_rows": len(plan),
                "source_financing_rows": len(source_financing_plan),
                "ambiguous_source_financing_events_left_unchanged": len(
                    AMBIGUOUS_SOURCE_FINANCE_EVENTS
                ),
                "excluded_projects": sorted(PROJECT_EXCLUSIONS),
                "removed_main_entity_rows": sorted(
                    set(MAIN_ENTITY_DELETIONS) | set(DBS_MAIN_ENTITY_IDS)
                ),
                "before_counts": before_counts,
                "after_counts": after_counts,
                "before_hashes": before_hashes,
                "after_hashes": after_hashes,
                "backups": backups,
                "log": str(log_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
