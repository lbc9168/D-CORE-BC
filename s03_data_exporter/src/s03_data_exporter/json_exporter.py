"""json_exporter.py
从 bri_re_export.db 导出静态 JSON 文件，供 s04 前端直接读取。

输出（均在 output_dir/）：
  projects_flat.json  — 所有项目完整字段（all_entities / stage_events 已解析为列表）
  tx_flat.json        — 输变电建设专用扁平表（all_entities 已解析为列表）
  mf_flat.json        — 新能源制造业专用扁平表（all_entities 已解析为列表）
  stats_country.json  — 国家维度聚合（地图热力图）
  stats_entity.json   — 企业维度聚合（排名图表）
  stats_timeline.json — 时间维度聚合（终态分布）
"""

import argparse
import json
import sqlite3
from pathlib import Path

from .external_references import (load_references, references_for_row,
                                  resolve_reference_snapshot, summarize_reference_audit)
from .production_capacity import public_production_capacity


def export_json(dst_db: Path, output_dir: Path, src_db: Path | None = None, *, external_references: Path | None = None,
                reference_config: Path | None = None) -> dict:
    """
    从 dst_db 中读取四张表，导出为 JSON 文件。

    Args:
        dst_db:     s03 导出数据库（bri_re_export.db）
        output_dir: JSON 文件输出目录
        src_db:     s02a 原始数据库路径，用于查询 last_updated（可选）
        external_references/reference_config: 显式快照/绑定源库的持久指针；无效时在写出前失败
    """
    snapshot = resolve_reference_snapshot(src_db, external_references, reference_config)
    references, reference_audit = load_references(snapshot, src_db, strict=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(dst_db.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    # ── projects_flat ─────────────────────────────────────────────────────────
    rows = [dict(r) for r in conn.execute("SELECT * FROM projects_flat").fetchall()]
    for row in rows:
        row["external_references"] = references_for_row(row, references, reference_audit)
        # JSON 字符串字段反序列化为列表
        for json_field, default in [
            ("all_entities", []),
            ("primary_entity_path", []),
            ("official_website_links", []),
            ("non_official_website_links", []),
            ("stage_events", []),
            ("generation_sources", []),
            ("technology_tags", []),
            ("technology_tags_en", []),
        ]:
            raw = row.get(json_field)
            if raw:
                try:
                    row[json_field] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    row[json_field] = default
            else:
                row[json_field] = default

    projects_path = output_dir / "projects_flat.json"
    with open(projects_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    print(f"  ✓ projects_flat.json : {len(rows)} 条  ({projects_path.stat().st_size // 1024} KB)")

    # ── tx_flat / mf_flat（任务 3.4：TX/MF 专用扁平表，同 projects_flat 风格）───
    for table in ["tx_flat", "mf_flat"]:
        txmf_rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
        for row in txmf_rows:
            if table == "mf_flat" and "production_capacity_value" in row:
                row["production_capacity_value"], row["production_capacity_unit"] = public_production_capacity(
                    row.get("production_capacity_value"), row.get("production_capacity_unit")
                )
            row["external_references"] = references_for_row(row, references, reference_audit)
            raw = row.get("all_entities")
            if raw:
                try:
                    row["all_entities"] = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    row["all_entities"] = []
            else:
                row["all_entities"] = []
        out_path = output_dir / f"{table}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(txmf_rows, f, ensure_ascii=False, indent=2)
        print(f"  ✓ {table}.json : {len(txmf_rows)} 条")

    # ── 聚合统计表 ────────────────────────────────────────────────────────────
    for table in ["stats_country", "stats_entity", "stats_timeline"]:
        stat_rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
        out_path = output_dir / f"{table}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(stat_rows, f, ensure_ascii=False, separators=(",", ":"))
        print(f"  ✓ {table}.json : {len(stat_rows)} 条")

    conn.close()

    # ── metadata.json（last_updated 来自 s02a project_events.publish_date）───
    last_updated = ""
    if src_db and src_db.exists():
        src_conn = sqlite3.connect(src_db.resolve().as_uri() + "?mode=ro", uri=True)
        row = src_conn.execute("SELECT MAX(publish_date) FROM project_events").fetchone()
        src_conn.close()
        if row and row[0]:
            last_updated = str(row[0])[:10]   # YYYY-MM-DD

    meta = {"last_updated": last_updated, "project_count": len(rows)}
    meta_path = output_dir / "metadata.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    print(f"  ✓ metadata.json : last_updated={last_updated or '(unknown)'}")
    summary = summarize_reference_audit(reference_audit)
    print(f"  external references: {summary}")
    return {"reference_snapshot": str(snapshot) if snapshot else None,
            "reference_summary": summary, "reference_audit": reference_audit}


def main() -> None:
    """Export an existing database to an explicit directory, without rebuilding or using APIs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-db", type=Path)
    parser.add_argument("--external-references", type=Path)
    parser.add_argument("--reference-config", type=Path, help="Source-bound metadata pointer; paths relative to config")
    parser.add_argument("--reference-audit", type=Path, help="Optional private candidate-status report; keep outside public/data")
    args = parser.parse_args()
    result = export_json(args.db, args.output, args.source_db, external_references=args.external_references,
                         reference_config=args.reference_config)
    if args.reference_audit:
        args.reference_audit.parent.mkdir(parents=True, exist_ok=True)
        args.reference_audit.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
