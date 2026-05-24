"""stats_builder.py
从 projects_flat 构建聚合统计表，供可视化前端直接查询。

stats_country  : country_iso × category × year → 项目数 / 总容量
stats_entity   : primary_entity × category × year → 项目数 / 总容量
stats_timeline : year × category × latest_stage → 项目数 / 总容量（终态分布）
"""

import sqlite3
from pathlib import Path


def build_stats(dst_db: Path) -> None:
    """在 dst_db（重）建三张聚合表。幂等：先 DROP 再重建。"""
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

    conn.commit()

    # 打印摘要
    for table in ["stats_country", "stats_entity", "stats_timeline"]:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  ✓ {table}: {count} 行")

    conn.close()
