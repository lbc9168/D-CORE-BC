"""flat_builder.py
从 s02a SQLite 三表结构构建 projects_flat 扁平表，写入导出数据库。

核心逻辑：
- 每个 canonical_project 对应一行
- 容量/阶段取"最终状态"事件（优先级：投入运营 > 竣工 > 在建 > 开工 > 签约 > 中标 > 收购）
- timeline_notes 记录所有阶段及日期的文字摘要（多事件时才生成）
- 实体列表从所有事件合并去重，primary_entity 取最终状态事件的第一个实体
"""

import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

# ── 枚举单一可信源：健壮上溯找仓库根（含 bri_enums.py），避免脆弱的 parents[N] ──
_root = next(p for p in Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
import bri_enums as enums
import bri_parse

# ── 国家英文名查找表（从 WorldCountryList.json 加载） ──────────────────────────
# parents: flat_builder.py 在 s03_data_exporter/src/s03_data_exporter/ 下，parents[3] = 仓库根
_WORLD_LIST_PATH = (
    Path(__file__).resolve().parents[3]
    / "s01b_project_extractor"
    / "WorldCountryList.json"
)

def _load_iso3_to_en() -> dict[str, str]:
    """从 WorldCountryList.json 读 {ISO3: 英文名}；文件缺失/解析失败时记录错误并返回空表。"""
    try:
        with open(_WORLD_LIST_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {c["iso"]: c["name_en"] for c in data if c.get("iso") and c.get("name_en")}
    except (OSError, json.JSONDecodeError) as e:
        logging.error("加载 WorldCountryList 失败 path=%s err=%s", _WORLD_LIST_PATH, e)
        return {}

ISO3_TO_EN_NAME: dict[str, str] = _load_iso3_to_en()

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS projects_flat (
    project_id          TEXT PRIMARY KEY,
    display_order       INTEGER,
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
    storage_power_mw    REAL,
    storage_energy_mwh  REAL,
    capacity_source     TEXT,
    latest_stage        TEXT,
    latest_stage_en     TEXT,
    latest_stage_date   TEXT,
    event_year          INTEGER,
    event_month         TEXT,
    timeline_notes      TEXT,
    stage_events        TEXT,
    first_event_year    INTEGER,
    last_event_year     INTEGER,
    event_count         INTEGER,
    total_investment_usd REAL,
    financing_model     TEXT,
    financing_model_en  TEXT,
    primary_entity      TEXT,
    primary_entity_en   TEXT,
    project_developer    TEXT,
    project_developer_en TEXT,
    all_entities        TEXT,
    has_soe                    INTEGER,
    verification_status        TEXT,
    official_website_links     TEXT,
    non_official_website_links TEXT,
    record_type                TEXT
)
"""


def _stage_rank(stage: str) -> int:
    """阶段终态优先级：取自 enums（越大越终态，未知 -1）。"""
    return enums.stage_priority(stage or "")


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


def _parse_capacity(val) -> Optional[float]:
    """容量解析复用单一可信源 bri_parse（capacity_mw 现为 REAL，兼容旧文本）。"""
    return bri_parse.parse_number(val)


def _stage_to_english(stage: str) -> str:
    s = (stage or "").strip()
    if not s:
        return ""
    mapped = enums.stage_en(s)
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
        # 非可再生能源等被排除的项目不进 projects_flat（兼容无 excluded 列的旧库）
        if proj.get("excluded", 0):
            continue

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

        # 容量：从最终状态事件往下找第一个有值的；储能/来源取自同一事件
        capacity_mw: Optional[float] = None
        storage_power_mw: Optional[float] = None
        storage_energy_mwh: Optional[float] = None
        capacity_source: Optional[str] = None
        for ev in sorted_events:
            c = _parse_capacity(ev.get("capacity_mw"))
            if c is not None:
                capacity_mw = c
                storage_power_mw = ev.get("storage_power_mw")
                storage_energy_mwh = ev.get("storage_energy_mwh")
                capacity_source = ev.get("capacity_source")
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

        # stage_events：仅含映射到 4 大类的可见事件，{group, year}，按 year 升序
        _stage_events: list[dict] = []
        for ev in proj_events:
            grp = enums.viz_group(ev.get("project_stage") or "")
            if grp is None:
                continue
            yr = _parse_year(ev.get("publish_date") or ev.get("investment_date"))
            if yr is None:
                continue
            _stage_events.append({"group": grp, "year": yr})
        _stage_events.sort(key=lambda x: x["year"])
        first_event_year = _stage_events[0]["year"] if _stage_events else None
        last_event_year = _stage_events[-1]["year"] if _stage_events else None

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
                            "involvement_type_en": enums.involvement_en(inv_type) or inv_type,
                            "is_central_soe":     ent.get("is_central_soe"),
                        }
                    )

        latest_entities = entities_by_event.get(latest["id"], [])
        primary_entity    = (latest_entities[0].get("implementing_entity") or "").strip() if latest_entities else ""
        primary_entity_en = (latest_entities[0].get("implementing_entity_en") or "").strip() if latest_entities else ""

        # 业主：优先代表(终态)事件，缺则取首个有值事件（cn/en 同源于该事件）
        _dev_ev = latest if (latest.get("project_developer") or "").strip() else next(
            (ev for ev in proj_events if (ev.get("project_developer") or "").strip()), latest)
        project_developer    = (_dev_ev.get("project_developer") or "").strip()
        project_developer_en = (_dev_ev.get("project_developer_en") or "").strip()

        # 事件年份（取最终状态事件日期的年份）
        event_year = _parse_year(
            latest.get("publish_date") or latest.get("investment_date")
        )

        category     = proj.get("category") or ""

        # 无装机容量意义的类别：清空容量与储能字段
        if enums.is_no_capacity(category):
            capacity_mw = None
            storage_power_mw = None
            storage_energy_mwh = None
            capacity_source = None

        latest_stage = latest.get("project_stage") or ""

        # financing_model：取所有事件中有效值；多值不一致时取日期最新的那个
        _INVALID_FM = {"", "Unknown", "unknown", "N/A", "n/a"}
        _events_by_date = sorted(
            proj_events,
            key=lambda e: e.get("publish_date") or e.get("investment_date") or "",
        )
        _valid_fm_events = [
            ev for ev in _events_by_date
            if (ev.get("financing_model") or "").strip() not in _INVALID_FM
        ]
        financing_model = _valid_fm_events[-1].get("financing_model", "").strip() if _valid_fm_events else ""

        # total_investment_usd（百万美元）：取代表(终态)事件值；为空则取最新有值事件
        # 弃用"所有事件均值"——同一项目不同阶段披露的投资额并非可平均的同类量
        def _inv(ev) -> Optional[float]:
            v = bri_parse.parse_number(ev.get("total_investment_usd"))
            return v if (v is not None and v > 0) else None

        total_investment_usd = _inv(latest)
        if total_investment_usd is None:
            for ev in sorted(
                proj_events,
                key=lambda e: e.get("publish_date") or e.get("investment_date") or "",
                reverse=True,
            ):
                v = _inv(ev)
                if v is not None:
                    total_investment_usd = v
                    break
        if total_investment_usd is not None:
            total_investment_usd = round(total_investment_usd, 2)

        has_soe = 1 if any(e.get("is_central_soe") == 1 for e in all_entities_list) else 0

        # 官方/非官方网站链接：跨所有事件收集去重
        _seen_off: set[str] = set()
        _seen_noff: set[str] = set()
        official_website_links: list[str] = []
        non_official_website_links: list[str] = []
        for ev in proj_events:
            for link in json.loads(ev.get("official_website_links") or "[]"):
                if link and link not in _seen_off:
                    _seen_off.add(link)
                    official_website_links.append(link)
            for link in json.loads(ev.get("non_official_website_links") or "[]"):
                if link and link not in _seen_noff:
                    _seen_noff.add(link)
                    non_official_website_links.append(link)

        # display_order 排序键：项目所有事件的最早日期（无则置末尾）
        _dates = [
            d for e in proj_events
            for d in (e.get("publish_date") or e.get("investment_date"),)
            if d
        ]
        _earliest = min(_dates) if _dates else "9999-99"

        rows.append(
            {
                "project_id":          pid,
                "_earliest":           _earliest,
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
                "category_en":         enums.category_en(category) or category,
                "capacity_mw":         capacity_mw,
                "storage_power_mw":    storage_power_mw,
                "storage_energy_mwh":  storage_energy_mwh,
                "capacity_source":     capacity_source,
                "latest_stage":        latest_stage,
                "latest_stage_en":     enums.stage_en(latest_stage) or latest_stage,
                "latest_stage_date":   latest.get("publish_date") or latest.get("investment_date") or "",
                "event_year":          event_year,
                "event_month":         _parse_month(
                    latest.get("publish_date") or latest.get("investment_date")
                ),
                "timeline_notes":      timeline_notes,
                "stage_events":        json.dumps(_stage_events, ensure_ascii=False),
                "first_event_year":    first_event_year,
                "last_event_year":     last_event_year,
                "event_count":         len(proj_events),
                "total_investment_usd": total_investment_usd,
                "financing_model":     financing_model,
                "financing_model_en":  enums.financing_en(financing_model) or financing_model,
                "primary_entity":      primary_entity,
                "primary_entity_en":   primary_entity_en,
                "project_developer":    project_developer,
                "project_developer_en": project_developer_en,
                "all_entities":        json.dumps(all_entities_list, ensure_ascii=False),
                "has_soe":                    has_soe,
                "verification_status":        latest.get("verification_status") or "unverified",
                "official_website_links":     json.dumps(official_website_links, ensure_ascii=False),
                "non_official_website_links": json.dumps(non_official_website_links, ensure_ascii=False),
                "record_type":                "具体项目",
            }
        )

    # display_order：按项目最早事件日期升序（老项目在前）；canonical_id 保持稳定不受影响
    rows.sort(key=lambda r: r["_earliest"])
    for i, r in enumerate(rows, start=1):
        r["display_order"] = i
        del r["_earliest"]

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


# ── tx_flat / mf_flat（任务 3.4：TX/MF 专用扁平表，不含 capacity_mw/storage_*）───

_TXMF_BASE_CREATE = """
    project_id       TEXT PRIMARY KEY,
    project_name     TEXT,
    project_name_en  TEXT,
    country          TEXT,
    country_en       TEXT,
    country_iso      TEXT,
    province         TEXT,
    latest_stage     TEXT,
    latest_stage_en  TEXT,
    event_year       INTEGER,
    all_entities     TEXT
"""

_TX_CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS tx_flat (
{_TXMF_BASE_CREATE},
    voltage_kv                  REAL,
    line_length_km               REAL,
    transmission_capacity_mw    REAL
)
"""

_MF_CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS mf_flat (
{_TXMF_BASE_CREATE},
    production_capacity_value   REAL,
    production_capacity_unit    TEXT
)
"""


def _load_source_tables(src_db: Path) -> tuple[list[dict], dict[str, list[dict]], dict[str, list[dict]]]:
    """读 s02a 三表，返回 (projects, events_by_project, entities_by_event)。"""
    src_conn = sqlite3.connect(src_db)
    src_conn.row_factory = sqlite3.Row
    projects = [dict(r) for r in src_conn.execute("SELECT * FROM canonical_projects").fetchall()]
    events   = [dict(r) for r in src_conn.execute("SELECT * FROM project_events").fetchall()]
    entities = [dict(r) for r in src_conn.execute("SELECT * FROM chinese_entities").fetchall()]
    src_conn.close()

    events_by_project: dict[str, list[dict]] = {}
    for ev in events:
        events_by_project.setdefault(ev["canonical_id"], []).append(ev)

    entities_by_event: dict[str, list[dict]] = {}
    for ent in entities:
        entities_by_event.setdefault(ent["event_id"], []).append(ent)

    return projects, events_by_project, entities_by_event


def _build_entities_list(sorted_events: list[dict], entities_by_event: dict[str, list[dict]]) -> list[dict]:
    """按 sorted_events 顺序合并去重实体，形式与 projects_flat.all_entities 一致。"""
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
                        "involvement_type_en": enums.involvement_en(inv_type) or inv_type,
                        "is_central_soe":     ent.get("is_central_soe"),
                    }
                )
    return all_entities_list


def _first_non_null(sorted_events: list[dict], *cols: str) -> tuple:
    """按 sorted_events（终态优先）顺序，取第一个 cols[0] 非空的事件，返回该事件对应各 cols 的值
    （保证多列取自同一事件，禁止跨事件配对）。全部事件该列均空则返回全 None 元组。"""
    for ev in sorted_events:
        if ev.get(cols[0]) is not None:
            return tuple(ev.get(c) for c in cols)
    return tuple(None for _ in cols)


def _build_txmf_rows(
    src_db: Path,
    province_map: dict[str, Optional[str]],
    category_zh: str,
    extra_cols_fn,
) -> list[dict]:
    """构建 tx_flat/mf_flat 共用行数据：基础列 + extra_cols_fn(sorted_events) 给出的专用列。"""
    projects, events_by_project, entities_by_event = _load_source_tables(src_db)

    rows: list[dict] = []
    for proj in projects:
        if proj.get("excluded", 0):
            continue
        if (proj.get("category") or "") != category_zh:
            continue

        pid = proj["id"]
        proj_events = events_by_project.get(pid, [])
        if not proj_events:
            continue

        sorted_events = sorted(
            proj_events,
            key=lambda e: (
                _stage_rank(e.get("project_stage")),
                e.get("publish_date") or e.get("investment_date") or "",
            ),
            reverse=True,
        )
        latest = sorted_events[0]

        latest_stage = latest.get("project_stage") or ""
        event_year = _parse_year(latest.get("publish_date") or latest.get("investment_date"))

        row = {
            "project_id":      pid,
            "project_name":    proj.get("project_name") or "",
            "project_name_en": proj.get("project_name_en") or "",
            "country":         proj.get("country") or "",
            "country_en":      ISO3_TO_EN_NAME.get(proj.get("country_iso") or "", ""),
            "country_iso":     proj.get("country_iso") or "",
            "province":        province_map.get(pid),
            "latest_stage":    latest_stage,
            "latest_stage_en": enums.stage_en(latest_stage) or latest_stage,
            "event_year":      event_year,
            "all_entities":    json.dumps(_build_entities_list(sorted_events, entities_by_event), ensure_ascii=False),
        }
        row.update(extra_cols_fn(sorted_events))
        rows.append(row)

    return rows


def _write_txmf_table(dst_db: Path, table: str, create_sql: str, rows: list[dict]) -> int:
    """DROP+CREATE 目标表并写入 rows（幂等，与 build_flat 写法一致）。"""
    dst_conn = sqlite3.connect(dst_db)
    dst_conn.execute(f"DROP TABLE IF EXISTS {table}")
    dst_conn.execute(create_sql)
    if rows:
        df = pd.DataFrame(rows)
        df.to_sql(table, dst_conn, if_exists="append", index=False)
    dst_conn.commit()
    dst_conn.close()
    return len(rows)


def build_tx_flat(src_db: Path, dst_db: Path, province_map: dict[str, Optional[str]]) -> int:
    """构建并写入 tx_flat 表（输变电建设专用扁平表：voltage_kv/line_length_km/transmission_capacity_mw，
    各列按终态事件优先取第一个非空值，互不要求同源）。"""

    def _extra(sorted_events: list[dict]) -> dict:
        (voltage_kv,) = _first_non_null(sorted_events, "voltage_kv")
        (line_length_km,) = _first_non_null(sorted_events, "line_length_km")
        (transmission_capacity_mw,) = _first_non_null(sorted_events, "transmission_capacity_mw")
        return {
            "voltage_kv":               voltage_kv,
            "line_length_km":           line_length_km,
            "transmission_capacity_mw": transmission_capacity_mw,
        }

    rows = _build_txmf_rows(src_db, province_map, "输变电建设", _extra)
    return _write_txmf_table(dst_db, "tx_flat", _TX_CREATE_SQL, rows)


def build_mf_flat(src_db: Path, dst_db: Path, province_map: dict[str, Optional[str]]) -> int:
    """构建并写入 mf_flat 表（新能源制造业专用扁平表：production_capacity_value/_unit，
    两列必须取自同一事件——按终态事件优先找第一个 value 非空的事件，同事件同时取 value 与 unit）。"""

    def _extra(sorted_events: list[dict]) -> dict:
        value, unit = _first_non_null(sorted_events, "production_capacity_value", "production_capacity_unit")
        return {
            "production_capacity_value": value,
            "production_capacity_unit":  unit,
        }

    rows = _build_txmf_rows(src_db, province_map, "新能源制造业", _extra)
    return _write_txmf_table(dst_db, "mf_flat", _MF_CREATE_SQL, rows)
