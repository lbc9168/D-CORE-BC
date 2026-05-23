"""json_exporter.py
从 bri_re_export.db 导出静态 JSON 文件，供 s04 前端直接读取。

输出（均在 output_dir/）：
  projects_flat.json  — 所有项目完整字段（all_entities 已解析为列表）
  stats_country.json        — 国家维度聚合（地图热力图）
  stats_entity.json         — 企业维度聚合（排名图表）
  stats_timeline.json       — 时间维度聚合（终态分布）
  stats_timeline_events.json— 事件级时间聚合（里程碑趋势图）
"""

import json
import sqlite3
from pathlib import Path


def export_json(dst_db: Path, output_dir: Path, src_db: Path | None = None) -> None:
    """
    从 dst_db 中读取四张表，导出为 JSON 文件。

    Args:
        dst_db:     s03 导出数据库（bri_re_export.db）
        output_dir: JSON 文件输出目录
        src_db:     s02a 原始数据库路径，用于查询 last_updated（可选）
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(dst_db)
    conn.row_factory = sqlite3.Row

    # ── projects_flat ─────────────────────────────────────────────────────────
    rows = [dict(r) for r in conn.execute("SELECT * FROM projects_flat").fetchall()]
    for row in rows:
        # JSON 字符串字段反序列化为列表
        for json_field, default in [
            ("all_entities", []),
            ("official_website_links", []),
            ("non_official_website_links", []),
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

    # ── 聚合统计表 ────────────────────────────────────────────────────────────
    for table in ["stats_country", "stats_entity", "stats_timeline", "stats_timeline_events"]:
        stat_rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
        out_path = output_dir / f"{table}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(stat_rows, f, ensure_ascii=False, separators=(",", ":"))
        print(f"  ✓ {table}.json : {len(stat_rows)} 条")

    conn.close()

    # ── metadata.json（last_updated 来自 s02a project_events.publish_date）───
    last_updated = ""
    if src_db and src_db.exists():
        src_conn = sqlite3.connect(src_db)
        row = src_conn.execute("SELECT MAX(publish_date) FROM project_events").fetchone()
        src_conn.close()
        if row and row[0]:
            last_updated = str(row[0])[:10]   # YYYY-MM-DD

    meta = {"last_updated": last_updated, "project_count": len(rows)}
    meta_path = output_dir / "metadata.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    print(f"  ✓ metadata.json : last_updated={last_updated or '(unknown)'}")
