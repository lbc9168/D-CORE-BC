"""flat_builder.py
从 s02a SQLite 三表结构构建 projects_flat 扁平表，写入导出数据库。

核心逻辑：
- 每个 canonical_project 对应一行
- 容量/阶段取"最终状态"事件（优先级：投入运营 > 竣工 > 在建 > 开工 > 签约 > 中标 > 收购）
- timeline_notes 记录所有阶段及日期的文字摘要（多事件时才生成）
- 实体列表从所有事件合并去重，primary_entity 取最终状态事件的第一个实体
"""

import json
import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd

# 阶段优先级：值越高 = 越"终态"
STAGE_PRIORITY: dict[str, int] = {
    "投入运营": 7,
    "竣工": 6,
    "在建": 5,
    "开工": 4,
    "签约": 3,
    "中标": 2,
    "收购": 1,
    "拟开发": 0,
}

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS projects_flat (
    project_id          TEXT PRIMARY KEY,
    project_name        TEXT,
    project_name_en     TEXT,
    name_en_quality     TEXT,
    country             TEXT,
    country_iso         TEXT,
    bri_country         TEXT,
    ldc                 TEXT,
    location            TEXT,
    province            TEXT,
    category            TEXT,
    capacity_mw         REAL,
    latest_stage        TEXT,
    latest_stage_date   TEXT,
    event_year          INTEGER,
    event_month         TEXT,
    timeline_notes      TEXT,
    event_count         INTEGER,
    financing_model     TEXT,
    primary_entity      TEXT,
    primary_entity_en   TEXT,
    all_entities        TEXT,
    verification_status TEXT,
    record_type         TEXT
)
"""


def _stage_rank(stage: str) -> int:
    return STAGE_PRIORITY.get(stage or "", 0)


def _parse_month(date_str: Optional[str]) -> Optional[str]:
    if not date_str:
        return None
    s = str(date_str)[:7]
    return s if len(s) == 7 and s[4] == "-" else None


def _parse_year(date_str: Optional[str]) -> Optional[int]:
    if not date_str:
        return None
    try:
        return int(str(date_str)[:4])
    except (ValueError, TypeError):
        return None


def _parse_capacity(val: Optional[str]) -> Optional[float]:
    if not val:
        return None
    try:
        return float(str(val).strip())
    except (ValueError, TypeError):
        return None


def build_flat(
    src_db: Path,
    dst_db: Path,
    province_map: dict[str, Optional[str]],
) -> int:
    """
    构建并写入 projects_flat 表。

    Args:
        src_db:       s02a 数据库路径
        dst_db:       s03 导出数据库路径
        province_map: {project_id: province_en_or_None}

    Returns:
        写入的行数
    """
    src_conn = sqlite3.connect(src_db)
    src_conn.row_factory = sqlite3.Row

    projects = [dict(r) for r in src_conn.execute("SELECT * FROM canonical_projects").fetchall()]
    events   = [dict(r) for r in src_conn.execute("SELECT * FROM project_events").fetchall()]
    entities = [dict(r) for r in src_conn.execute("SELECT * FROM chinese_entities").fetchall()]
    src_conn.close()

    # 构建查找索引
    events_by_project: dict[str, list[dict]] = {}
    for ev in events:
        events_by_project.setdefault(ev["canonical_id"], []).append(ev)

    entities_by_event: dict[str, list[dict]] = {}
    for ent in entities:
        entities_by_event.setdefault(ent["event_id"], []).append(ent)

    rows: list[dict] = []

    for proj in projects:
        pid = proj["id"]
        proj_events = events_by_project.get(pid, [])

        if not proj_events:
            continue

        # 按阶段优先级（降序）→ 日期（降序）排序，取第一个为"最终状态"事件
        sorted_events = sorted(
            proj_events,
            key=lambda e: (
                _stage_rank(e.get("project_stage")),
                e.get("publish_date") or e.get("investment_date") or "",
            ),
            reverse=True,
        )
        latest = sorted_events[0]

        # 容量：从最终状态事件往下找第一个有值的
        capacity_mw: Optional[float] = None
        for ev in sorted_events:
            c = _parse_capacity(ev.get("capacity_mw"))
            if c is not None:
                capacity_mw = c
                break

        # timeline_notes：按日期升序，格式"签约: 2022-03 → 开工: 2023-06"
        timeline_notes = ""
        if len(proj_events) > 1:
            tl_events = sorted(
                proj_events,
                key=lambda e: e.get("publish_date") or e.get("investment_date") or "",
            )
            parts: list[str] = []
            for ev in tl_events:
                stage = ev.get("project_stage") or ""
                date = ev.get("publish_date") or ev.get("investment_date") or ""
                ym = str(date)[:7] if date else "??"
                if stage:
                    parts.append(f"{stage}: {ym}")
            timeline_notes = " → ".join(parts)

        # 实体：所有事件合并去重（按最终状态 → 其他顺序），primary 取最终状态第一个
        all_entities_list: list[dict] = []
        seen_entities: set[str] = set()
        for ev in sorted_events:
            for ent in entities_by_event.get(ev["id"], []):
                name = (ent.get("implementing_entity") or "").strip()
                if name and name not in seen_entities:
                    seen_entities.add(name)
                    all_entities_list.append(
                        {
                            "name":            name,
                            "name_en":         ent.get("implementing_entity_en") or "",
                            "subsidiary":      ent.get("subsidiary_name") or "",
                            "subsidiary_en":   ent.get("subsidiary_name_en") or "",
                            "involvement_type": ent.get("involvement_type") or "",
                        }
                    )

        latest_entities = entities_by_event.get(latest["id"], [])
        primary_entity    = (latest_entities[0].get("implementing_entity") or "").strip() if latest_entities else ""
        primary_entity_en = (latest_entities[0].get("implementing_entity_en") or "").strip() if latest_entities else ""

        # 事件年份（取最终状态事件日期的年份）
        event_year = _parse_year(
            latest.get("publish_date") or latest.get("investment_date")
        )

        rows.append(
            {
                "project_id":          pid,
                "project_name":        proj.get("project_name") or "",
                "project_name_en":     proj.get("project_name_en") or "",
                "name_en_quality":     proj.get("name_en_quality") or "",
                "country":             proj.get("country") or "",
                "country_iso":         proj.get("country_iso") or "",
                "bri_country":         proj.get("bri_country") or "",
                "ldc":                 proj.get("ldc") or "",
                "location":            proj.get("location") or "",
                "province":            province_map.get(pid),
                "category":            proj.get("category") or "",
                "capacity_mw":         capacity_mw,
                "latest_stage":        latest.get("project_stage") or "",
                "latest_stage_date":   latest.get("publish_date") or latest.get("investment_date") or "",
                "event_year":          event_year,
                "event_month":         _parse_month(
                    latest.get("publish_date") or latest.get("investment_date")
                ),
                "timeline_notes":      timeline_notes,
                "event_count":         len(proj_events),
                "financing_model":     "" if (fm := (latest.get("financing_model") or "").strip()) in ("Unknown", "unknown", "N/A", "n/a") else fm,
                "primary_entity":      primary_entity,
                "primary_entity_en":   primary_entity_en,
                "all_entities":        json.dumps(all_entities_list, ensure_ascii=False),
                "verification_status": latest.get("verification_status") or "unverified",
                "record_type":         "具体项目",
            }
        )

    # 写入导出数据库
    dst_conn = sqlite3.connect(dst_db)
    dst_conn.execute("DROP TABLE IF EXISTS projects_flat")
    dst_conn.execute(CREATE_TABLE_SQL)

    if rows:
        df = pd.DataFrame(rows)
        df.to_sql("projects_flat", dst_conn, if_exists="append", index=False)

    dst_conn.commit()
    dst_conn.close()

    return len(rows)
