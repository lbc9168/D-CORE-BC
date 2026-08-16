"""保守清理中方实体事实表中的真正重复行。

仅当 event、集团、具体子公司和参与角色全部相同，且其余业务字段没有非空冲突时，
才合并到最小 ID；冲突组只进入报告，不删除。
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from s02b_entity_normalizer.tools.entity_recovery import _backup_db, _require_db

logger = logging.getLogger(__name__)

_MERGE_FIELDS = (
    "implementing_entity_en",
    "subsidiary_name_en",
    "entity_en_quality",
    "subcontract_amount_raw",
    "subcontract_amount",
    "is_central_soe",
)


def _present(value: Any) -> bool:
    """判断字段是否包含可用于合并的非空值。"""
    return value is not None and value != ""


def build_dedup_plan(db_path: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """返回（安全合并计划，冲突组），全程只读。"""
    connection = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        groups = connection.execute(
            """
            SELECT event_id, COALESCE(implementing_entity, '') AS implementing_entity,
                   COALESCE(subsidiary_name, '') AS subsidiary_name,
                   COALESCE(involvement_type, '') AS involvement_type,
                   GROUP_CONCAT(id) AS ids
            FROM chinese_entities
            GROUP BY event_id, COALESCE(implementing_entity, ''),
                     COALESCE(subsidiary_name, ''), COALESCE(involvement_type, '')
            HAVING COUNT(*) > 1
            ORDER BY event_id, MIN(id)
            """
        ).fetchall()

        safe: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        for group in groups:
            ids = sorted(int(value) for value in str(group["ids"]).split(","))
            placeholders = ",".join("?" for _ in ids)
            rows = connection.execute(
                f"SELECT id, {', '.join(_MERGE_FIELDS)} FROM chinese_entities "
                f"WHERE id IN ({placeholders}) ORDER BY id",
                ids,
            ).fetchall()
            merged: dict[str, Any] = {}
            field_conflicts: dict[str, list[Any]] = {}
            for field in _MERGE_FIELDS:
                values: list[Any] = []
                for row in rows:
                    value = row[field]
                    if _present(value) and value not in values:
                        values.append(value)
                if len(values) > 1:
                    field_conflicts[field] = values
                merged[field] = values[0] if values else None

            item = {
                "event_id": group["event_id"],
                "implementing_entity": group["implementing_entity"],
                "subsidiary_name": group["subsidiary_name"],
                "involvement_type": group["involvement_type"],
                "keeper_id": ids[0],
                "delete_ids": ids[1:],
                "merged": merged,
            }
            if field_conflicts:
                item["conflicts"] = field_conflicts
                conflicts.append(item)
            else:
                safe.append(item)
        return safe, conflicts
    finally:
        connection.close()


def write_report(
    safe: list[dict[str, Any]], conflicts: list[dict[str, Any]], out_path: str
) -> None:
    """写出安全合并与保留冲突的统一审计 CSV。"""
    output = Path(out_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "decision",
        "event_id",
        "implementing_entity",
        "subsidiary_name",
        "involvement_type",
        "keeper_id",
        "delete_ids",
        "conflicts",
    ]
    with output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for decision, items in (("merge", safe), ("keep_conflict", conflicts)):
            for item in items:
                writer.writerow(
                    {
                        "decision": decision,
                        "event_id": item["event_id"],
                        "implementing_entity": item["implementing_entity"],
                        "subsidiary_name": item["subsidiary_name"],
                        "involvement_type": item["involvement_type"],
                        "keeper_id": item["keeper_id"],
                        "delete_ids": json.dumps(item["delete_ids"]),
                        "conflicts": json.dumps(
                            item.get("conflicts", {}), ensure_ascii=False
                        ),
                    }
                )


def apply_dedup_plan(db_path: str, safe: list[dict[str, Any]]) -> dict[str, Any]:
    """备份后事务合并安全重复组，并验证删除集合及最终行数。"""
    db = Path(db_path).resolve()
    backup = _backup_db(db, tag="entity_dedup")
    delete_ids = [row_id for item in safe for row_id in item["delete_ids"]]
    connection = sqlite3.connect(db)
    try:
        before_count = connection.execute("SELECT COUNT(*) FROM chinese_entities").fetchone()[0]
        connection.execute("BEGIN IMMEDIATE")
        connection.executemany(
            """
            UPDATE chinese_entities
            SET implementing_entity_en = ?, subsidiary_name_en = ?, entity_en_quality = ?,
                subcontract_amount_raw = ?, subcontract_amount = ?, is_central_soe = ?
            WHERE id = ?
            """,
            [
                tuple(item["merged"][field] for field in _MERGE_FIELDS)
                + (item["keeper_id"],)
                for item in safe
            ],
        )
        connection.executemany(
            "DELETE FROM chinese_entities WHERE id = ?", [(row_id,) for row_id in delete_ids]
        )
        after_count = connection.execute("SELECT COUNT(*) FROM chinese_entities").fetchone()[0]
        remaining_deleted_ids = connection.execute(
            f"SELECT COUNT(*) FROM chinese_entities WHERE id IN "
            f"({','.join('?' for _ in delete_ids)})",
            delete_ids,
        ).fetchone()[0] if delete_ids else 0
        if after_count != before_count - len(delete_ids) or remaining_deleted_ids:
            raise RuntimeError("实体去重后的行数或删除 ID 校验失败")
        connection.commit()
    except sqlite3.Error as exc:
        connection.rollback()
        logger.error("实体去重事务失败: %s", exc)
        raise
    finally:
        connection.close()
    return {"groups_merged": len(safe), "rows_deleted": len(delete_ids), "backup": str(backup)}


def main() -> None:
    """CLI 入口：默认只报告，显式 --apply 才删除安全重复行。"""
    parser = argparse.ArgumentParser(description="保守清理 chinese_entities 真正重复行")
    parser.add_argument("--db", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        db = _require_db(args.db, "主库")
    except ValueError as exc:
        parser.error(str(exc))

    safe, conflicts = build_dedup_plan(str(db))
    write_report(safe, conflicts, args.out)
    summary = {
        "safe_groups": len(safe),
        "safe_rows_to_delete": sum(len(item["delete_ids"]) for item in safe),
        "conflict_groups_kept": len(conflicts),
    }
    if not args.apply:
        print(f"实体去重 dry-run：{summary}；报告={args.out}")
        return
    result = apply_dedup_plan(str(db), safe)
    print(f"实体去重完成：{summary}；{result}；报告={args.out}")


if __name__ == "__main__":
    main()
