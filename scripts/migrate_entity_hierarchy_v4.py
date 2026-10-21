#!/usr/bin/env python3
"""P2: apply company_hierarchy_v4 to chinese_entities.

Dry-run by default. Apply mode backs up the SQLite file, updates only rows whose
derived group/SOE changed, writes an audit CSV, and verifies invariants.

Policy:
- 16 rows with entity_id: resolve by ID.
- Rows without entity_id: resolve subsidiary_name -> implementing_entity ->
  entity_name -> raw_entity_name; only unique V4 identities are used.
- unmatched / group-only rows are left untouched.
- 湖南云箭 historical rows are explicitly blanked (group fields = NULL).
- entity_id/entity_name are filled only for rows that actually change group/SOE.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from s01b_project_extractor.stages.entity_normalize import (
    UNMATCHED_NEEDS_REVIEW,
    VERIFIED_CHINESE,
    resolve_entity,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "s02a_duplicate_merger" / "data" / "db" / "bri_re_database.db"
DEFAULT_REGISTRY = ROOT / "supporting_docs" / "company_registry_v4.json"
DEFAULT_REPORT = ROOT / "output" / "company-registry-v4" / "p2"

logging.disable(logging.WARNING)


def group_of(entity: dict) -> str | None:
    if entity.get("historical_only"):
        return None
    if entity.get("parent_entity_id"):
        return entity["root_entity_id"]
    if entity.get("v3_group_id") and entity["v3_group_id"] != entity["entity_id"]:
        return entity["v3_group_id"]
    return entity["entity_id"]


def load_registry(path: Path) -> dict[str, dict]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {
        entity["entity_id"]: entity
        for entity in doc["entities"]
        if entity.get("entity_id", "").startswith("ent_")
    }


def resolve_row(row: sqlite3.Row, by_id: dict[str, dict]) -> tuple[str, dict | None, dict | None]:
    entity_id = (row["entity_id"] or "").strip()
    if entity_id and entity_id in by_id:
        entity = by_id[entity_id]
        return "by_id", entity, by_id.get(group_of(entity))
    name = (
        (row["subsidiary_name"] or "").strip()
        or (row["implementing_entity"] or "").strip()
        or (row["entity_name"] or "").strip()
        or (row["raw_entity_name"] or "").strip()
    )
    if not name:
        return "no_name", None, None
    resolved = resolve_entity(name)
    if resolved.entity_id and resolved.entity_id in by_id:
        entity = by_id[resolved.entity_id]
        return "name", entity, by_id.get(group_of(entity))
    if resolved.verification_status == VERIFIED_CHINESE and resolved.group_id:
        return "group_only", None, None
    return "unmatched", None, None


def is_hunan_historical(row: sqlite3.Row) -> bool:
    return "云箭" in (row["subsidiary_name"] or "") and "兵器装备" in (row["implementing_entity"] or "")


def collect_plan(conn: sqlite3.Connection, by_id: dict[str, dict]) -> tuple[list[dict], Counter]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT id, event_id, entity_id, entity_name, group_entity_id,
                  implementing_entity, implementing_entity_en, subsidiary_name,
                  raw_entity_name, is_central_soe
           FROM chinese_entities ORDER BY id"""
    ).fetchall()
    plan: list[dict] = []
    stats: Counter = Counter()
    for row in rows:
        stats["rows"] += 1
        if is_hunan_historical(row):
            stats["hunan_blank"] += 1
            plan.append({
                "id": row["id"],
                "event_id": row["event_id"],
                "action": "blank_group",
                "source": "hunan_historical",
                "name": row["subsidiary_name"],
                "old_implementing_entity": row["implementing_entity"],
                "new_implementing_entity": None,
                "old_group_entity_id": row["group_entity_id"],
                "new_group_entity_id": None,
                "old_is_central_soe": row["is_central_soe"],
                "new_is_central_soe": None,
                "old_entity_id": row["entity_id"],
                "new_entity_id": row["entity_id"],
                "new_entity_name": row["entity_name"],
            })
            continue

        source, entity, group = resolve_row(row, by_id)
        stats[f"resolve_{source}"] += 1
        if entity is None or group is None:
            continue
        new_group_name = group.get("name_cn")
        new_group_id = group.get("entity_id")
        new_soe = group.get("is_central_soe")
        new_soe_int = None if new_soe is None else int(bool(new_soe))
        new_entity_id = entity.get("entity_id")
        new_entity_name = entity.get("name_cn")
        old_soe = row["is_central_soe"]
        old_soe_int = None if old_soe in (None, "") else int(old_soe)
        if (row["implementing_entity"] or "") == (new_group_name or "") and old_soe_int == new_soe_int:
            stats["unchanged"] += 1
            continue
        stats["changed"] += 1
        plan.append({
            "id": row["id"],
            "event_id": row["event_id"],
            "action": "update",
            "source": source,
            "name": row["subsidiary_name"] or row["implementing_entity"] or row["entity_name"],
            "old_implementing_entity": row["implementing_entity"],
            "new_implementing_entity": new_group_name,
            "old_group_entity_id": row["group_entity_id"],
            "new_group_entity_id": new_group_id,
            "old_is_central_soe": old_soe_int,
            "new_is_central_soe": new_soe_int,
            "old_entity_id": row["entity_id"],
            "new_entity_id": new_entity_id,
            "new_entity_name": new_entity_name,
        })
    return plan, stats


def write_audit(path: Path, plan: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "id", "event_id", "action", "source", "name",
        "old_implementing_entity", "new_implementing_entity",
        "old_group_entity_id", "new_group_entity_id",
        "old_is_central_soe", "new_is_central_soe",
        "old_entity_id", "new_entity_id", "new_entity_name",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(plan)


def apply_plan(db: Path, plan: list[dict]) -> None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db.with_name(db.name + f".bak.{stamp}")
    if backup.exists():
        raise SystemExit(f"备份已存在，拒绝覆盖: {backup}")
    shutil.copy2(db, backup)
    if not backup.exists() or backup.stat().st_size != db.stat().st_size:
        raise SystemExit(f"备份失败或大小不一致: {backup}")

    conn = sqlite3.connect(db)
    try:
        before = conn.execute("SELECT COUNT(*) FROM chinese_entities").fetchone()[0]
        backup_rows = sqlite3.connect(f"file:{backup}?mode=ro", uri=True).execute(
            "SELECT COUNT(*) FROM chinese_entities"
        ).fetchone()[0]
        if before != backup_rows:
            raise SystemExit("备份行数与源库不一致，停止")

        normal = [row for row in plan if row["action"] == "update"]
        blank = [row for row in plan if row["action"] == "blank_group"]
        with conn:
            conn.executemany(
                """UPDATE chinese_entities SET
                     implementing_entity=?,
                     implementing_entity_en=NULL,
                     group_entity_id=?,
                     is_central_soe=?,
                     entity_id=COALESCE(NULLIF(entity_id,''), ?),
                     entity_name=COALESCE(NULLIF(entity_name,''), ?)
                   WHERE id=?""",
                [
                    (
                        row["new_implementing_entity"],
                        row["new_group_entity_id"],
                        row["new_is_central_soe"],
                        row["new_entity_id"],
                        row["new_entity_name"],
                        row["id"],
                    )
                    for row in normal
                ],
            )
            conn.executemany(
                """UPDATE chinese_entities SET
                     implementing_entity=NULL,
                     implementing_entity_en=NULL,
                     group_entity_id=NULL,
                     is_central_soe=NULL
                   WHERE id=?""",
                [(row["id"],) for row in blank],
            )

        after = conn.execute("SELECT COUNT(*) FROM chinese_entities").fetchone()[0]
        if after != before:
            raise SystemExit(f"行数变化: {before} -> {after}")
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if fk or integrity != "ok":
            raise SystemExit(f"完整性校验失败: fk={fk[:5]} integrity={integrity}")
    except Exception:
        conn.rollback()
        # Restore from the verified backup on any failure.
        shutil.copy2(backup, db)
        raise
    finally:
        conn.close()
    print(f"backup: {backup}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    by_id = load_registry(args.registry)
    conn = sqlite3.connect(f"file:{args.db}?mode=ro" if not args.apply else args.db, uri=not args.apply)
    try:
        plan, stats = collect_plan(conn, by_id)
    finally:
        conn.close()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = args.out or (DEFAULT_REPORT / f"p2-{'apply' if args.apply else 'plan'}-{stamp}.csv")
    write_audit(out, plan)

    print("== P2 plan ==")
    for key, value in stats.items():
        print(f"{key}: {value}")
    print(f"planned_updates: {len(plan)}")
    print(f"audit: {out}")
    if not args.apply:
        print("dry-run: no database changes")
        return 0
    if not plan:
        print("nothing to apply")
        return 0
    apply_plan(args.db, plan)
    print("applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
