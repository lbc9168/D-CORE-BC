"""全库能源构成影子汇总 CLI：生成建议类别与标签，但绝不修改正式 category、subcategory 或 ID。"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from s01b_project_extractor.energy_taxonomy import (
    EnergyFacts,
    GENERATION_SOURCES,
    POLICY_VERSION,
    STORAGE_STATUSES,
    aggregate_energy_facts,
    classify_energy,
    extract_energy_facts,
    normalize_generation_sources,
)


def _validate_db(db_path: str) -> None:
    """校验影子汇总所需三张表和事件事实列均存在。"""
    path = Path(db_path)
    if not path.exists():
        raise ValueError(f"数据库文件不存在：{db_path}")
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"canonical_projects", "project_events", "project_classification_shadow"}
        missing_tables = sorted(required.difference(tables))
        if missing_tables:
            raise RuntimeError(f"缺表 {missing_tables}，请先执行能源影子 schema 迁移")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(project_events)")}
        required_columns = {
            "generation_sources_json", "storage_status", "storage_power_mw_raw",
            "storage_energy_mwh_raw", "storage_power_mw", "storage_energy_mwh",
            "energy_composition_evidence",
        }
        missing_columns = sorted(required_columns.difference(columns))
        if missing_columns:
            raise RuntimeError(f"project_events 缺列 {missing_columns}")
    finally:
        conn.close()


def _decode_sources(raw: object) -> tuple[str, ...]:
    """把事件 JSON 发电源解码为受控稳定集合，非法值留给 fallback 解析。"""
    try:
        value = json.loads(str(raw or "[]"))
    except json.JSONDecodeError:
        return ()
    if not isinstance(value, list):
        return ()
    try:
        return normalize_generation_sources(value)
    except ValueError:
        return ()


def _event_facts(row: sqlite3.Row) -> tuple[EnergyFacts, bool]:
    """优先消费结构化事件事实，缺失时从历史文本严格回填并标记来源。"""
    sources = _decode_sources(row["generation_sources_json"])
    status = str(row["storage_status"] or "").strip()
    has_structured = bool(sources) or status in STORAGE_STATUSES
    text = "；".join(
        str(row[name] or "")
        for name in ("capacity_details", "project_summary", "energy_composition_evidence")
        if str(row[name] or "").strip()
    )
    fallback = extract_energy_facts(text)
    if not sources:
        sources = fallback.generation_sources
    if status not in STORAGE_STATUSES:
        status = fallback.storage_status
    evidence = str(row["energy_composition_evidence"] or "").strip() or fallback.evidence
    facts = EnergyFacts(
        sources,
        status,
        str(row["storage_power_mw_raw"] or "").strip() or fallback.storage_power_mw_raw,
        str(row["storage_energy_mwh_raw"] or "").strip() or fallback.storage_energy_mwh_raw,
        row["storage_power_mw"] if status == "confirmed" else None,
        row["storage_energy_mwh"] if status == "confirmed" else None,
        evidence,
    )
    return facts, has_structured


def _scan(db_path: str) -> list[dict]:
    """只读扫描未排除 canonical 及其事件，构建影子分类报告行。"""
    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        projects = conn.execute(
            "SELECT id, project_name, category, subcategory FROM canonical_projects WHERE COALESCE(excluded,0)=0"
        ).fetchall()
        events = conn.execute(
            """SELECT canonical_id, capacity_details, project_summary, generation_sources_json,
               storage_status, storage_power_mw_raw, storage_energy_mwh_raw,
               storage_power_mw, storage_energy_mwh, energy_composition_evidence
               FROM project_events"""
        ).fetchall()
    finally:
        conn.close()

    by_canonical: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for event in events:
        by_canonical[event["canonical_id"]].append(event)

    rows: list[dict] = []
    for project in projects:
        event_facts: list[EnergyFacts] = []
        structured = False
        for event in by_canonical.get(project["id"], []):
            facts, is_structured = _event_facts(event)
            event_facts.append(facts)
            structured = structured or is_structured
        aggregate = aggregate_energy_facts(event_facts)
        classification = classify_energy(aggregate.generation_sources, aggregate.storage_status)
        rows.append({
            "canonical_id": project["id"],
            "project_name": project["project_name"] or "",
            "current_category": project["category"] or "",
            "current_subcategory": project["subcategory"] or "",
            "generation_sources": list(aggregate.generation_sources),
            "storage_status": aggregate.storage_status,
            "storage_power_mw": aggregate.storage_power_mw,
            "storage_energy_mwh": aggregate.storage_energy_mwh,
            "proposed_category": classification.proposed_category,
            "technology_tags": list(classification.technology_tags),
            "classification_version": POLICY_VERSION,
            "needs_review": int(classification.needs_review),
            "review_reason": classification.review_reason,
            "classification_source": "extracted" if structured else "backfill",
            "evidence": aggregate.evidence,
        })
    return rows


def _apply_shadow(db_path: str, rows: list[dict]) -> None:
    """单事务 upsert 非人工锁定的影子快照，不触碰 canonical_projects。"""
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.executemany(
                """INSERT INTO project_classification_shadow
                   (canonical_id, generation_sources_json, storage_status, proposed_category,
                    storage_power_mw, storage_energy_mwh, technology_tags_json,
                    classification_version, needs_review, review_reason,
                    classification_source, manual_locked, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                   ON CONFLICT(canonical_id) DO UPDATE SET
                     generation_sources_json=excluded.generation_sources_json,
                     storage_status=excluded.storage_status,
                     storage_power_mw=excluded.storage_power_mw,
                     storage_energy_mwh=excluded.storage_energy_mwh,
                     proposed_category=excluded.proposed_category,
                     technology_tags_json=excluded.technology_tags_json,
                     classification_version=excluded.classification_version,
                     needs_review=excluded.needs_review,
                     review_reason=excluded.review_reason,
                     classification_source=excluded.classification_source,
                     updated_at=excluded.updated_at
                   WHERE project_classification_shadow.manual_locked=0""",
                [
                    (
                        row["canonical_id"],
                        json.dumps(row["generation_sources"], ensure_ascii=False),
                        row["storage_status"], row["proposed_category"],
                        row["storage_power_mw"], row["storage_energy_mwh"],
                        json.dumps(row["technology_tags"], ensure_ascii=False),
                        row["classification_version"], row["needs_review"],
                        row["review_reason"] or None, row["classification_source"], now,
                    )
                    for row in rows
                ],
            )
    finally:
        conn.close()


def _write_csv(path: str, rows: list[dict]) -> None:
    """把影子差异行写为 Excel 兼容 UTF-8 CSV。"""
    fieldnames = [
        "canonical_id", "project_name", "current_category", "current_subcategory",
        "generation_sources", "storage_status", "proposed_category", "technology_tags",
        "storage_power_mw", "storage_energy_mwh",
        "classification_version", "needs_review", "review_reason", "classification_source", "evidence",
    ]
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            output = dict(row)
            output["generation_sources"] = json.dumps(output["generation_sources"], ensure_ascii=False)
            output["technology_tags"] = json.dumps(output["technology_tags"], ensure_ascii=False)
            writer.writerow(output)


def _write_json(path: str, payload: dict) -> None:
    """把影子摘要与完整报告行写为机器可读 JSON。"""
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run(
    db_path: str,
    *,
    apply: bool = False,
    out_csv: Optional[str] = None,
    out_json: Optional[str] = None,
) -> dict:
    """运行能源分类影子扫描，可选写 shadow 表与 CSV/JSON 报告。"""
    _validate_db(db_path)
    rows = _scan(db_path)
    payload = {
        "projects": len(rows),
        "would_change": sum(
            1 for row in rows
            if row["proposed_category"] and row["proposed_category"] != row["current_category"]
        ),
        "needs_review": sum(row["needs_review"] for row in rows),
        "applied": apply,
        "classification_version": POLICY_VERSION,
        "rows": rows,
    }
    if apply:
        _apply_shadow(db_path, rows)
    if out_csv:
        _write_csv(out_csv, rows)
    if out_json:
        _write_json(out_json, payload)
    return payload


def main() -> None:
    """解析 CLI 参数并执行默认只读的影子分类。"""
    parser = argparse.ArgumentParser(description="能源构成分类影子扫描")
    parser.add_argument("--db", required=True, help="目标 bri_re_database.db")
    parser.add_argument("--apply", action="store_true", help="只写 shadow 表（默认 dry-run）")
    parser.add_argument("--out-csv", default=None, help="差异 CSV 输出路径")
    parser.add_argument("--out-json", default=None, help="机器报告 JSON 输出路径")
    args = parser.parse_args()
    report = run(
        args.db, apply=args.apply, out_csv=args.out_csv, out_json=args.out_json
    )
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
