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

# ── 国家英文名查找表（从 WorldCountryList.json 加载） ──────────────────────────
_WORLD_LIST_PATH = (
    Path(__file__).resolve().parents[5]
    / "s01b_project_extractor"
    / "WorldCountryList.json"
)

def _load_iso3_to_en() -> dict[str, str]:
    try:
        with open(_WORLD_LIST_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {c["iso"]: c["name_en"] for c in data if c.get("iso") and c.get("name_en")}
    except Exception:
        return {}

ISO3_TO_EN_NAME: dict[str, str] = _load_iso3_to_en()

# 装机容量不适用的类别（输变电建设、储能、新能源制造业）
NO_CAPACITY_CATS: set[str] = {"储能", "输变电建设", "新能源制造业"}

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

# ── 英文映射字典 ──────────────────────────────────────────────────────────────

CATEGORY_EN: dict[str, str] = {
    "光伏":        "Solar PV",
    "风电":        "Wind Power",
    "水电":        "Hydropower",
    "核电":        "Nuclear Power",
    "火电":        "Thermal Power",
    "地热及余热":   "Geothermal & Waste Heat",
    "生物质":      "Biomass",
    "储能":        "Energy Storage",
    "综合能源":    "Integrated Energy",
    "新能源制造业": "New Energy Manufacturing",
    "输变电建设":   "Transmission & Distribution",
}

STAGE_EN: dict[str, str] = {
    "拟开发":   "Planned",
    "收购":     "Acquired",
    "中标":     "Awarded",
    "签约":     "Signed",
    "开工":     "Construction Started",
    "在建":     "Under Construction",
    "竣工":     "Completed",
    "投入运营": "In Operation",
    "审批":     "Approval",
    "获批":     "Approved",
    "推进":     "Progressing",
    "融资":     "Financing",
    "融资支持":  "Financing Support",
    "融资完成":  "Financing Completed",
    "融资关闭":  "Financial Close",
    "设备供应":  "Equipment Supply",
    "设备制造":  "Equipment Manufacturing",
    "设备发运":  "Equipment Delivery",
    "设计获批":  "Design Approved",
    "计划于2026年同步启动改造两台机组": "Retrofit Start Planned in 2026",
    "Unknown":  "Unknown",
}

FINANCING_MODEL_EN: dict[str, str] = {
    "信贷融资": "Credit Financing",
    "债券融资": "Bond Financing",
    "股权融资": "Equity Financing",
    "混合融资": "Blended Financing",
    "其他":     "Other",
    "Unknown":  "Unknown",
}

INVOLVEMENT_TYPE_EN: dict[str, str] = {
    "BOT/BOOT":    "BOT/BOOT",
    "EPC总承包":   "EPC General Contractor",
    "设计/施工服务方": "Design/Construction Service",
    "股权投资方":  "Equity Investor",
    "设备供应商":  "Equipment Supplier",
    "投融资+EPC":  "Investment + EPC",
    "融资方":      "Financier",
    "Unknown":     "Unknown",
}

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS projects_flat (
    project_id          TEXT PRIMARY KEY,
    project_name        TEXT,
    project_name_en     TEXT,
    name_en_quality     TEXT,
    country             TEXT,
    country_en          TEXT,
    country_iso         TEXT,
    bri_country         TEXT,
    ldc                 TEXT,
    location            TEXT,
    province            TEXT,
    category            TEXT,
    category_en         TEXT,
    capacity_mw         REAL,
    latest_stage        TEXT,
    latest_stage_en     TEXT,
    latest_stage_date   TEXT,
    event_year          INTEGER,
    event_month         TEXT,
    timeline_notes      TEXT,
    event_count         INTEGER,
    financing_model     TEXT,
    financing_model_en  TEXT,
    primary_entity      TEXT,
    primary_entity_en   TEXT,
    all_entities        TEXT,
    has_soe             INTEGER,
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


def _stage_to_english(stage: str) -> str:
    s = (stage or "").strip()
    if not s:
        return ""
    mapped = STAGE_EN.get(s)
    if mapped:
        return mapped
    # 兜底：若仍是中文短语，统一转为英文占位，避免前端出现中文 timeline。
    if any("\u4e00" <= ch <= "\u9fff" for ch in s):
        return "Other Milestone"
    return s


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
                    stage_en = _stage_to_english(stage)
                    parts.append(f"{stage_en}: {ym}")
            timeline_notes = " → ".join(parts)

        # 实体：所有事件合并去重（按最终状态 → 其他顺序），primary 取最终状态第一个
        all_entities_list: list[dict] = []
        seen_entities: set[str] = set()
        for ev in sorted_events:
            for ent in entities_by_event.get(ev["id"], []):
                name = (ent.get("implementing_entity") or "").strip()
                if name and name not in seen_entities:
                    seen_entities.add(name)
                    inv_type = ent.get("involvement_type") or ""
                    all_entities_list.append(
                        {
                            "name":               name,
                            "name_en":            ent.get("implementing_entity_en") or "",
                            "subsidiary":         ent.get("subsidiary_name") or "",
                            "subsidiary_en":      ent.get("subsidiary_name_en") or "",
                            "involvement_type":   inv_type,
                            "involvement_type_en": INVOLVEMENT_TYPE_EN.get(inv_type, inv_type),
                            "is_central_soe":     ent.get("is_central_soe"),
                        }
                    )

        latest_entities = entities_by_event.get(latest["id"], [])
        primary_entity    = (latest_entities[0].get("implementing_entity") or "").strip() if latest_entities else ""
        primary_entity_en = (latest_entities[0].get("implementing_entity_en") or "").strip() if latest_entities else ""

        # 事件年份（取最终状态事件日期的年份）
        event_year = _parse_year(
            latest.get("publish_date") or latest.get("investment_date")
        )

        category     = proj.get("category") or ""

        # 无装机容量意义的类别：清空容量字段
        if category in NO_CAPACITY_CATS:
            capacity_mw = None

        latest_stage = latest.get("project_stage") or ""
        fm_raw       = (latest.get("financing_model") or "").strip()
        financing_model = "" if fm_raw in ("Unknown", "unknown", "N/A", "n/a") else fm_raw
        has_soe = 1 if any(e.get("is_central_soe") == 1 for e in all_entities_list) else 0

        rows.append(
            {
                "project_id":          pid,
                "project_name":        proj.get("project_name") or "",
                "project_name_en":     proj.get("project_name_en") or "",
                "name_en_quality":     proj.get("name_en_quality") or "",
                "country":             proj.get("country") or "",
                "country_en":          ISO3_TO_EN_NAME.get(proj.get("country_iso") or "", ""),
                "country_iso":         proj.get("country_iso") or "",
                "bri_country":         proj.get("bri_country") or "",
                "ldc":                 proj.get("ldc") or "",
                "location":            proj.get("location") or "",
                "province":            province_map.get(pid),
                "category":            category,
                "category_en":         CATEGORY_EN.get(category, category),
                "capacity_mw":         capacity_mw,
                "latest_stage":        latest_stage,
                "latest_stage_en":     STAGE_EN.get(latest_stage, latest_stage),
                "latest_stage_date":   latest.get("publish_date") or latest.get("investment_date") or "",
                "event_year":          event_year,
                "event_month":         _parse_month(
                    latest.get("publish_date") or latest.get("investment_date")
                ),
                "timeline_notes":      timeline_notes,
                "event_count":         len(proj_events),
                "financing_model":     financing_model,
                "financing_model_en":  FINANCING_MODEL_EN.get(financing_model, financing_model),
                "primary_entity":      primary_entity,
                "primary_entity_en":   primary_entity_en,
                "all_entities":        json.dumps(all_entities_list, ensure_ascii=False),
                "has_soe":             has_soe,
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
