"""外科恢复被旧 S02B 模糊合并改写的中方实体名称。

历史行按稳定 ``chinese_entities.id`` 从基线库恢复；本次增量行从指定日期范围的
``projects.db`` 原子提取结果重建。默认只生成 CSV 计划，``--apply`` 才会备份并事务写库。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import sqlite3
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _require_db(path_value: str, label: str) -> Path:
    """校验 SQLite 路径存在且包含必要表。"""
    path = Path(path_value).resolve()
    if not path.is_file():
        raise ValueError(f"{label}不存在: {path}")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        connection.close()
    expected = {"extracted_projects"} if label == "projects.db" else {"chinese_entities"}
    if not expected.issubset(tables):
        raise ValueError(f"{label}缺少表: {sorted(expected - tables)}")
    return path


def _parse_date(value: str, label: str) -> str:
    """校验并返回 ISO 日期。"""
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"{label}必须是 YYYY-MM-DD: {value}") from exc


def _file_sha256(path: Path) -> str:
    """分块计算文件 SHA-256。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_db(db_path: Path, tag: str = "entity_recovery") -> Path:
    """用 SQLite backup API 创建并验证独立恢复前快照。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_name(f"{db_path.name}.pre_{tag}_{timestamp}.bak")
    source = sqlite3.connect(db_path)
    try:
        destination = sqlite3.connect(backup_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()

    verify = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
    try:
        count = verify.execute("SELECT COUNT(*) FROM chinese_entities").fetchone()[0]
    finally:
        verify.close()
    if backup_path.stat().st_size == 0 or count <= 0:
        raise RuntimeError(f"恢复前备份验证失败: {backup_path}")
    return backup_path


def _load_transition_map(current: sqlite3.Connection, baseline: sqlite3.Connection) -> dict[str, set[str]]:
    """由稳定历史 ID 推导旧模糊步骤的原名到现名映射，仅用于重建新增行。"""
    old_names = {
        int(row["id"]): str(row["implementing_entity"] or "")
        for row in baseline.execute("SELECT id, implementing_entity FROM chinese_entities")
    }
    transitions: dict[str, set[str]] = defaultdict(set)
    for row in current.execute("SELECT id, implementing_entity FROM chinese_entities"):
        row_id = int(row["id"])
        if row_id in old_names:
            transitions[old_names[row_id]].add(str(row["implementing_entity"] or ""))
    return transitions


def _candidate_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """返回与 S02A 实体写入有关的候选值元组。"""
    return (
        str(row.get("implementing_entity") or "").strip(),
        str(row.get("implementing_entity_en") or "").strip(),
        str(row.get("subsidiary_name") or "").strip(),
        str(row.get("subsidiary_name_en") or "").strip(),
        str(row.get("involvement_type") or "").strip(),
        str(row.get("subcontract_amount_usd") or "").strip(),
        row.get("is_central_soe"),
    )


def _load_campaign_candidates(
    projects: sqlite3.Connection, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    """加载本次日期范围内已进入 S02A 的具体项目实体原子行。"""
    rows = projects.execute(
        """
        SELECT id, project_name, implementing_entity, implementing_entity_en,
               subsidiary_name, subsidiary_name_en, involvement_type,
               subcontract_amount_usd, is_central_soe, source_link
        FROM extracted_projects
        WHERE imported_to_s02a = 1
          AND date(publish_date) BETWEEN ? AND ?
          AND COALESCE(implementing_entity, '') <> ''
          AND COALESCE(record_type, '具体项目') = '具体项目'
        ORDER BY id
        """,
        (start_date, end_date),
    ).fetchall()
    return [dict(row) for row in rows]


def _assign_group(
    current_rows: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    transitions: dict[str, set[str]],
) -> dict[int, dict[str, Any]]:
    """按精确现名、已观测旧映射和一一消歧顺序匹配一组新增实体。"""
    unique_candidates: dict[tuple[Any, ...], dict[str, Any]] = {}
    for candidate in candidates:
        unique_candidates.setdefault(_candidate_key(candidate), candidate)

    assignments: dict[int, dict[str, Any]] = {}
    remaining_rows = list(current_rows)
    remaining_candidates = list(unique_candidates.values())

    def assign_unique(predicate) -> None:
        """反复提交当前仅有一个候选的行，并从候选池移除。"""
        changed = True
        while changed:
            changed = False
            for row in list(remaining_rows):
                matches = [candidate for candidate in remaining_candidates if predicate(row, candidate)]
                if len(matches) != 1:
                    continue
                candidate = matches[0]
                assignments[int(row["id"])] = candidate
                remaining_rows.remove(row)
                remaining_candidates.remove(candidate)
                changed = True

    assign_unique(
        lambda row, candidate: str(candidate["implementing_entity"] or "").strip()
        == str(row["implementing_entity"] or "").strip()
    )
    assign_unique(
        lambda row, candidate: str(row["implementing_entity"] or "").strip()
        in transitions.get(str(candidate["implementing_entity"] or "").strip(), set())
    )

    if len(remaining_rows) == len(remaining_candidates) == 1:
        assignments[int(remaining_rows[0]["id"])] = remaining_candidates[0]
        remaining_rows.clear()
        remaining_candidates.clear()

    if remaining_rows:
        details = [
            {
                "id": row["id"],
                "event_id": row["event_id"],
                "current": row["implementing_entity"],
                "candidate_names": [candidate["implementing_entity"] for candidate in remaining_candidates],
            }
            for row in remaining_rows
        ]
        raise RuntimeError(f"新增实体无法无歧义重建: {details}")
    return assignments


def build_recovery_plan(
    db_path: str,
    baseline_path: str,
    projects_path: str,
    start_date: str,
    end_date: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """生成历史 ID 恢复和新增 ID 重建计划，不写任何数据库。"""
    current = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    baseline = sqlite3.connect(f"file:{Path(baseline_path).resolve()}?mode=ro", uri=True)
    projects = sqlite3.connect(f"file:{Path(projects_path).resolve()}?mode=ro", uri=True)
    for connection in (current, baseline, projects):
        connection.row_factory = sqlite3.Row

    try:
        baseline_rows = {
            int(row["id"]): dict(row)
            for row in baseline.execute(
                "SELECT id, implementing_entity, implementing_entity_en FROM chinese_entities"
            )
        }
        current_rows = [
            dict(row)
            for row in current.execute(
                "SELECT id, event_id, implementing_entity, implementing_entity_en, "
                "subsidiary_name, involvement_type FROM chinese_entities ORDER BY id"
            )
        ]
        current_ids = {int(row["id"]) for row in current_rows}
        missing_baseline_ids = set(baseline_rows) - current_ids
        if missing_baseline_ids:
            raise RuntimeError(f"当前库缺少 {len(missing_baseline_ids)} 个基线实体 ID")

        plan: list[dict[str, Any]] = []
        for row in current_rows:
            row_id = int(row["id"])
            if row_id not in baseline_rows:
                continue
            expected = baseline_rows[row_id]
            old_cn = str(row["implementing_entity"] or "")
            new_cn = str(expected["implementing_entity"] or "")
            old_en = str(row["implementing_entity_en"] or "")
            new_en = str(expected["implementing_entity_en"] or "")
            if (old_cn, old_en) != (new_cn, new_en):
                plan.append(
                    {
                        "id": row_id,
                        "event_id": row["event_id"],
                        "cohort": "baseline_restore",
                        "old_impl": old_cn,
                        "new_impl": new_cn,
                        "old_impl_en": old_en,
                        "new_impl_en": new_en,
                    }
                )

        transitions = _load_transition_map(current, baseline)
        campaign_candidates = _load_campaign_candidates(projects, start_date, end_date)
        candidates_by_link: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for candidate in campaign_candidates:
            candidates_by_link[str(candidate["source_link"] or "")].append(candidate)

        new_rows_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in current_rows:
            if int(row["id"]) not in baseline_rows:
                new_rows_by_event[str(row["event_id"])].append(row)

        for event_id, event_rows in new_rows_by_event.items():
            event = current.execute(
                "SELECT source_links FROM project_events WHERE id = ?", (event_id,)
            ).fetchone()
            if event is None:
                raise RuntimeError(f"新增实体引用不存在的 event_id: {event_id}")
            try:
                links = json.loads(event["source_links"] or "[]")
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"event {event_id} source_links 不是合法 JSON") from exc
            event_candidates = [
                candidate
                for link in links
                for candidate in candidates_by_link.get(str(link), [])
            ]

            grouped_rows: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
            grouped_candidates: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
            for row in event_rows:
                grouped_rows[
                    (
                        str(row["subsidiary_name"] or "").strip(),
                        str(row["involvement_type"] or "").strip(),
                    )
                ].append(row)
            for candidate in event_candidates:
                grouped_candidates[
                    (
                        str(candidate["subsidiary_name"] or "").strip(),
                        str(candidate["involvement_type"] or "").strip(),
                    )
                ].append(candidate)

            for key, rows_for_key in grouped_rows.items():
                assignments = _assign_group(
                    rows_for_key, grouped_candidates.get(key, []), transitions
                )
                for row in rows_for_key:
                    candidate = assignments[int(row["id"])]
                    old_cn = str(row["implementing_entity"] or "")
                    new_cn = str(candidate["implementing_entity"] or "").strip()
                    old_en = str(row["implementing_entity_en"] or "")
                    new_en = str(candidate["implementing_entity_en"] or "").strip()
                    if (old_cn, old_en) != (new_cn, new_en):
                        plan.append(
                            {
                                "id": int(row["id"]),
                                "event_id": event_id,
                                "cohort": "campaign_rebuild",
                                "old_impl": old_cn,
                                "new_impl": new_cn,
                                "old_impl_en": old_en,
                                "new_impl_en": new_en,
                            }
                        )

        stats = {
            "current_rows": len(current_rows),
            "baseline_rows": len(baseline_rows),
            "campaign_rows": len(current_rows) - len(baseline_rows),
            "planned_changes": len(plan),
            "baseline_changes": sum(row["cohort"] == "baseline_restore" for row in plan),
            "campaign_changes": sum(row["cohort"] == "campaign_rebuild" for row in plan),
        }
        return plan, stats
    finally:
        current.close()
        baseline.close()
        projects.close()


def write_plan_csv(plan: list[dict[str, Any]], out_path: str) -> None:
    """将恢复计划写为 UTF-8 BOM CSV 供人工审计。"""
    output = Path(out_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "id",
        "event_id",
        "cohort",
        "old_impl",
        "new_impl",
        "old_impl_en",
        "new_impl_en",
    ]
    with output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(plan)


def apply_recovery_plan(db_path: str, plan: list[dict[str, Any]]) -> dict[str, Any]:
    """备份后在单事务内按 ID 应用恢复计划并验证 ID/行数不变。"""
    db = Path(db_path).resolve()
    backup = _backup_db(db)
    connection = sqlite3.connect(db)
    try:
        before_count = connection.execute("SELECT COUNT(*) FROM chinese_entities").fetchone()[0]
        before_ids = {
            int(row[0]) for row in connection.execute("SELECT id FROM chinese_entities")
        }
        connection.execute("BEGIN IMMEDIATE")
        connection.executemany(
            "UPDATE chinese_entities "
            "SET implementing_entity = ?, implementing_entity_en = ? WHERE id = ?",
            [(row["new_impl"], row["new_impl_en"] or None, row["id"]) for row in plan],
        )
        after_count = connection.execute("SELECT COUNT(*) FROM chinese_entities").fetchone()[0]
        after_ids = {int(row[0]) for row in connection.execute("SELECT id FROM chinese_entities")}
        if before_count != after_count or before_ids != after_ids:
            raise RuntimeError("恢复过程改变了 chinese_entities 行数或 ID 集合")
        connection.commit()
    except sqlite3.Error as exc:
        connection.rollback()
        logger.error("实体恢复事务失败: %s", exc)
        raise
    finally:
        connection.close()
    return {
        "updated": len(plan),
        "backup": str(backup),
        "backup_sha256": _file_sha256(backup),
    }


def main() -> None:
    """CLI 入口：默认 dry-run，显式 --apply 才落库。"""
    parser = argparse.ArgumentParser(description="恢复旧 S02B 误合并的中方实体名称")
    parser.add_argument("--db", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--projects-db", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    try:
        db = _require_db(args.db, "主库")
        baseline = _require_db(args.baseline, "基线库")
        projects = _require_db(args.projects_db, "projects.db")
        start_date = _parse_date(args.start_date, "start-date")
        end_date = _parse_date(args.end_date, "end-date")
        if start_date > end_date:
            raise ValueError("start-date 不能晚于 end-date")
    except ValueError as exc:
        parser.error(str(exc))

    log_path = str(Path(args.out).with_suffix(".log"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()],
    )
    plan, stats = build_recovery_plan(
        str(db), str(baseline), str(projects), start_date, end_date
    )
    write_plan_csv(plan, args.out)
    logger.info("恢复计划: %s", stats)
    if not args.apply:
        print(f"实体恢复 dry-run：{stats}；计划={args.out}")
        return
    result = apply_recovery_plan(str(db), plan)
    logger.info("实体恢复已应用: %s", result)
    print(f"实体恢复完成：{stats}；{result}；计划={args.out}")


if __name__ == "__main__":
    main()
