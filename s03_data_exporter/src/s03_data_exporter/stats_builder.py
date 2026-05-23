"""stats_builder.py
从 projects_flat 构建聚合统计表，供可视化前端直接查询。

stats_country        : country_iso × category × year → 项目数 / 总容量
stats_entity         : primary_entity × category × year → 项目数 / 总容量
stats_timeline       : year × category × latest_stage → 项目数 / 总容量（终态分布）
stats_timeline_events: year × month × category × stage × country_iso → 里程碑数 / 总容量
                       （事件级：一个项目在它经历的每个阶段-年份各计一次）
"""

import sqlite3
from pathlib import Path


def build_stats(dst_db: Path, src_db: Path) -> None:
    """在 dst_db（重）建聚合表；事件级表读源库 src_db 的 project_events。幂等：先 DROP 再重建。"""
    conn = sqlite3.connect(dst_db)

    # ── stats_country ────────────────────────────────────────────────────────────
    conn.execute("DROP TABLE IF EXISTS stats_country")
    conn.execute("""
        CREATE TABLE stats_country (
            country_iso         TEXT,
            category            TEXT,
            year                INTEGER,
            project_count       INTEGER,
            total_capacity_mw   REAL,
            PRIMARY KEY (country_iso, category, year)
        )
    """)
    conn.execute("""
        INSERT INTO stats_country
        SELECT
            country_iso,
            category,
            event_year          AS year,
            COUNT(*)            AS project_count,
            SUM(capacity_mw)    AS total_capacity_mw
        FROM projects_flat
        WHERE country_iso IS NOT NULL AND country_iso != ''
          AND event_year  IS NOT NULL
        GROUP BY country_iso, category, event_year
    """)

    # ── stats_entity ─────────────────────────────────────────────────────────────
    conn.execute("DROP TABLE IF EXISTS stats_entity")
    conn.execute("""
        CREATE TABLE stats_entity (
            primary_entity      TEXT,
            primary_entity_en   TEXT,
            category            TEXT,
            year                INTEGER,
            project_count       INTEGER,
            total_capacity_mw   REAL,
            PRIMARY KEY (primary_entity, category, year)
        )
    """)
    conn.execute("""
        INSERT INTO stats_entity
        SELECT
            primary_entity,
            MAX(primary_entity_en)  AS primary_entity_en,
            category,
            event_year              AS year,
            COUNT(*)                AS project_count,
            SUM(capacity_mw)        AS total_capacity_mw
        FROM projects_flat
        WHERE primary_entity IS NOT NULL AND primary_entity != ''
          AND event_year     IS NOT NULL
        GROUP BY primary_entity, category, event_year
    """)

    # ── stats_timeline ───────────────────────────────────────────────────────────
    conn.execute("DROP TABLE IF EXISTS stats_timeline")
    conn.execute("""
        CREATE TABLE stats_timeline (
            year            INTEGER,
            category        TEXT,
            latest_stage    TEXT,
            count           INTEGER,
            capacity_mw     REAL,
            PRIMARY KEY (year, category, latest_stage)
        )
    """)
    conn.execute("""
        INSERT INTO stats_timeline
        SELECT
            event_year      AS year,
            category,
            latest_stage,
            COUNT(*)        AS count,
            SUM(capacity_mw) AS capacity_mw
        FROM projects_flat
        WHERE event_year IS NOT NULL
        GROUP BY event_year, category, latest_stage
    """)

    # ── stats_timeline_events（事件级：每个项目在每个阶段-年份各计一次）──────────
    src = sqlite3.connect(src_db)
    src.row_factory = sqlite3.Row
    ev_rows = src.execute(
        """
        SELECT pe.canonical_id AS cid, cp.category AS category, cp.country_iso AS country,
               pe.project_stage AS stage,
               COALESCE(NULLIF(pe.publish_date,''), pe.investment_date) AS d,
               pe.capacity_mw AS cap
        FROM project_events pe
        JOIN canonical_projects cp ON cp.id = pe.canonical_id
        WHERE COALESCE(cp.excluded,0) = 0
        """
    ).fetchall()
    src.close()

    agg: dict[tuple, dict] = {}
    seen: dict[tuple, set] = {}  # (key)->set(cid)，同项目同年同月同阶段同国只计一次
    for r in ev_rows:
        d = r["d"] or ""
        if len(d) < 4 or not d[:4].isdigit():
            continue
        year = int(d[:4])
        month = d[:7] if (len(d) >= 7 and d[4] == "-") else None
        key = (year, month, r["category"] or "", r["stage"] or "Unknown", r["country"] or "")
        bucket = agg.setdefault(key, {"count": 0, "cap": 0.0})
        s = seen.setdefault(key, set())
        if r["cid"] not in s:
            s.add(r["cid"])
            bucket["count"] += 1
        if r["cap"] is not None:
            bucket["cap"] += r["cap"]

    conn.execute("DROP TABLE IF EXISTS stats_timeline_events")
    conn.execute(
        """
        CREATE TABLE stats_timeline_events (
            year          INTEGER,
            month         TEXT,
            category      TEXT,
            stage         TEXT,
            country_iso   TEXT,
            project_count INTEGER,
            capacity_mw   REAL,
            PRIMARY KEY (year, month, category, stage, country_iso)
        )
        """
    )
    conn.executemany(
        "INSERT INTO stats_timeline_events VALUES (?,?,?,?,?,?,?)",
        [
            (k[0], k[1], k[2], k[3], k[4], v["count"], round(v["cap"], 2))
            for k, v in agg.items()
        ],
    )

    conn.commit()

    # 打印摘要
    for table in ["stats_country", "stats_entity", "stats_timeline", "stats_timeline_events"]:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  ✓ {table}: {count} 行")

    conn.close()
