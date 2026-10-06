"""duplicate_merger_tool.py
一带一路可再生能源数据库 — 去重合并与 SQLite 入库工具

四阶段处理：
1. 候选筛查：Python 端 fuzzy matching 找潜在跨源重复对
2. LLM 确认：Gemini 确认是否为同一物理项目（阶段可不同）
3. 字段仲裁：LLM 解决冲突字段，合并实体列表
4. SQLite 写入：规范化三表结构（canonical_projects / project_events / chinese_entities）
"""

import glob
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai import types, errors as genai_errors
from pydantic import BaseModel, Field
from rapidfuzz import fuzz

# ── 枚举/解析单一可信源：健壮上溯找仓库根（含 bri_enums.py），避免脆弱的 parents[N] ──
_root = next(p for p in Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
_s01b_src = _root / "s01b_project_extractor" / "src"
if str(_s01b_src) not in sys.path:
    sys.path.insert(0, str(_s01b_src))
import bri_enums as enums
import bri_location
import bri_parse
from s01b_project_extractor.energy_taxonomy_v2 import (
    EnergyFacts,
    POLICY_VERSION as ENERGY_TAXONOMY_VERSION,
    STORAGE_STATUSES,
    aggregate_energy_facts,
    classify_energy,
    normalize_generation_sources,
)

from s02a_duplicate_merger.tools import dedup_cache  # 同包：去重判断结果缓存（断点续跑）
from s02a_duplicate_merger.tools import coordination
from s02a_duplicate_merger.tools.capacity_review_guard import capture_reviewed_capacity, restore_reviewed_capacity

load_dotenv()

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────────────

FUZZY_THRESHOLD = 85          # CN 名称 token_sort_ratio 阈值
FUZZY_THRESHOLD_EN = 80       # EN 名称 token_sort_ratio 阈值（AI 生成，略低）
FUZZY_PARTIAL_THRESHOLD = 90  # CN 名称 partial_ratio 阈值（捕获子串包含关系）
FUZZY_ENTITY_ASSIST = 60      # 实体重叠辅助：名称达到此分 + 实体重叠 → 送 LLM
CAPACITY_TOLERANCE = 0.05     # 装机容量容错率（5%，收紧自 10%）
CAPACITY_BYPASS_SCORE = 97    # 任一名称信号 ≥ 此分时豁免容量检查（收紧自 92）
CAPACITY_EXACT_TOLERANCE = 0.01  # Signal 4 容量精确匹配误差上限（1%）
CAPACITY_MIN_MW = 50             # Signal 4 最低容量门槛（收紧自 0.5MW，减少常见容量误触发）
BROAD_LLM_SCORE = 40             # Signal 5：名称 ≥ 此分 + 实体重叠或容量接近 → 送 LLM
CAPACITY_BROAD_TOLERANCE = 0.15  # Signal 5 容量"接近"判定（15% 以内）
CANONICAL_AUTO_MATCH_SCORE = 97  # 跨批次：名称 ≥ 此分 → 自动匹配，无需 LLM
CANONICAL_LLM_MIN_SCORE    = 70  # 跨批次：[70, 97) → LLM 确认是否匹配已有 canonical
CANONICAL_CAP_GUARD_TOLERANCE = 0.20  # ≥97分自动匹配时的容量安全检查阈值（20%）
LOCATION_ANCHOR_CAP_TOLERANCE = 0.15  # Signal 6 容量"接近"判定（15% 以内）
LOCATION_ANCHOR_MIN_MW = 10           # Signal 6 最低容量门槛（地点相等已是强约束，可低于 Signal 4 的 50）
GEMINI_MODEL      = "gemini-2.5-flash-lite"  # 字段仲裁等非关键 LLM 调用
# 项目合并确认模型：默认稳定版；要换更强模型用环境变量覆盖，不改代码（P3 收口，原写死 preview 版）
LLM_CONFIRM_MODEL = os.getenv("S02A_LLM_CONFIRM_MODEL", "gemini-2.5-flash-lite")
# 去重并行 LLM 核验线程数：默认 3 保守；Gemini 限额大时用环境变量调高（库写仍在主线程串行，线程安全）
DEDUP_WORKERS = int(os.getenv("S02A_DEDUP_WORKERS", "3"))
# 去重确认每批对数：每批跑完即 commit 缓存（断点续跑粒度）
DEDUP_BATCH_SIZE = int(os.getenv("S02A_DEDUP_BATCH", "500"))

# ── SQLite 建表 SQL ────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS non_project_records (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    record_type             TEXT,           -- 设备订单 | 一般合作
    project_name            TEXT,
    country                 TEXT,
    country_iso             TEXT,
    category                TEXT,
    capacity_mw_raw         TEXT,
    capacity_mw             REAL,
    capacity_details        TEXT,
    project_stage           TEXT,
    implementing_entity     TEXT,
    implementing_entity_en  TEXT,
    subsidiary_name         TEXT,
    subsidiary_name_en      TEXT,
    involvement_type        TEXT,
    subcontract_amount_raw  TEXT,
    subcontract_amount      REAL,
    financing_model         TEXT,
    financing_model_details TEXT,
    source_link             TEXT,
    publish_date            TEXT,
    additional_remarks      TEXT,
    created_at              TEXT
);

CREATE TABLE IF NOT EXISTS canonical_projects (
    id               TEXT PRIMARY KEY,   -- e.g. IND-PV-0001
    project_name     TEXT,
    project_name_en  TEXT,
    name_en_quality  TEXT DEFAULT 'ai_translated',  -- ai_translated | source_verified | manual_verified | official
    country          TEXT,
    country_iso      TEXT,
    bri_country      TEXT,
    ldc              TEXT,
    location         TEXT,
    location_norm    TEXT,               -- 规范地名（音译归一；NULL=未归一，消费方回退 location）
    category         TEXT,
    technology_tags_json TEXT NOT NULL DEFAULT '[]',
    excluded         INTEGER DEFAULT 0,   -- 1=非可再生能源等，排除出统计
    excluded_reason  TEXT,
    created_at       TEXT
);

CREATE TABLE IF NOT EXISTS project_classification_shadow (
    canonical_id             TEXT PRIMARY KEY REFERENCES canonical_projects(id),
    generation_sources_json  TEXT NOT NULL DEFAULT '[]',
    storage_status           TEXT NOT NULL DEFAULT 'not_mentioned',
    storage_power_mw         REAL,
    storage_energy_mwh       REAL,
    proposed_category        TEXT,
    technology_tags_json     TEXT NOT NULL DEFAULT '[]',
    classification_version   TEXT NOT NULL,
    needs_review             INTEGER NOT NULL DEFAULT 0,
    review_reason            TEXT,
    classification_source    TEXT NOT NULL DEFAULT 'policy',
    manual_locked            INTEGER NOT NULL DEFAULT 0,
    updated_at               TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_events (
    id                      TEXT PRIMARY KEY,   -- e.g. IND-PV-0001-01
    canonical_id            TEXT NOT NULL REFERENCES canonical_projects(id),
    project_stage           TEXT,
    capacity_mw_raw         TEXT,
    capacity_mw             REAL,
    capacity_details        TEXT,
    storage_power_mw        REAL,
    storage_energy_mwh      REAL,
    generation_sources_json TEXT,
    storage_status          TEXT,
    storage_power_mw_raw    TEXT,
    storage_energy_mwh_raw  TEXT,
    energy_composition_evidence TEXT,
    voltage_kv_raw          TEXT,
    line_length_km_raw      TEXT,
    transmission_capacity_mw_raw TEXT,
    production_capacity_raw TEXT,
    capacity_source         TEXT,
    project_developer       TEXT,
    project_developer_en    TEXT,
    planned_start_date      TEXT,
    planned_complete_date   TEXT,
    total_investment_usd_raw TEXT,
    total_investment_usd    REAL,
    contract_amount_usd_raw TEXT,   -- 合同金额原文短语（EPC/承包合同额，与投资额/融资额不互斥）
    contract_amount_usd     REAL,   -- 合同金额（百万美元）
    financing_amount_usd_raw TEXT,  -- 融资额度原文短语（贷款/授信/银团）
    financing_amount_usd    REAL,   -- 融资额度（百万美元）
    investment_date         TEXT,
    financing_model         TEXT,
    financing_model_details TEXT,
    source_links            TEXT,   -- JSON 数组
    official_website_links     TEXT,   -- JSON 数组（官方网站链接）
    non_official_website_links TEXT,   -- JSON 数组（非官方网站链接）
    publish_date            TEXT,
    additional_remarks      TEXT,
    project_summary         TEXT,
    created_at              TEXT,
    -- 外部信源交叉验证字段（由 cross_verification_tool 填写）
    verification_status     TEXT DEFAULT 'unverified',  -- unverified | verified | unmatched | pending_review
    verification_confidence TEXT,                       -- high | medium | low
    verification_sources    TEXT,                       -- JSON 数组，外部信源 URL
    needs_manual_review     INTEGER DEFAULT 0           -- 0 | 1
);

CREATE TABLE IF NOT EXISTS chinese_entities (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id                TEXT NOT NULL REFERENCES project_events(id),
    implementing_entity     TEXT,
    implementing_entity_en  TEXT,
    subsidiary_name         TEXT,
    subsidiary_name_en      TEXT,
    entity_en_quality       TEXT DEFAULT 'ai_translated',  -- ai_translated | source_verified | manual_verified
    involvement_type        TEXT,
    subcontract_amount_raw  TEXT,
    subcontract_amount      REAL,
    is_central_soe          INTEGER
);
"""


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _clean(val: Any) -> str:
    """转为字符串并清理 NA / nan 值。"""
    s = str(val).strip()
    return "" if s in ("nan", "None", "NA", "N/A", "<NA>", "NaN") else s


ENTITY_IDENTITY_FIELDS = (
    "entity_id", "entity_name", "group_entity_id", "raw_entity_name",
    "identity_status", "entity_evidence_json",
)


def _entity_identity_key(entity: Dict) -> tuple:
    """法律主体 ID 优先；旧格式保留集团、具体子公司和角色，不能只按集团合并。"""
    role = _clean(entity.get("involvement_type"))
    entity_id = _clean(entity.get("entity_id"))
    if entity_id:
        return ("id", entity_id, role)
    return ("legacy", _clean(entity.get("implementing_entity")),
            _clean(entity.get("subsidiary_name")), role)


def _find_existing_entity(conn: sqlite3.Connection, event_id: str, entity: Dict):
    """Find one relation without guessing between namesakes or merging different IDs.

    After intake approval an unlinked raw name can acquire a parent/group name.
    Its exact stored raw mention bridges that change only within this event/role.
    """
    entity_id = _clean(entity.get("entity_id"))
    role = _clean(entity.get("involvement_type"))
    select = ("SELECT id,entity_id,implementing_entity,subsidiary_name FROM chinese_entities "
              "WHERE event_id=? AND COALESCE(involvement_type,'')=? AND ")

    def unique(condition: str, parameters: tuple):
        matches = conn.execute(select + condition, (event_id, role, *parameters)).fetchall()
        if len(matches) > 1:
            raise ValueError(f"Ambiguous entity replay: event={event_id} role={role} "
                             f"entity_id={entity_id!r} rows={[row[0] for row in matches]}")
        return matches[0] if matches else None

    if entity_id:
        match = unique("entity_id=?", (entity_id,))
        if match:
            return match
    match = unique(
        "(COALESCE(entity_id,'')='' OR ?='') AND implementing_entity=? "
        "AND COALESCE(subsidiary_name,'')=?",
        (entity_id, _clean(entity.get("implementing_entity")), _clean(entity.get("subsidiary_name"))),
    )
    if match:
        return match
    raw_name = _clean(entity.get("raw_entity_name"))
    if entity_id and raw_name:
        return unique("COALESCE(entity_id,'')='' AND raw_entity_name=?", (raw_name,))
    return None


def _pending_publication(record: Dict) -> bool:
    """待复核记录留在源库等待定向重解析；旧身份门禁也不得自动放行。"""
    status = _clean(record.get("publication_status"))
    return status == "pending_review" or (
        not status
        and _clean(record.get("exclude_source")) == "entity_verification"
        and _clean(record.get("needs_review")) in {"1", "1.0", "True", "true"}
    )


def _excluded_publication(record: Dict) -> bool:
    """明确排除只留源库审计，不向有效项目传播排除状态或业务事实。"""
    return _clean(record.get("publication_status")) == "excluded" or _clean(
        record.get("excluded")
    ) in {"1", "1.0", "True", "true"}


def _parse_capacity(val: Any) -> Optional[float]:
    """提取装机容量数值，失败返回 None（复用单一可信源 bri_parse）。"""
    return bri_parse.parse_number(val)


_CAPACITY_TOKEN_RE = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)\s*(?:MW(?:ac|dc|p)?|兆瓦)", re.IGNORECASE
)


def _capacity_values(project: Dict) -> list[float]:
    """收集项目的代表容量及 AC/DC 明细值，供去重时比较容量口径。"""
    values: list[float] = []
    for raw in (project.get("capacity_mw"), project.get("pv_capacity_mw")):
        parsed = _parse_capacity(raw)
        if parsed and parsed > 0:
            values.append(parsed)
    details = str(project.get("capacity_details") or "")
    values.extend(float(match) for match in _CAPACITY_TOKEN_RE.findall(details))
    return list(dict.fromkeys(values))


def _capacity_pair_close(
    a: Dict,
    b: Dict,
    tolerance: float,
    *,
    minimum_mw: float = 0,
) -> bool:
    """任一同口径容量值在阈值内即视为容量可兼容。"""
    vals_a = [v for v in _capacity_values(a) if v >= minimum_mw]
    vals_b = [v for v in _capacity_values(b) if v >= minimum_mw]
    return any(abs(x - y) / max(x, y) <= tolerance for x in vals_a for y in vals_b)


def _hard_identity_conflict(a: Dict, b: Dict) -> bool:
    """地点与开发商同时明显冲突时，确定性阻止自动或传递合并。"""
    loc_a, loc_b = _norm_location(a), _norm_location(b)
    loc_conflict = bool(
        loc_a and loc_b and loc_a not in loc_b and loc_b not in loc_a
        and fuzz.ratio(loc_a, loc_b) < 60
    )
    dev_a = str(a.get("project_developer") or "").strip()
    dev_b = str(b.get("project_developer") or "").strip()
    dev_conflict = bool(dev_a and dev_b and fuzz.token_sort_ratio(dev_a, dev_b) < 45)
    return loc_conflict and dev_conflict


def _decode_generation_sources(raw: object) -> tuple[str, ...]:
    """把 JSON 或列表形式的发电源解码为 v2 受控集合，非法值返回空集合。"""
    value = raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw or "[]")
        except json.JSONDecodeError:
            return ()
    if not isinstance(value, (list, tuple)):
        return ()
    try:
        return normalize_generation_sources(value)
    except ValueError:
        return ()


def _project_energy_facts(project: Dict) -> EnergyFacts:
    """把一条 s02a 逻辑项目转换为事件级 v2 原子事实。"""
    status = str(project.get("storage_status") or "not_mentioned").strip()
    if status not in STORAGE_STATUSES:
        status = "unclear"
    confirmed = status == "confirmed"
    return EnergyFacts(
        _decode_generation_sources(project.get("generation_sources_json")),
        status,
        project.get("storage_power_mw_raw") if confirmed else None,
        project.get("storage_energy_mwh_raw") if confirmed else None,
        project.get("storage_power_mw") if confirmed else None,
        project.get("storage_energy_mwh") if confirmed else None,
        str(project.get("energy_composition_evidence") or "").strip(),
    )


def _apply_incoming_energy_taxonomy(project: Dict) -> None:
    """在 canonical 匹配前用事件原子事实确定 category，并清除非 confirmed 储能容量。"""
    if project.get("category") in {"输变电建设", "新能源制造业"}:
        project["generation_sources_json"] = "[]"
        project["storage_status"] = "not_mentioned"
        project["storage_power_mw"] = None
        project["storage_energy_mwh"] = None
        project["storage_power_mw_raw"] = ""
        project["storage_energy_mwh_raw"] = ""
        project["capacity_mw"] = ""
        return
    facts = _project_energy_facts(project)
    project["storage_status"] = facts.storage_status
    project["storage_power_mw"] = facts.storage_power_mw
    project["storage_energy_mwh"] = facts.storage_energy_mwh
    project["storage_power_mw_raw"] = facts.storage_power_mw_raw or ""
    project["storage_energy_mwh_raw"] = facts.storage_energy_mwh_raw or ""
    if project.get("excluded"):
        return
    classification = classify_energy(facts.generation_sources, facts.storage_status)
    if not classification.needs_review and classification.proposed_category:
        project["category"] = classification.proposed_category


def _refresh_canonical_energy_taxonomy(conn: sqlite3.Connection, canonical_id: str) -> None:
    """聚合 canonical 的全部事件事实，更新 shadow，并确定性写入未锁定的正式类别和标签。"""
    project_row = conn.execute(
        "SELECT COALESCE(excluded,0), category FROM canonical_projects WHERE id=?", (canonical_id,)
    ).fetchone()
    if not project_row or project_row[0] or project_row[1] in {"输变电建设", "新能源制造业"}:
        return
    shadow_row = conn.execute(
        "SELECT manual_locked FROM project_classification_shadow WHERE canonical_id=?",
        (canonical_id,),
    ).fetchone()
    if shadow_row and shadow_row[0]:
        return
    event_rows = conn.execute(
        """SELECT generation_sources_json, storage_status,
                  storage_power_mw_raw, storage_energy_mwh_raw,
                  storage_power_mw, storage_energy_mwh,
                  energy_composition_evidence
           FROM project_events WHERE canonical_id=?""",
        (canonical_id,),
    ).fetchall()
    facts = aggregate_energy_facts(
        EnergyFacts(
            _decode_generation_sources(row[0]),
            row[1] if row[1] in STORAGE_STATUSES else "not_mentioned",
            row[2] if row[1] == "confirmed" else None,
            row[3] if row[1] == "confirmed" else None,
            row[4] if row[1] == "confirmed" else None,
            row[5] if row[1] == "confirmed" else None,
            str(row[6] or "").strip(),
        )
        for row in event_rows
    )
    classification = classify_energy(facts.generation_sources, facts.storage_status)
    now = datetime.now().isoformat()
    conn.execute(
        """INSERT INTO project_classification_shadow
           (canonical_id, generation_sources_json, storage_status,
            storage_power_mw, storage_energy_mwh, proposed_category,
            technology_tags_json, classification_version, needs_review,
            review_reason, classification_source, manual_locked, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 's02a_policy', 0, ?)
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
        (
            canonical_id,
            json.dumps(facts.generation_sources, ensure_ascii=False),
            facts.storage_status,
            facts.storage_power_mw,
            facts.storage_energy_mwh,
            classification.proposed_category,
            json.dumps(classification.technology_tags, ensure_ascii=False),
            ENERGY_TAXONOMY_VERSION,
            int(classification.needs_review),
            classification.review_reason or None,
            now,
        ),
    )
    if not classification.needs_review and classification.proposed_category:
        conn.execute(
            """UPDATE canonical_projects
               SET category=?, technology_tags_json=?,
                   subcategory=CASE WHEN subcategory IN ('光储','其他') THEN NULL ELSE subcategory END
               WHERE id=?""",
            (
                classification.proposed_category,
                json.dumps(classification.technology_tags, ensure_ascii=False),
                canonical_id,
            ),
        )


def _get_cat_code(category: str) -> str:
    """category 中文键 → canonical_id 用的 code；未知类别记 warning 并回退 UNK。"""
    code = enums.category_code(category)
    if code is None:
        logger.warning("未知 category «%s»，canonical_id code 回退为 UNK", category)
        return "UNK"
    return code


def _stage_order_sql(col: str = "project_stage") -> str:
    """构造按阶段终态优先级排序的 SQL CASE 片段（值越大越终态）。
    取值来自 enums.stage_priority（单一可信源）；阶段键来自自有 enums，非外部输入。"""
    whens = " ".join(
        f"WHEN '{s}' THEN {enums.stage_priority(s)}" for s in enums.valid_stages()
    )
    return f"CASE {col} {whens} ELSE -1 END"


# ── 数据库初始化 ────────────────────────────────────────────────────────────────

def _init_db(db_path: str) -> sqlite3.Connection:
    parent = os.path.dirname(os.path.abspath(db_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    existing_entity_columns = {row[1] for row in conn.execute("PRAGMA table_info(chinese_entities)")}
    for column in ENTITY_IDENTITY_FIELDS:
        if column not in existing_entity_columns:
            conn.execute(f"ALTER TABLE chinese_entities ADD COLUMN {column} TEXT")
    for col_def in [
        # Explicit historical adjudications are narrower than canonical exclusion.
        # NULL preserves legacy behavior; optional field warnings do not set this.
        "ALTER TABLE project_events ADD COLUMN publication_status TEXT CHECK(publication_status IN ('eligible','pending_review','excluded'))",
        "ALTER TABLE chinese_entities ADD COLUMN publication_status TEXT CHECK(publication_status IN ('eligible','pending_review','excluded'))",
        "ALTER TABLE project_events ADD COLUMN project_summary TEXT",
        "ALTER TABLE project_events ADD COLUMN official_website_links TEXT",
        "ALTER TABLE project_events ADD COLUMN non_official_website_links TEXT",
        "ALTER TABLE chinese_entities ADD COLUMN is_central_soe INTEGER",
        # S2a 数值化新列（idempotent）
        "ALTER TABLE project_events ADD COLUMN capacity_mw_raw TEXT",
        "ALTER TABLE project_events ADD COLUMN storage_power_mw REAL",
        "ALTER TABLE project_events ADD COLUMN storage_energy_mwh REAL",
        "ALTER TABLE project_events ADD COLUMN generation_sources_json TEXT",
        "ALTER TABLE project_events ADD COLUMN storage_status TEXT",
        "ALTER TABLE project_events ADD COLUMN storage_power_mw_raw TEXT",
        "ALTER TABLE project_events ADD COLUMN storage_energy_mwh_raw TEXT",
        "ALTER TABLE project_events ADD COLUMN energy_composition_evidence TEXT",
        "ALTER TABLE project_events ADD COLUMN voltage_kv_raw TEXT",
        "ALTER TABLE project_events ADD COLUMN line_length_km_raw TEXT",
        "ALTER TABLE project_events ADD COLUMN transmission_capacity_mw_raw TEXT",
        "ALTER TABLE project_events ADD COLUMN production_capacity_raw TEXT",
        "ALTER TABLE project_events ADD COLUMN capacity_source TEXT",
        "ALTER TABLE project_events ADD COLUMN total_investment_usd_raw TEXT",
        "ALTER TABLE chinese_entities ADD COLUMN subcontract_amount_raw TEXT",
        "ALTER TABLE non_project_records ADD COLUMN capacity_mw_raw TEXT",
        "ALTER TABLE non_project_records ADD COLUMN subcontract_amount_raw TEXT",
        "ALTER TABLE canonical_projects ADD COLUMN excluded INTEGER DEFAULT 0",
        "ALTER TABLE canonical_projects ADD COLUMN excluded_reason TEXT",
        "ALTER TABLE canonical_projects ADD COLUMN location_norm TEXT",
        "ALTER TABLE canonical_projects ADD COLUMN technology_tags_json TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE project_events ADD COLUMN project_developer_en TEXT",
        # TX/MF 稀疏专用列（阶段3 任务3.1）
        "ALTER TABLE project_events ADD COLUMN voltage_kv REAL",
        "ALTER TABLE project_events ADD COLUMN line_length_km REAL",
        "ALTER TABLE project_events ADD COLUMN transmission_capacity_mw REAL",
        "ALTER TABLE project_events ADD COLUMN production_capacity_value REAL",
        "ALTER TABLE project_events ADD COLUMN production_capacity_unit TEXT",
        # 子类/光伏子项/金额稀疏列（指标扩展 2026-W30）
        "ALTER TABLE canonical_projects ADD COLUMN subcategory TEXT",
        "ALTER TABLE project_events ADD COLUMN pv_capacity_mw REAL",
        "ALTER TABLE project_events ADD COLUMN contract_amount_usd_raw TEXT",
        "ALTER TABLE project_events ADD COLUMN contract_amount_usd REAL",
        "ALTER TABLE project_events ADD COLUMN financing_amount_usd_raw TEXT",
        "ALTER TABLE project_events ADD COLUMN financing_amount_usd REAL",
        # 能源分类 shadow 旧库兼容（表可能来自早期、列不完整的试运行版本）
        "ALTER TABLE project_classification_shadow ADD COLUMN generation_sources_json TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE project_classification_shadow ADD COLUMN storage_status TEXT NOT NULL DEFAULT 'not_mentioned'",
        "ALTER TABLE project_classification_shadow ADD COLUMN storage_power_mw REAL",
        "ALTER TABLE project_classification_shadow ADD COLUMN storage_energy_mwh REAL",
        "ALTER TABLE project_classification_shadow ADD COLUMN proposed_category TEXT",
        "ALTER TABLE project_classification_shadow ADD COLUMN technology_tags_json TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE project_classification_shadow ADD COLUMN classification_version TEXT NOT NULL DEFAULT 'energy_taxonomy_v2.1'",
        "ALTER TABLE project_classification_shadow ADD COLUMN needs_review INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE project_classification_shadow ADD COLUMN review_reason TEXT",
        "ALTER TABLE project_classification_shadow ADD COLUMN classification_source TEXT NOT NULL DEFAULT 'policy'",
        "ALTER TABLE project_classification_shadow ADD COLUMN manual_locked INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE project_classification_shadow ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''",
    ]:
        try:
            conn.execute(col_def)
        except sqlite3.OperationalError:
            pass  # 列已存在（已知无害边界，按 CLAUDE.md 规则1 沉默）
    conn.commit()
    return conn


def _gen_canonical_id(conn: sqlite3.Connection, iso: str, cat_code: str) -> str:
    """生成新 canonical_id，格式 {ISO3}-{CAT}-{NNNN}；按数值取最大尾号 +1（修字符串排序 bug）。"""
    prefix = f"{iso}-{cat_code}-"
    row = conn.execute(
        "SELECT MAX(CAST(substr(id, ?) AS INTEGER)) FROM canonical_projects WHERE id LIKE ?",
        (len(prefix) + 1, prefix + "%"),
    ).fetchone()
    new_num = (row[0] or 0) + 1
    return f"{prefix}{new_num:04d}"


def _gen_event_id(conn: sqlite3.Connection, canonical_id: str) -> str:
    """生成新 event_id，格式 {canonical_id}-{NN}；按数值取最大尾号 +1（修字符串排序 bug）。"""
    prefix = f"{canonical_id}-"
    row = conn.execute(
        "SELECT MAX(CAST(substr(id, ?) AS INTEGER)) FROM project_events "
        "WHERE canonical_id = ? AND id LIKE ?",
        (len(prefix) + 1, canonical_id, prefix + "%"),
    ).fetchone()
    new_num = (row[0] or 0) + 1
    return f"{prefix}{new_num:02d}"


# ── 阶段一：加载 & 归组为逻辑项目 ─────────────────────────────────────────────

def _load_from_db(input_db: str, source_urls: list[str] | None = None) -> tuple[pd.DataFrame, list[int]]:
    """从 s01 projects.db 读取未导入记录（imported_to_s02a=0）。

    Returns:
        (DataFrame with Excel-style column names, list of record IDs to mark later)
    """
    source_urls = coordination.validate_source_urls(source_urls)
    if source_urls == []:
        return pd.DataFrame(), []
    if not os.path.exists(input_db):
        print(f"  [警告] 未找到 projects.db: {input_db}")
        return pd.DataFrame(), []

    conn = sqlite3.connect(input_db)
    query = "SELECT * FROM extracted_projects WHERE imported_to_s02a = 0"
    if source_urls is not None:
        query += " AND source_link IN (" + ",".join("?" for _ in source_urls) + ")"
    df = pd.read_sql(query, conn, params=source_urls)
    held = coordination.held_ids(conn)
    if held:
        df = df.loc[~df["id"].isin(held)].copy()
    conn.close()

    if df.empty:
        return df, []

    pending = df.apply(_pending_publication, axis=1)
    if pending.any():
        logger.info("保留 %s 条企业待复核记录，未导入且不标记 imported_to_s02a", int(pending.sum()))
        df = df.loc[~pending].copy()

    record_ids: list[int] = df["id"].tolist()
    excluded = df.apply(_excluded_publication, axis=1) if not df.empty else pd.Series(dtype=bool)
    if excluded.any():
        logger.info("%s 条明确排除记录仅在源库保留审计，并标记已处理", int(excluded.sum()))
        df = df.loc[~excluded].copy()

    # 将 SQLite 列名映射回 s02a 内部期望的 Excel 列名格式
    df = df.rename(columns={
        "record_type":             "Record Type",
        "project_name":            "Project Name",
        "project_name_en":         "Project Name (EN)",
        "name_en_quality":         "Name EN Quality",
        "country":                 "Country",
        "country_iso":             "Country ISO",
        "bri_country":             "BRI country",
        "ldc":                     "LDCs",
        "location":                "Location (State)",
        "category":                "Category",
        "capacity_mw":             "Capacity (MW)",
        "capacity_details":        "Capacity Details",
        "generation_sources_json": "Generation Sources JSON",
        "storage_status":          "Storage Status",
        "storage_power_mw_raw":    "Storage Power Raw",
        "storage_energy_mwh_raw":  "Storage Energy Raw",
        "storage_power_mw":        "Storage Power MW",
        "storage_energy_mwh":      "Storage Energy MWh",
        "energy_composition_evidence": "Energy Composition Evidence",
        "voltage_kv_raw":         "TX Voltage Evidence",
        "voltage_kv":             "TX Voltage KV",
        "line_length_km_raw":     "TX Line Length Evidence",
        "line_length_km":         "TX Line Length KM",
        "transmission_capacity_mw_raw": "TX Transmission Capacity Evidence",
        "transmission_capacity_mw": "TX Transmission Capacity MW",
        "production_capacity_raw": "MF Production Capacity Evidence",
        "production_capacity_value": "MF Production Capacity Value",
        "production_capacity_unit": "MF Production Capacity Unit",
        "project_stage":           "Project Stage",
        "project_developer":       "Project Developer",
        "project_developer_en":    "Project Developer (EN)",
        "multiple_chinese":        "Multiple Chinese Enterprises Involved",
        "implementing_entity":     "Implementing Chinese Entity",
        "implementing_entity_en":  "Implementing Chinese Entity (EN)",
        "subsidiary_name":         "Subsidiary Name",
        "subsidiary_name_en":      "Subsidiary Name (EN)",
        "involvement_type":        "Type of involvement of Chinese Entities",
        "subcontract_amount_usd":  "Subcontract Amount (Million USD)",
        "planned_start_date":      "(Planned) Start Date",
        "planned_complete_date":   "(Planned) Complete Date",
        "total_investment_usd":    "Total Project Investment (Million USD)",
        "contract_amount_usd":     "Contract Amount (Million USD)",
        "contract_amount_usd_raw": "Contract Amount Raw",
        "financing_amount_usd":    "Financing Amount (Million USD)",
        "financing_amount_usd_raw": "Financing Amount Raw",
        "investment_date":         "Investment (or Financial Close) Date",
        "financing_model":         "Financing Model",
        "financing_model_details": "Financing Model Details",
        "source_link":             "Source Link",
        "publish_date":            "Publish Date",
        "official_website":        "Official Website link",
        "non_official_website":    "Non-official website link",
        "additional_remarks":      "Additional Remarks",
    })

    print(f"  已从 projects.db 加载 {len(df)} 条未导入记录（imported_to_s02a=0）")
    return df, record_ids


def _mark_imported(input_db: str, record_ids: list[int]) -> None:
    """将已处理记录标记为 imported_to_s02a = 1（防止重复导入）。"""
    if not record_ids:
        return
    conn = sqlite3.connect(input_db)
    try:
        placeholders = ",".join("?" * len(record_ids))
        conn.execute(
            f"UPDATE extracted_projects SET imported_to_s02a = 1 WHERE id IN ({placeholders})",
            record_ids,
        )
        conn.commit()
        print(f"  已标记 {len(record_ids)} 条记录为 imported_to_s02a=1")
    except sqlite3.Error as e:
        raise RuntimeError(f"标记 imported_to_s02a 失败: {input_db}: {e}") from e
    finally:
        conn.close()


def _group_into_logical_projects(df: pd.DataFrame) -> List[Dict]:
    """
    将扁平化 Excel 行归组为逻辑项目。
    同一信源 + 同一项目基本信息 → 一个逻辑项目（含多家实体）。
    """
    if df.empty:
        return []
    df = df.loc[~df.apply(lambda row: _pending_publication(row) or _excluded_publication(row), axis=1)].copy()
    key_cols = [
        "Source Link", "Project Name", "Country",
        "Category", "Capacity (MW)", "Project Stage",
    ]
    # 确保所有关键列存在
    for col in key_cols:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("").astype(str).str.strip()

    # 其余列清理 NaN
    for col in df.columns:
        if col not in key_cols:
            df[col] = df[col].fillna("").astype(str).str.strip().replace("nan", "")

    logical_projects: List[Dict] = []
    for _, rows in df.groupby(key_cols, sort=False):
        first = rows.iloc[0]

        # 收集实体（集团 + 具体子公司 + 角色为联合键；同集团不同子公司必须同时保留）
        seen_entities: set = set()
        entities: List[Dict] = []
        for _, row in rows.iterrows():
            entity = {
                    "implementing_entity":    _clean(row.get("Implementing Chinese Entity", "")),
                    "implementing_entity_en": _clean(row.get("Implementing Chinese Entity (EN)", "")),
                    "subsidiary_name":        _clean(row.get("Subsidiary Name", "")),
                    "subsidiary_name_en":     _clean(row.get("Subsidiary Name (EN)", "")),
                    "involvement_type":       _clean(row.get("Type of involvement of Chinese Entities", "")),
                    "subcontract_amount":     _clean(row.get("Subcontract Amount (Million USD)", "")),
                    "is_central_soe":         row.get("is_central_soe"),
                    **{field: _clean(row.get(field)) for field in ENTITY_IDENTITY_FIELDS},
            }
            ent_key = _entity_identity_key(entity)
            if entity["implementing_entity"] and ent_key not in seen_entities:
                seen_entities.add(ent_key)
                entities.append(entity)

        src = _clean(first.get("Source Link", ""))

        # 收集所有行的官方/非官方网站链接，过滤非 URL 值（如纯数字 ID），保序去重
        def _collect_links(col: str) -> List[str]:
            seen: set = set()
            result_links: List[str] = []
            for _, row in rows.iterrows():
                v = _clean(row.get(col, ""))
                if v and v.startswith("http") and v not in seen:
                    seen.add(v)
                    result_links.append(v)
            return result_links

        logical_projects.append({
            "_source_record_ids": [int(v) for v in rows["id"]] if "id" in rows else [],
            "record_type":          _clean(first.get("Record Type", "具体项目")) or "具体项目",
            "project_name":         _clean(first.get("Project Name", "")),
            "project_name_en":      _clean(first.get("Project Name (EN)", "")),
            "name_en_quality":      _clean(first.get("Name EN Quality", "ai_translated")) or "ai_translated",
            "country":              _clean(first.get("Country", "")),
            "country_iso":          _clean(first.get("Country ISO", "")) or "UNK",
            "bri_country":          _clean(first.get("BRI country", "")),
            "ldc":                  _clean(first.get("LDCs", "")),
            "location":             _clean(first.get("Location (State)", "")),
            "category":             _clean(first.get("Category", "")),
            "capacity_mw":          _clean(first.get("Capacity (MW)", "")),
            "capacity_details":     _clean(first.get("Capacity Details", "")),
            "generation_sources_json": _clean(first.get("Generation Sources JSON", "")) or "[]",
            "storage_status":       _clean(first.get("Storage Status", "")) or "not_mentioned",
            "storage_power_mw_raw": _clean(first.get("Storage Power Raw", "")),
            "storage_energy_mwh_raw": _clean(first.get("Storage Energy Raw", "")),
            "storage_power_mw":     _parse_capacity(first.get("Storage Power MW", "")),
            "storage_energy_mwh":   _parse_capacity(first.get("Storage Energy MWh", "")),
            "energy_composition_evidence": _clean(first.get("Energy Composition Evidence", "")),
            "voltage_kv_raw":       _clean(first.get("TX Voltage Evidence", "")),
            "voltage_kv":           _parse_capacity(first.get("TX Voltage KV", "")),
            "line_length_km_raw":   _clean(first.get("TX Line Length Evidence", "")),
            "line_length_km":       _parse_capacity(first.get("TX Line Length KM", "")),
            "transmission_capacity_mw_raw": _clean(
                first.get("TX Transmission Capacity Evidence", "")
            ),
            "transmission_capacity_mw": _parse_capacity(
                first.get("TX Transmission Capacity MW", "")
            ),
            "production_capacity_raw": _clean(
                first.get("MF Production Capacity Evidence", "")
            ),
            "production_capacity_value": _parse_capacity(
                first.get("MF Production Capacity Value", "")
            ),
            "production_capacity_unit": _clean(
                first.get("MF Production Capacity Unit", "")
            ),
            "project_stage":        _clean(first.get("Project Stage", "")),
            "project_developer":    _clean(first.get("Project Developer", "")),
            "project_developer_en": _clean(first.get("Project Developer (EN)", "")),
            "planned_start_date":   _clean(first.get("(Planned) Start Date", "")),
            "planned_complete_date":_clean(first.get("(Planned) Complete Date", "")),
            "total_investment_usd": _clean(first.get("Total Project Investment (Million USD)", "")),
            "contract_amount_usd":  _clean(first.get("Contract Amount (Million USD)", "")),
            "contract_amount_usd_raw":  _clean(first.get("Contract Amount Raw", "")),
            "financing_amount_usd": _clean(first.get("Financing Amount (Million USD)", "")),
            "financing_amount_usd_raw": _clean(first.get("Financing Amount Raw", "")),
            "investment_date":      _clean(first.get("Investment (or Financial Close) Date", "")),
            "financing_model":      _clean(first.get("Financing Model", "")),
            "financing_details":    _clean(first.get("Financing Model Details", "")),
            "source_links":         [src] if src else [],
            "official_website_links":     _collect_links("Official Website link"),
            "non_official_website_links": _collect_links("Non-official website link"),
            "publish_date":         _clean(first.get("Publish Date", "")),
            "additional_remarks":   _clean(first.get("Additional Remarks", "")),
            "project_summary":      _clean(first.get("project_summary", "")),
            "excluded":             0,
            "exclude_reason":       "",
            "entities":             entities,
        })

    return logical_projects


# ── 阶段二：候选筛查 ───────────────────────────────────────────────────────────

def _name_similarity(a: Dict, b: Dict) -> Tuple[bool, int]:
    """
    多信号名称相似度检查，任一信号通过即返回 True。

    信号优先级：
      1. CN token_sort_ratio  ≥ FUZZY_THRESHOLD        （词序无关整体相似）
      2. EN token_sort_ratio  ≥ FUZZY_THRESHOLD_EN      （AI 英文名互验）
      3. CN partial_ratio     ≥ FUZZY_PARTIAL_THRESHOLD  （子串包含关系）

    Returns:
        (通过, 最高分) — 最高分用于决定是否豁免容量检查
    """
    best = 0

    # 信号 1：中文名 token_sort_ratio
    score_cn = fuzz.token_sort_ratio(a["project_name"], b["project_name"])
    best = max(best, score_cn)
    if score_cn >= FUZZY_THRESHOLD:
        return True, best

    # 信号 2：英文名 token_sort_ratio（两者均非空时才计算）
    en_a = a.get("project_name_en", "").strip()
    en_b = b.get("project_name_en", "").strip()
    if en_a and en_b:
        score_en = fuzz.token_sort_ratio(en_a, en_b)
        best = max(best, score_en)
        if score_en >= FUZZY_THRESHOLD_EN:
            return True, best

    # 信号 3：中文名 partial_ratio（捕获一个名称是另一个子串的情况）
    score_partial = fuzz.partial_ratio(a["project_name"], b["project_name"])
    best = max(best, score_partial)
    if score_partial >= FUZZY_PARTIAL_THRESHOLD:
        return True, best

    return False, best


def _entity_overlap(a: Dict, b: Dict) -> bool:
    """检查两条记录是否有共同的中方主体（实体名称精确匹配）。"""
    # 占位实体不是真实公司，不能作为同一项目的实体信号，否则不同项目会被错并。
    def _real_entities(proj: Dict) -> set:
        return {
            e["implementing_entity"]
            for e in proj.get("entities", [])
            if e["implementing_entity"] and e["implementing_entity"] != enums.UNNAMED_ENTITY_PLACEHOLDER
        }

    ents_a = _real_entities(a)
    ents_b = _real_entities(b)
    return bool(ents_a & ents_b)


def _norm_location(p: Dict) -> str:
    """取项目的规范地名：location_norm 优先，回退原 location。"""
    return _clean(p.get("location_norm", "")) or _clean(p.get("location", ""))


def _infer_location_from_name(
    client: genai.Client,
    config: types.GenerateContentConfig,
    projects: List[Dict],
    batch_size: int = 30,
) -> None:
    """从项目名推断 location，原地回填空 location 字段。

    仅处理 location 为空的记录。提取不出则保持空字符串，不强行填写。
    使用轻量模型 GEMINI_MODEL 批量调用（每批 batch_size 条）。
    """
    targets = [p for p in projects if not _clean(p.get("location", ""))]
    if not targets:
        return

    print(f"  [location 推断] 共 {len(targets)} 条记录缺少 location，开始批量推断...")

    filled = 0
    for i in range(0, len(targets), batch_size):
        batch = targets[i : i + batch_size]
        items = [
            {"id": j, "project_name": p["project_name"], "country": p["country"]}
            for j, p in enumerate(batch)
        ]
        prompt = (
            "你是能源项目数据处理专家。请从以下项目名称中提取项目所在的省/州/城市/地区名称。\n\n"
            "规则：\n"
            "1. 只提取项目所在地的地名（省/州/邦/城市/地区等），不包括国家名本身\n"
            "2. 如果项目名称中没有明确地名，返回空字符串\n"
            "3. 保留原始语言（中文名提取中文地名，英文名提取英文地名）\n"
            "4. 只取最具体的地名（同时有省和城市时取城市，只有省则取省）\n\n"
            f"输入（JSON 数组）：\n{json.dumps(items, ensure_ascii=False)}\n\n"
            '只输出如下格式的 JSON，不要有其他文字：\n'
            '{"results": [{"id": 0, "location": "地名或空字符串"}]}'
        )

        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=config,
            )
            result = json.loads(resp.text)
            for entry in result.get("results", []):
                idx = entry.get("id")
                loc = _clean(entry.get("location", ""))
                if loc and isinstance(idx, int) and 0 <= idx < len(batch):
                    batch[idx]["location"] = loc
                    filled += 1
        except Exception as e:
            print(f"  [location 推断] 批次 {i // batch_size + 1} 失败，跳过：{e}")

    print(f"  [location 推断] 完成，成功填充 {filled}/{len(targets)} 条")


def _apply_location_norm(projects: List[Dict], client) -> int:
    """批内地点归一化（in-memory）：按国家分组调 bri_location，写 project["location_norm"]。

    让 Signal 6 锚点能命中音译变体；失败仅记日志，锚点回退原 location，不阻塞主流程。
    返回应用条数。
    """
    if client is None:
        return 0
    by_country: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for p in projects:
        loc = _clean(p.get("location", ""))
        if loc:
            by_country[p["country"]][loc] += 1
    grouped = {c: list(d.items()) for c, d in by_country.items() if len(d) >= 2}
    if not grouped:
        return 0
    try:
        mapping = bri_location.build_location_mapping(grouped, client)
    except Exception as e:
        logging.error("批内地点归一化失败，Signal 6 锚点将回退原 location: %s", e)
        return 0
    applied = 0
    for p in projects:
        norm = mapping.get(p["country"], {}).get(_clean(p.get("location", "")))
        if norm:
            p["location_norm"] = norm
            applied += 1
    return applied


def _find_duplicate_candidates(projects: List[Dict]) -> List[Tuple[int, int, float]]:
    """
    找出潜在的跨源重复对，采用多信号 OR 逻辑以最大化召回率。

    硬过滤（必须同时满足）：
      - 相同国家；跨类别时必须满足极强名称或名称+地点+容量救援条件
      - 不共享信源链接

    软信号（满足任一即送 LLM 确认）：
      - 名称信号：CN token_sort_ratio / EN token_sort_ratio / CN partial_ratio
      - 实体辅助：共有中方实体 + 名称中度相似（≥ FUZZY_ENTITY_ASSIST）
      - Signal 4（跨语言容量信道）：容量在 1% 误差内且 ≥ 0.5MW
        适用于翻译机制加入前的历史数据：一方以英文作 project_name，
        另一方以中文，名称模糊得分极低（5~30），但容量精确匹配。
      - Signal 6（地点/容量硬锚点）：所有名称/实体/容量信号均未命中时，
        规范地名相同（音译归一后）+ 容量 15% 以内 → 锚定候选。

    容量豁免：
      - 任一名称信号得分 ≥ CAPACITY_BYPASS_SCORE 时跳过容量检查
        （名称已高度吻合，容量差异由 LLM 判断）
      - Signal 4/5/6 路径已在触发时完成容量验证，不再重复检查

    返回 (i, j, best_score)，best_score 用于高分直接确认判断。
    """
    candidates: List[Tuple[int, int, float]] = []

    # 预分组：在同一国家内比较，以允许类别纠正后仍找回原 canonical ID。
    # P3 (a)：去掉 project_stage 维度——同一物理项目的签约/开工/竣工记录必须能进同一候选组
    groups: Dict[Tuple, List[int]] = defaultdict(list)
    for idx, p in enumerate(projects):
        groups[(p["country"],)].append(idx)

    for group_indices in groups.values():
        ng = len(group_indices)
        for gi in range(ng):
            for gj in range(gi + 1, ng):
                i, j = group_indices[gi], group_indices[gj]
                a, b = projects[i], projects[j]

                # ── 硬过滤（国家/类别/阶段已由分组保证）────────────────────
                if set(a["source_links"]) & set(b["source_links"]):
                    continue

                # ── 名称多信号匹配 ───────────────────────────────────────────
                name_ok, best_score = _name_similarity(a, b)

                # 跨类别救援仅接受极强名称，或中强名称叠加同地点与相近容量。
                if a.get("category") != b.get("category"):
                    loc_a, loc_b = _norm_location(a), _norm_location(b)
                    anchored = (
                        best_score >= CANONICAL_LLM_MIN_SCORE
                        and loc_a
                        and loc_a == loc_b
                        and _capacity_pair_close(a, b, CAPACITY_BROAD_TOLERANCE)
                    )
                    if best_score < CANONICAL_AUTO_MATCH_SCORE and not anchored:
                        continue

                # ── 实体重叠辅助（名称中度相似 + 同一中方实体）──────────────
                entity_boost = (
                    not name_ok
                    and best_score >= FUZZY_ENTITY_ASSIST
                    and _entity_overlap(a, b)
                )

                # ── Signal 4：跨语言容量信道（安全网）──────────────────────
                # 触发条件：名称信号均未命中 + 容量在 1% 误差内且 ≥ 0.5MW
                # 跨语言名称（中文 vs 英文）的 token_sort_ratio 通常仅 5~30，
                # 无法通过 Signals 1-3，改用精确容量匹配作为触发条件
                cross_lang_boost = False
                if not name_ok and not entity_boost:
                    if _capacity_pair_close(
                        a, b, CAPACITY_EXACT_TOLERANCE, minimum_mw=CAPACITY_MIN_MW
                    ):
                        cross_lang_boost = True

                # ── Signal 5：宽松 LLM 候选（所有前置信号均未命中的补救）──
                # 触发条件：名称中度相似（≥ 56）+ 实体重叠 OR 容量在 15% 以内
                broad_llm_boost = False
                if not name_ok and not entity_boost and not cross_lang_boost:
                    if best_score >= BROAD_LLM_SCORE:
                        capacity_close = _capacity_pair_close(
                            a, b, CAPACITY_BROAD_TOLERANCE, minimum_mw=CAPACITY_MIN_MW
                        )
                        if _entity_overlap(a, b) or capacity_close:
                            broad_llm_boost = True
                            print(
                                f"  [Signal 5] 宽松候选: "
                                f"[{a['project_name'][:20]}] vs [{b['project_name'][:20]}] "
                                f"(name={best_score})"
                            )

                # ── Signal 6（P3b 地点/容量硬锚点）：名称信号全部未命中的最后防线 ──
                # 音译归一后的同地名 + 容量接近 → 候选（如 达瓦德密 vs 达旺德米 的同一风电场）
                location_boost = False
                if not name_ok and not entity_boost and not cross_lang_boost and not broad_llm_boost:
                    loc_a, loc_b = _norm_location(a), _norm_location(b)
                    if loc_a and len(loc_a) >= 2 and loc_a == loc_b:
                        if _capacity_pair_close(
                            a,
                            b,
                            LOCATION_ANCHOR_CAP_TOLERANCE,
                            minimum_mw=LOCATION_ANCHOR_MIN_MW,
                        ):
                            location_boost = True
                            print(
                                f"  [Signal 6] 地点锚点候选: "
                                f"[{a['project_name'][:20]}] vs [{b['project_name'][:20]}] "
                                f"(location={loc_a})"
                            )

                if not name_ok and not entity_boost and not cross_lang_boost and not broad_llm_boost and not location_boost:
                    continue

                # ── 容量检查（名称高度相似时豁免；Signal 4/5/6 路径已验证容量）
                if not cross_lang_boost and not broad_llm_boost and not location_boost and best_score < CAPACITY_BYPASS_SCORE:
                    if _capacity_values(a) and _capacity_values(b):
                        if not _capacity_pair_close(a, b, CAPACITY_TOLERANCE):
                            continue

                candidates.append((i, j, best_score))

    return candidates


# ── 阶段三：LLM 确认 & 合并 ────────────────────────────────────────────────────

def _llm_call_with_retry(fn, *args, max_retries: int = 5, **kwargs):
    """LLM 调用包装：限速（429）+ 瞬时网络/socket 错误均指数退避重试，其他异常直接上抛。

    socket 错误（如 Windows 高并发下 WinError 10048 端口耗尽）必须重试——否则被上层
    _llm_confirm 当成"不是同一项目"静默漏并、污染去重；退避还能让 TIME_WAIT 端口排空。
    """
    delay = 2.0
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if attempt >= max_retries:
                raise
            msg = str(e).lower()
            transient = (
                "429" in msg or "resource_exhausted" in msg or "quota" in msg or "rate" in msg
                or "10048" in msg or "winerror" in msg or "socket" in msg or "connection" in msg
                or "timed out" in msg or "timeout" in msg or "temporarily" in msg or "unavailable" in msg
            )
            if transient:
                print(f"  [瞬时错误重试 {attempt + 1}/{max_retries}] {type(e).__name__} 等待 {delay:.0f}s…")
                time.sleep(delay)
                delay = min(delay * 2, 60)
            else:
                raise


def _fmt_entities(project: Dict) -> str:
    """格式化中方实体列表为可读字符串。"""
    ents = [
        e["implementing_entity"]
        for e in project.get("entities", [])
        if e.get("implementing_entity")
    ]
    return "、".join(ents) if ents else "未知"


_DEDUP_DECISION_RULES = """判断依据（按优先级）：
1. 必须至少存在一个项目身份锚点：相同专名、项目公司/开发商、精确场址/项目用地，或信源明确说明是同一物理工程；同国、同省、容量相同/相近或同篇报道本身均不足以合并。
2. 项目公司、开发商或具体场址明显冲突时不匹配；同一电站的不同建设期（Phase I/II 等）也不得合并。
3. AC/DC 或 MWac/MWp 是同一光伏项目的不同容量口径，不因数值差异直接拆分；其他无法解释的容量差异大于 10% 时倾向不匹配。
4. 签约、开工、竣工和投运可以是同一项目生命周期的不同事件；名称的简称、全称、中英混用和通用后缀差异不单独构成拆分依据。
5. 同一仪式、框架协议、合作伙伴或部分地点重叠不等于同一项目；项目数量、总容量或地域组合不同的项目包应判 partial_overlap，不得合并。"""

_DEDUP_RELATIONSHIPS = {
    "exact_duplicate",
    "same_project_different_event",
    "partial_overlap",
    "distinct",
}
_PROJECT_PACKAGE_RE = re.compile(
    r"\d+\s*座|项目包|项目组合|一揽子|一批项目|portfolio|cluster|multiple projects|several projects",
    re.IGNORECASE,
)


def _parse_dedup_relationship(result: dict) -> str:
    """解析四态去重结论，并兼容旧版 same_project 布尔输出。"""
    if not isinstance(result, dict):
        raise ValueError("去重响应必须为对象")
    relationship = str(result.get("relationship", "") or "").strip()
    if relationship in _DEDUP_RELATIONSHIPS:
        return relationship
    if result.get("same_project") is True:
        return "same_project_different_event"
    if result.get("same_project") is False:
        return "distinct"
    raise ValueError("去重响应缺少有效relationship或显式same_project布尔值")


def _looks_like_project_package(project: Dict) -> bool:
    """识别需跳过高分自动合并、交由四态判断的多项目包表述。"""
    surface = " ".join(
        str(project.get(key, "") or "")
        for key in ("project_name", "project_name_en", "capacity_details", "project_summary")
    )
    return bool(_PROJECT_PACKAGE_RE.search(surface))


def _llm_confirm_raising(
    client: genai.Client,
    config: types.GenerateContentConfig,
    a: Dict,
    b: Dict,
) -> bool:
    """确认两条记录是否同一物理项目（阶段可不同）；LLM/解析持久失败时**抛出**（供缓存标 failed）。"""
    src_a = a["source_links"][0] if a["source_links"] else "未知"
    src_b = b["source_links"][0] if b["source_links"] else "未知"
    prompt = f"""
请判断以下两条记录是否描述同一个能源项目（同一物理工程；项目阶段可以不同——签约/开工/竣工/投运属于同一项目生命周期的不同阶段事件）。

记录A：
- 项目名称（中）：{a['project_name']}
- 项目名称（英）：{a.get('project_name_en', '—')}
- 国家：{a['country']}，省/地区/城市：{a['location'] or '未知'}
- 能源类型：{a['category']}，装机容量：{a['capacity_mw'] or '未知'} MW
- 容量明细：{a.get('capacity_details') or '未知'}
- 项目阶段：{a['project_stage']}
- 项目开发商：{a.get('project_developer') or '未知'}
- 中方实体：{_fmt_entities(a)}
- 项目摘要：{a.get('project_summary') or '未知'}
- 信源：{src_a}

记录B：
- 项目名称（中）：{b['project_name']}
- 项目名称（英）：{b.get('project_name_en', '—')}
- 国家：{b['country']}，省/地区/城市：{b['location'] or '未知'}
- 能源类型：{b['category']}，装机容量：{b['capacity_mw'] or '未知'} MW
- 容量明细：{b.get('capacity_details') or '未知'}
- 项目阶段：{b['project_stage']}
- 项目开发商：{b.get('project_developer') or '未知'}
- 中方实体：{_fmt_entities(b)}
- 项目摘要：{b.get('project_summary') or '未知'}
- 信源：{src_b}

{_DEDUP_DECISION_RULES}

严格返回 JSON（无其他内容）：
{{"relationship": "exact_duplicate|same_project_different_event|partial_overlap|distinct", "confidence": "high|medium|low", "reasoning": "一句话理由"}}
    """
    resp = _llm_call_with_retry(
        client.models.generate_content,
        model=LLM_CONFIRM_MODEL, contents=prompt, config=config,
    )
    result = json.loads(resp.text)
    if isinstance(result, list):
        result = result[0]
    relationship = _parse_dedup_relationship(result)
    if relationship == "partial_overlap" or (relationship != "distinct" and result.get("confidence") == "low"):
        raise coordination.CoordinationRequired({
            "kind": "batch_identity", "model_result": result, "raw_response": resp.text,
            "reason": result.get("reasoning", ""),
            "source_record_ids": sorted(set(a.get("_source_record_ids", []) + b.get("_source_record_ids", []))),
            "incoming": deepcopy(a), "candidate": deepcopy(b),
        })
    if relationship in {"exact_duplicate", "same_project_different_event"} and result.get("confidence") != "low":
        return True
    if relationship in {"exact_duplicate", "same_project_different_event"} and result.get("confidence") == "low":
        print(f"    → [低置信度，不合并] {result.get('reasoning', '')}")
    return False


def _llm_confirm(
    client: genai.Client,
    config: types.GenerateContentConfig,
    a: Dict,
    b: Dict,
) -> bool:
    """Compatibility entry point; an unavailable verdict is never a negative match."""
    return _llm_confirm_raising(client, config, a, b)


def _llm_confirm_canonical(
    client: genai.Client,
    config: types.GenerateContentConfig,
    project: Dict,
    db_id: str,
    db_name: str,
    db_name_en: str,
    db_location: str,
    db_capacity: str,
    db_capacity_details: str = "",
    db_developer: str = "",
    db_entities: str = "",
) -> bool:
    """调用 Gemini 确认新记录是否与库中已有 canonical project 为同一物理项目。"""
    prompt = f"""
请判断以下"新记录"与"库中已有项目"是否描述同一个能源项目（同一物理工程）。

新记录：
- 项目名称（中）：{project['project_name']}
- 项目名称（英）：{project.get('project_name_en', '—')}
- 国家：{project['country']}，省/地区/城市：{project['location'] or '未知'}
- 能源类型：{project['category']}，装机容量：{project['capacity_mw'] or '未知'} MW
- 容量明细：{project.get('capacity_details') or '未知'}
- 项目阶段：{project['project_stage']}
- 项目开发商：{project.get('project_developer') or '未知'}
- 中方实体：{_fmt_entities(project)}
- 项目摘要：{project.get('project_summary') or '未知'}

库中已有项目（ID: {db_id}）：
- 项目名称（中）：{db_name}
- 项目名称（英）：{db_name_en or '—'}
- 国家：{project['country']}，省/地区/城市：{db_location or '未知'}
- 能源类型：{project['category']}，装机容量（代表值）：{db_capacity or '未知'} MW
- 容量明细：{db_capacity_details or '未知'}
- 项目开发商：{db_developer or '未知'}
- 中方实体：{db_entities or '未知'}

{_DEDUP_DECISION_RULES}


严格返回 JSON（无其他内容）：
{{"relationship": "exact_duplicate|same_project_different_event|partial_overlap|distinct", "confidence": "high|medium|low", "reasoning": "一句话理由"}}
    """
    resp = _llm_call_with_retry(
        client.models.generate_content,
        model=LLM_CONFIRM_MODEL, contents=prompt, config=config,
    )
    result = json.loads(resp.text)
    if isinstance(result, list):
        result = result[0]
    relationship = _parse_dedup_relationship(result)
    historical = dict(project_name=db_name, project_name_en=db_name_en, location=db_location,
                      capacity_mw=db_capacity, capacity_details=db_capacity_details,
                      project_developer=db_developer, entities=db_entities)
    hard_conflict = _hard_identity_conflict(project, historical)
    if relationship == "partial_overlap" or (relationship != "distinct" and (result.get("confidence") == "low" or hard_conflict)):
        raise coordination.CoordinationRequired({
            "kind": "historical_identity", "candidate_canonical_id": db_id,
            "model_result": result, "raw_response": resp.text,
            "reason": result.get("reasoning", ""), "historical": historical,
            "conflict_fields": {k: {"incoming": project.get(k), "historical": historical[k]}
                                for k in ("project_name", "location", "capacity_mw", "capacity_details", "project_developer")
                                if str(project.get(k) or "") != str(historical[k] or "")},
        })
    if relationship in {"exact_duplicate", "same_project_different_event"} and result.get("confidence") != "low":
        return True
    if relationship in {"exact_duplicate", "same_project_different_event"} and result.get("confidence") == "low":
        print(f"    → [低置信度，不匹配] {result.get('reasoning', '')}")
    return False



def _llm_choose_value(
    client: genai.Client,
    config: types.GenerateContentConfig,
    field: str,
    values: List[str],
    project_name: str,
) -> str:
    """LLM 从多个冲突值中选择最可信的一个。"""
    prompt = f"""项目：{project_name}
字段：{field}
候选值：{values}

请选择最可信或最完整的值。
严格返回 JSON：{{"chosen": "选择的值", "reasoning": "一句话理由"}}
    """
    resp = _llm_call_with_retry(
        client.models.generate_content,
        model=GEMINI_MODEL, contents=prompt, config=config,
    )
    result = json.loads(resp.text)
    if not isinstance(result, dict) or result.get("chosen") not in values:
        raise ValueError("冲突仲裁必须返回候选集合中的明确值")
    return result["chosen"]



def _merge_group(
    client: genai.Client,
    config: types.GenerateContentConfig,
    records: List[Dict],
) -> Dict:
    """将一组确认重复的记录合并为一条。"""
    eligible = [r for r in records if not _pending_publication(r) and not _excluded_publication(r)]
    if not eligible:
        # 调用方写库守卫仍会跳过；不能伪造有效项目。
        return records[0].copy()
    records = eligible
    base = records[0].copy()
    base["_source_record_ids"] = sorted({i for r in records for i in r.get("_source_record_ids", [])})
    base["_coordination_conflicts"] = [e for r in records for e in r.get("_coordination_conflicts", [])]

    # 合并信源链接（保序去重）
    seen_links: set = set()
    merged_links: List[str] = []
    for r in records:
        for link in r["source_links"]:
            if link and link not in seen_links:
                seen_links.add(link)
                merged_links.append(link)
    base["source_links"] = merged_links

    base["excluded"] = 0
    base["exclude_reason"] = ""

    # 合并官方/非官方网站链接（保序去重）
    for link_key in ("official_website_links", "non_official_website_links"):
        seen_wl: set = set()
        merged_wl: List[str] = []
        for r in records:
            for link in r.get(link_key, []):
                if link and link not in seen_wl:
                    seen_wl.add(link)
                    merged_wl.append(link)
        base[link_key] = merged_wl

    # 合并中方实体：同法人别名可合并，集团内不同子公司必须保留。
    seen_ent: set = set()
    merged_entities: List[Dict] = []
    for r in records:
        for e in r["entities"]:
            key = _entity_identity_key(e)
            if e["implementing_entity"] and key not in seen_ent:
                seen_ent.add(key)
                merged_entities.append(e)
    base["entities"] = merged_entities

    # EN 翻译字段：取首个非空值，无需 LLM 仲裁
    for en_field in ["project_name_en", "project_developer_en"]:
        vals = [r.get(en_field, "") for r in records if r.get(en_field)]
        if vals:
            base[en_field] = vals[0]

    # 字段冲突处理（raw 原文短语列不仲裁，随 first；只仲裁数值/枚举语义字段）
    conflict_fields = [
        "capacity_mw", "project_developer",
        "planned_start_date", "planned_complete_date",
        "total_investment_usd", "contract_amount_usd", "financing_amount_usd",
        "financing_model",
    ]
    conflict_log: List[str] = []
    for field in conflict_fields:
        vals = list(dict.fromkeys(r[field] for r in records if r.get(field)))
        if len(vals) > 1:
            chosen = _llm_choose_value(client, config, field, vals, base["project_name"])
            base[field] = chosen
            conflict_log.append(f"{field}:[{' vs '.join(vals)}]→{chosen}")
        elif len(vals) == 1:
            base[field] = vals[0]

    if conflict_log:
        note = "【合并冲突】" + "; ".join(conflict_log)
        base["additional_remarks"] = (
            base.get("additional_remarks", "") + " " + note
        ).strip()

    return base


def _run_confirm_batches(client, config, projects, todo, cache_db, cache) -> None:
    """分批并行确认 todo=[(pi,pj,key)]；每批 commit 缓存并回填内存 cache（断点续跑核心）。"""
    for start in range(0, len(todo), DEDUP_BATCH_SIZE):
        batch = todo[start:start + DEDUP_BATCH_SIZE]

        def _confirm(item):
            pi, pj, k = item
            try:
                same = _llm_confirm_raising(client, config, projects[pi], projects[pj])
                return k, (1 if same else 0), "done", None, (pi, pj)
            except coordination.CoordinationRequired as e:
                # Business holds are reusable decisions, not retryable API failures.
                return k, None, "pending", e.evidence, (pi, pj)
            except (RuntimeError, ValueError, OSError, genai_errors.APIError) as e:
                logging.error("确认失败 pair_key=%s: %s", k, e)
                return k, None, "failed", None, (pi, pj)

        rows = []
        with ThreadPoolExecutor(max_workers=DEDUP_WORKERS) as exe:
            for k, same, status, evidence, indices in exe.map(_confirm, batch):
                if evidence:
                    if cache_db:
                        dedup_cache.save_evidence(cache_db, k, evidence)
                    for index in indices:
                        projects[index].setdefault("_coordination_conflicts", []).append(evidence)
                rows.append((k, same, status))
                cache[k] = (same, status)
        if cache_db:
            dedup_cache.upsert(cache_db, rows)
        print(f"    批次 {start // DEDUP_BATCH_SIZE + 1}：确认 {len(batch)} 对"
              f"（累计 {min(start + len(batch), len(todo))}/{len(todo)}）")


def _confirm_llm_pairs(client, config, projects, llm_pairs, cache_db) -> Dict[Tuple[int, int], bool]:
    """对 llm_pairs 做可缓存/可续跑/失败重跑的确认，返回 {(pi,pj): same_bool}。"""
    keyed = [(pi, pj, dedup_cache.pair_key(projects[pi], projects[pj])) for pi, pj in llm_pairs]
    cache: Dict[str, Tuple] = {}
    if cache_db:
        dedup_cache.init_cache(cache_db)
        cache = dedup_cache.load_cache(cache_db)

    for pi, pj, k in keyed:
        if cache.get(k, (None, "none"))[1] == "pending":
            evidence = dedup_cache.load_evidence(cache_db, k)
            for index in (pi, pj):
                projects[index].setdefault("_coordination_conflicts", []).append(evidence)
    todo = [(pi, pj, k) for pi, pj, k in keyed if cache.get(k, (None, "none"))[1] not in {"done", "pending"}]
    print(f"  LLM 核验 {len(llm_pairs)} 对（已缓存 {len(llm_pairs) - len(todo)} / 待确认 {len(todo)}"
          f" / workers={DEDUP_WORKERS} / batch={DEDUP_BATCH_SIZE}）…")
    _run_confirm_batches(client, config, projects, todo, cache_db, cache)

    failed = [(pi, pj, k) for pi, pj, k in keyed if cache.get(k, (None, "none"))[1] == "failed"]
    if failed:
        print(f"  失败重跑 {len(failed)} 对…")
        _run_confirm_batches(client, config, projects, failed, cache_db, cache)

    still = sum(1 for _, _, k in keyed if cache.get(k, (None, "none"))[1] == "failed")
    if still:
        raise RuntimeError(f"仍有 {still} 对去重确认失败；停止入库，保留未导入状态及已完成缓存，续跑重试")

    out: Dict[Tuple[int, int], bool] = {}
    for pi, pj, k in keyed:
        same, status = cache.get(k, (None, "none"))
        out[(pi, pj)] = (status == "done" and bool(same))
    return out


def _assemble_multi_group(indices, root, projects, client, config) -> List[Dict]:
    """组装一个多记录 union-find 组：按 stage 分摊合并，多阶段打共享 gid，返回该组所有记录。

    各组互相独立 → 可并行（_merge_group 的字段仲裁 LLM 是大头，串行会拖几小时）。
    """
    by_stage: Dict[str, List[int]] = defaultdict(list)
    for i in indices:
        by_stage[projects[i]["project_stage"]].append(i)
    gid = f"grp-{root}"
    out: List[Dict] = []
    for stage_indices in by_stage.values():
        recs = [projects[i] for i in stage_indices]
        merged_rec = recs[0] if len(recs) == 1 else _merge_group(client, config, recs)
        if len(by_stage) > 1:
            merged_rec = dict(merged_rec)
            merged_rec["dedup_group_id"] = gid
        out.append(merged_rec)
    return out


def _deduplicate_projects(
    client: genai.Client,
    config: types.GenerateContentConfig,
    projects: List[Dict],
    candidates: List[Tuple[int, int, float]],
    cache_db: Optional[str] = None,
) -> List[Dict]:
    """Union-Find 分组后逐对确认（高分自动确认，其余 LLM 核验），合并确认重复的记录。

    cache_db 非空时：LLM 确认走持久缓存（断点续跑 + 失败重跑）；为 None 时行为同现状。
    """
    n = len(projects)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[py] = px

    # 分流：高分自动确认 vs 需要 LLM 核验
    score_map = {(i, j): s for i, j, s in candidates}
    auto_pairs: List[Tuple[int, int]] = []
    llm_pairs: List[Tuple[int, int]] = []
    for i, j, best_score in candidates:
        cn_score = fuzz.token_sort_ratio(
            projects[i].get("project_name", ""),
            projects[j].get("project_name", ""),
        )
        package_candidate = _looks_like_project_package(projects[i]) or _looks_like_project_package(projects[j])
        if best_score >= CANONICAL_AUTO_MATCH_SCORE and cn_score >= CANONICAL_AUTO_MATCH_SCORE and not package_candidate:
            auto_pairs.append((i, j))
        else:
            llm_pairs.append((i, j))

    for i, j in auto_pairs:
        a, b = projects[i], projects[j]
        if _hard_identity_conflict(a, b):
            print(
                f"  [身份冲突，不合并] [{a['project_name'][:25]}] vs "
                f"[{b['project_name'][:25]}]"
            )
            continue
        print(
            f"  [高分自动确认 score={score_map[(i, j)]:.0f}] "
            f"[{a['project_name'][:25]}] vs [{b['project_name'][:25]}]"
        )
        union(i, j)

    if llm_pairs:
        verdicts = _confirm_llm_pairs(client, config, projects, llm_pairs, cache_db)
        for (pi, pj), same in verdicts.items():
            if same and not _hard_identity_conflict(projects[pi], projects[pj]):
                union(pi, pj)

    groups: Dict[int, List[int]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    # 单体直接收；多记录组并行组装（落库语义 = 一条 canonical + 每阶段一条 event）
    result: List[Dict] = []
    multi: List[Tuple[int, List[int]]] = []
    for root, indices in groups.items():
        if len(indices) == 1:
            result.append(projects[indices[0]])
        else:
            multi.append((root, indices))

    if multi:
        print(f"  并行组装 {len(multi)} 个合并组（workers={DEDUP_WORKERS}）…")
        with ThreadPoolExecutor(max_workers=DEDUP_WORKERS) as exe:
            for recs in exe.map(
                lambda ri: _assemble_multi_group(ri[1], ri[0], projects, client, config), multi
            ):
                result.extend(recs)

    return result


# ── 阶段四：SQLite 写入 ────────────────────────────────────────────────────────

def _capacity_guard(
    conn: sqlite3.Connection,
    canonical_id: str,
    project: Dict,
) -> bool:
    """容量守卫：≥97分自动匹配前检查容量差异。

    若新项目与已有 canonical 的代表性容量差异超过 CANONICAL_CAP_GUARD_TOLERANCE，
    返回 True（建议降级为 LLM 确认）。
    以下情况返回 False（不拦截）：
      - 新项目无容量值
      - canonical 无任何有值的 project_event 容量
    """
    cap_new = _parse_capacity(project.get("capacity_mw"))
    if not cap_new or cap_new <= 0:
        return False   # 无法判断，不拦截

    # capacity_mw 现为 REAL，直接取数值列；代表容量优先取终态事件
    row = conn.execute(
        f"""SELECT capacity_mw, capacity_details FROM project_events
           WHERE canonical_id = ? AND capacity_mw IS NOT NULL
           ORDER BY {_stage_order_sql('project_stage')} DESC
           LIMIT 1""",
        (canonical_id,),
    ).fetchone()
    if not row:
        return False   # canonical 无容量记录，不拦截

    cap_db = _parse_capacity(row[0])
    if not cap_db or cap_db <= 0:
        return False
    existing = {"capacity_mw": cap_db, "capacity_details": row[1] or ""}
    return not _capacity_pair_close(
        project, existing, CANONICAL_CAP_GUARD_TOLERANCE
    )


def _create_canonical(
    conn: sqlite3.Connection,
    project: Dict,
    canonical_cache: Optional[Dict[Tuple[str, str], List]] = None,
) -> str:
    """直接新建一个 canonical_project（不做任何名称匹配），返回 new_id，并更新内存缓存。"""
    iso = project["country_iso"] or "UNK"
    cat = project["category"]
    name = project["project_name"]
    name_en = project.get("project_name_en", "")
    new_id = _gen_canonical_id(conn, iso, _get_cat_code(cat))
    conn.execute(
        """INSERT INTO canonical_projects
           (id, project_name, project_name_en, name_en_quality,
            country, country_iso, bri_country, ldc, location, location_norm, category, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            new_id, name, name_en,
            project.get("name_en_quality", "ai_translated"),
            project["country"], iso,
            project["bri_country"], project["ldc"],
            project["location"], project.get("location_norm", ""), cat,
            datetime.now().isoformat(),
        ),
    )
    if canonical_cache is not None:
        canonical_cache.setdefault((iso, cat), []).append(
            (new_id, name, name_en, _norm_location(project))
        )
    return new_id


def _find_or_create_canonical(
    conn: sqlite3.Connection,
    project: Dict,
    client: Optional[genai.Client] = None,
    config: Optional[types.GenerateContentConfig] = None,
    canonical_cache: Optional[Dict[Tuple[str, str], List]] = None,
    _rejected_ids: Tuple[str, ...] = (),
) -> Tuple[str, bool]:
    """
    查找已有的 canonical_project，或新建一条。

    名称匹配：相同 country_iso（允许跨类别救援），比较四个名称对（取最高分）：
      1. incoming CN  vs DB CN
      2. incoming EN  vs DB CN
      3. incoming EN  vs DB EN
      4. incoming CN  vs DB EN

    三级决策：
      score ≥ CANONICAL_AUTO_MATCH_SCORE (97) → 自动匹配（容量守卫：差异 > 20% 且有 LLM 时降级确认）
      CANONICAL_LLM_MIN_SCORE (70) ≤ score < 97 且 client 非空 → LLM 确认
      client 为空时降级：score ≥ FUZZY_THRESHOLD (85) → 自动匹配
      地点辅助（P3b）：score < 70 但同类、地点相同/包含 + 容量守卫通过 → 仅送 LLM 确认
      同地点候选优先；LLM拒绝后至多再核验2个候选，不因第一个错误候选即漏掉真实项目
      其余 → 新建

    返回 (canonical_id, is_new)。
    """
    iso = project["country_iso"] or "UNK"
    cat = project["category"]
    name = project["project_name"]
    incoming_en = (project.get("project_name_en") or "").strip()

    if canonical_cache is not None:
        rows = [
            row
            for (row_iso, _row_category), cached_rows in canonical_cache.items()
            if row_iso == iso
            for row in cached_rows
        ]
    else:
        rows = conn.execute(
            "SELECT id, project_name, project_name_en, "
            "COALESCE(NULLIF(location_norm, ''), location, '') "
            "FROM canonical_projects WHERE country_iso = ?",
            (iso,),
        ).fetchall()

    best_id: Optional[str] = None
    best_score = 0
    best_cn_score = 0
    best_location = ""
    incoming_loc = _norm_location(project)
    best_rank = (-1, -1.0)
    loc_assist = False
    for row_id, row_name, row_name_en, _row_loc in rows:
        if row_id in _rejected_ids:
            continue
        cn_score = fuzz.token_sort_ratio(name, row_name or "")
        score = cn_score
        if incoming_en and row_name:
            score = max(score, fuzz.token_sort_ratio(incoming_en, row_name))
        if incoming_en and row_name_en:
            score = max(score, fuzz.token_sort_ratio(incoming_en, row_name_en))
        if name and row_name_en:
            score = max(score, fuzz.token_sort_ratio(name, row_name_en))
        # Place context ranks candidates before boilerplate name similarity. A
        # descriptive prefix (e.g. coastal + district) must not hide the same site.
        row_loc = (_row_loc or "").strip()
        same_place = bool(len(incoming_loc) >= 2 and len(row_loc) >= 2 and (
            incoming_loc.casefold() in row_loc.casefold()
            or row_loc.casefold() in incoming_loc.casefold()
        ))
        assisted = False
        if score < CANONICAL_LLM_MIN_SCORE and same_place and client is not None:
            row_category = conn.execute("SELECT category FROM canonical_projects WHERE id=?", (row_id,)).fetchone()
            assisted = bool(row_category and row_category[0] == cat
                            and not _capacity_guard(conn, row_id, project))
        rank = (int(same_place), score)
        if (score >= CANONICAL_LLM_MIN_SCORE or assisted) and rank > best_rank:
            best_id, best_score, best_cn_score, best_location = row_id, score, cn_score, (_row_loc or "")
            best_rank, loc_assist = rank, assisted

    if loc_assist:
        print(f"  [地点辅助] {best_id} 地点一致/包含「{incoming_loc}」，降门槛送 LLM (score={best_score})")

    if best_id:
        incoming_loc_for_guard = _norm_location(project)
        location_conflict = bool(
            incoming_loc_for_guard
            and best_location
            and incoming_loc_for_guard not in best_location
            and best_location not in incoming_loc_for_guard
            and fuzz.ratio(incoming_loc_for_guard, best_location) < 60
        )
        if (
            best_score >= CANONICAL_AUTO_MATCH_SCORE
            and best_cn_score >= CANONICAL_AUTO_MATCH_SCORE
            and not _looks_like_project_package(project)
            and not loc_assist
            and not location_conflict
        ):
            if _capacity_guard(conn, best_id, project):
                if client is None:
                    raise coordination.CoordinationRequired({"kind": "historical_identity", "candidate_canonical_id": best_id,
                        "reason": "Strong name match but unresolved capacity conflict; confirmation unavailable"})
                # 容量差异大，高分名称不足以自动匹配 → 降级 LLM 确认
                print(
                    f"  [容量守卫] {best_id} vs [{name[:25]}] "
                    f"(score={best_score}, 容量差异>{CANONICAL_CAP_GUARD_TOLERANCE:.0%}，降级 LLM 核验)"
                )
                # 不 return，fall through 到下方 LLM 确认分支
            else:
                # 高度相似且容量无异常，直接匹配
                return best_id, False

        if best_id and client is not None and (best_score >= CANONICAL_LLM_MIN_SCORE or loc_assist):
            # LLM 确认（含：70-97 原有路径 + 被容量守卫降级的 ≥97 路径）
            print(
                f"  [LLM canonical 核验] {best_id} vs "
                f"[{name[:25]}] (score={best_score})"
            )
            db_row = conn.execute(
                f"""SELECT cp.project_name, cp.project_name_en, cp.location,
                          (SELECT pe.capacity_mw FROM project_events pe
                           WHERE pe.canonical_id = cp.id
                             AND pe.capacity_mw IS NOT NULL
                           ORDER BY {_stage_order_sql('pe.project_stage')} DESC
                           LIMIT 1) AS capacity_mw,
                          (SELECT pe.capacity_details FROM project_events pe
                           WHERE pe.canonical_id = cp.id
                           ORDER BY {_stage_order_sql('pe.project_stage')} DESC
                           LIMIT 1) AS capacity_details,
                          (SELECT pe.project_developer FROM project_events pe
                           WHERE pe.canonical_id = cp.id
                             AND COALESCE(pe.project_developer, '') <> ''
                           ORDER BY {_stage_order_sql('pe.project_stage')} DESC
                           LIMIT 1) AS project_developer,
                          (SELECT GROUP_CONCAT(DISTINCT ce.implementing_entity)
                           FROM chinese_entities ce
                           WHERE ce.event_id IN (
                             SELECT pe2.id FROM project_events pe2
                             WHERE pe2.canonical_id = cp.id
                           )) AS entities
                   FROM canonical_projects cp
                   WHERE cp.id = ?""",
                (best_id,),
            ).fetchone()
            (db_name, db_name_en, db_location, db_capacity,
             db_capacity_details, db_developer, db_entities) = (
                db_row if db_row else ("", "", "", "", "", "", "")
            )
            confirmed = _llm_confirm_canonical(
                client, config, project,
                best_id,
                db_name or "", db_name_en or "",
                db_location or "", db_capacity or "",
                db_capacity_details or "", db_developer or "", db_entities or "",
            )
            if confirmed:
                print(f"    → LLM 确认匹配 {best_id}")
                return best_id, False
            else:
                print(f"    → LLM 判断为不同项目，检查剩余候选")
                if len(_rejected_ids) < 2:
                    return _find_or_create_canonical(
                        conn, project, client, config, canonical_cache,
                        _rejected_ids + (best_id,),
                    )
        elif best_id and best_score >= FUZZY_THRESHOLD and not location_conflict:
            # 无 LLM 降级模式：保持原有 ≥ 85 自动匹配行为
            return best_id, False

    # 名称匹配未命中 → 新建 canonical
    return _create_canonical(conn, project, canonical_cache), True


def _write_to_db(
    conn: sqlite3.Connection,
    projects: List[Dict],
    client: Optional[genai.Client] = None,
    config: Optional[types.GenerateContentConfig] = None,
    trust_dedup: bool = False,
    *,
    commit: bool = True,
    on_hold=None,
    resolved_canonical_id: Optional[str] = None,
    resolved_distinct: bool = False,
) -> Dict[str, int]:
    """将合并后的逻辑项目写入三张 SQLite 表，返回各表新增条数。

    trust_dedup=True（从空库全局重建）：dedup 结果即权威，非 gid 每条直接新建 canonical，
    跳过 `_find_or_create_canonical` 的 LLM 重匹配（避免冗余 + ≥97 误并覆盖 dedup 决定）；
    gid 组照常复用。默认 False（季度增量：新批须与库里旧 canonical 匹配，不可跳）。
    """
    cnt = {"canonical_new": 0, "event_new": 0, "entity_new": 0, "event_cross_merged": 0}

    # 预加载所有 canonical_projects 到内存，避免逐项 SQL 查询
    canonical_cache: Dict[Tuple[str, str], List] = {}
    for row in conn.execute(
        "SELECT id, project_name, project_name_en, country_iso, category, "
        "COALESCE(NULLIF(location_norm, ''), location, '') FROM canonical_projects"
    ).fetchall():
        key = (row[3], row[4])  # (country_iso, category)
        canonical_cache.setdefault(key, []).append((row[0], row[1], row[2], row[5]))

    # 同一 dedup_group_id（去重阶段确认的跨阶段同项目）强制共用 canonical，跳过名称匹配
    # gid 仅在单次运行内有效；跨批次低名称分场景由 _find_or_create_canonical 的地点辅助通道兜底
    group_canonical: Dict[str, str] = {}
    group_holds: Dict[str, list] = {}
    for project in projects:
        if project.get("dedup_group_id") and project.get("_coordination_conflicts"):
            group_holds[project["dedup_group_id"]] = project["_coordination_conflicts"]
    if (resolved_canonical_id or resolved_distinct) and len(projects) != 1:
        raise ValueError("A reviewed identity resolution must target one logical project")

    for project in projects:
        if _pending_publication(project) or _excluded_publication(project):
            logger.info("跳过未获准发布的项目记录: %s", project.get("project_name", ""))
            continue
        _apply_incoming_energy_taxonomy(project)
        # ── canonical_projects ──────────────────────────────────────────────
        gid = project.get("dedup_group_id")
        conflicts = project.get("_coordination_conflicts") or group_holds.get(gid)
        if conflicts and not (resolved_canonical_id or resolved_distinct):
            if on_hold is None:
                raise coordination.CoordinationRequired({"reason": "Unresolved batch identity", "conflicts": conflicts})
            on_hold(project, conflicts)
            continue
        if resolved_canonical_id:
            existing = conn.execute("SELECT country_iso FROM canonical_projects WHERE id=?", (resolved_canonical_id,)).fetchone()
            if not existing or existing[0] != project["country_iso"]:
                raise ValueError("Reviewed canonical missing or belongs to another country")
            canonical_id, is_new = resolved_canonical_id, False
        elif resolved_distinct:
            canonical_id, is_new = _create_canonical(conn, project, canonical_cache), True
        elif gid and gid in group_canonical:
            canonical_id, is_new = group_canonical[gid], False
        elif trust_dedup:
            # 从空库全局重建：dedup 已全局比过，直接建新 canonical（跳过 LLM 重匹配）
            canonical_id, is_new = _create_canonical(conn, project, canonical_cache), True
            if gid:
                group_canonical[gid] = canonical_id
        else:
            try:
                canonical_id, is_new = _find_or_create_canonical(conn, project, client, config, canonical_cache)
            except coordination.CoordinationRequired as e:
                if on_hold is None:
                    raise
                on_hold(project, [e.evidence])
                if gid:
                    group_holds[gid] = [e.evidence]
                continue
            if gid:
                group_canonical[gid] = canonical_id
        if is_new:
            cnt["canonical_new"] += 1

        # ── project_events ─────────────────────────────────────────────────
        # 同阶段事件是否已存在（支持重复运行的幂等性）
        existing_event = conn.execute(
            "SELECT id FROM project_events WHERE canonical_id = ? AND project_stage = ?",
            (canonical_id, project["project_stage"]),
        ).fetchone()

        reviewed_capacity = None
        if existing_event:
            event_id = existing_event[0]
            reviewed_capacity = capture_reviewed_capacity(conn, event_id, project)

            if client is not None:
                # ── 跨批次完整合并（有 LLM 时）──────────────────────────────
                print(f"  [跨批次合并] {event_id} 与新记录执行完整字段合并")
                existing_dict = _fetch_event_as_dict(conn, event_id)
                merged_rec = _merge_group(client, config, [existing_dict, project])
                conn.execute(
                    """UPDATE project_events SET
                           capacity_mw_raw = ?, capacity_mw = ?, capacity_source = ?,
                           capacity_details = ?,
                           project_developer = ?, project_developer_en = ?,
                           planned_start_date = ?, planned_complete_date = ?,
                           total_investment_usd_raw = ?, total_investment_usd = ?,
                           contract_amount_usd_raw = ?, contract_amount_usd = ?,
                           financing_amount_usd_raw = ?, financing_amount_usd = ?,
                           investment_date = ?,
                           financing_model = ?, financing_model_details = ?,
                           source_links = ?,
                           official_website_links = ?, non_official_website_links = ?,
                           publish_date = ?, additional_remarks = ?
                       WHERE id = ?""",
                    (
                        reviewed_capacity["capacity_mw_raw"] if reviewed_capacity else merged_rec["capacity_mw"],
                        reviewed_capacity["capacity_mw"] if reviewed_capacity else bri_parse.parse_number(merged_rec["capacity_mw"]),
                        "manual" if reviewed_capacity else "original",
                        reviewed_capacity["capacity_details"] if reviewed_capacity else merged_rec["capacity_details"],
                        merged_rec["project_developer"],
                        merged_rec.get("project_developer_en", ""),
                        merged_rec["planned_start_date"],
                        merged_rec["planned_complete_date"],
                        merged_rec["total_investment_usd"],
                        bri_parse.parse_number(merged_rec["total_investment_usd"]),
                        merged_rec.get("contract_amount_usd_raw") or None,
                        bri_parse.parse_number(merged_rec.get("contract_amount_usd")),
                        merged_rec.get("financing_amount_usd_raw") or None,
                        bri_parse.parse_number(merged_rec.get("financing_amount_usd")),
                        merged_rec["investment_date"],
                        merged_rec["financing_model"],
                        merged_rec.get("financing_details", ""),
                        json.dumps(merged_rec["source_links"], ensure_ascii=False),
                        json.dumps(merged_rec.get("official_website_links", []), ensure_ascii=False),
                        json.dumps(merged_rec.get("non_official_website_links", []), ensure_ascii=False),
                        merged_rec["publish_date"],
                        merged_rec["additional_remarks"],
                        event_id,
                    ),
                )
                cnt["event_cross_merged"] += 1
            else:
                # ── 降级：无 LLM 时仅追加 source_links（原有行为）────────────
                old_raw = conn.execute(
                    "SELECT source_links FROM project_events WHERE id = ?", (event_id,)
                ).fetchone()[0]
                old_links: List[str] = json.loads(old_raw) if old_raw else []
                new_links = list(dict.fromkeys(old_links + project["source_links"]))
                conn.execute(
                    "UPDATE project_events SET source_links = ? WHERE id = ?",
                    (json.dumps(new_links, ensure_ascii=False), event_id),
                )
        else:
            event_id = _gen_event_id(conn, canonical_id)
            conn.execute(
                """INSERT INTO project_events
                   (id, canonical_id, project_stage,
                    capacity_mw_raw, capacity_mw, capacity_details,
                    storage_power_mw, storage_energy_mwh, capacity_source,
                    project_developer, project_developer_en, planned_start_date, planned_complete_date,
                    total_investment_usd_raw, total_investment_usd,
                    contract_amount_usd_raw, contract_amount_usd,
                    financing_amount_usd_raw, financing_amount_usd, investment_date,
                    financing_model, financing_model_details, source_links,
                    official_website_links, non_official_website_links,
                    publish_date, additional_remarks, project_summary, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id, canonical_id,
                    project["project_stage"],
                    project["capacity_mw"], bri_parse.parse_number(project["capacity_mw"]),
                    project["capacity_details"],
                    project.get("storage_power_mw"), project.get("storage_energy_mwh"), "original",
                    project["project_developer"], project.get("project_developer_en", ""),
                    project["planned_start_date"], project["planned_complete_date"],
                    project["total_investment_usd"], bri_parse.parse_number(project["total_investment_usd"]),
                    project.get("contract_amount_usd_raw") or None,
                    bri_parse.parse_number(project.get("contract_amount_usd")),
                    project.get("financing_amount_usd_raw") or None,
                    bri_parse.parse_number(project.get("financing_amount_usd")),
                    project["investment_date"],
                    project["financing_model"],    project["financing_details"],
                    json.dumps(project["source_links"], ensure_ascii=False),
                    json.dumps(project.get("official_website_links", []), ensure_ascii=False),
                    json.dumps(project.get("non_official_website_links", []), ensure_ascii=False),
                    project["publish_date"],   project["additional_remarks"],
                    project.get("project_summary", ""),
                    datetime.now().isoformat(),
                ),
            )
            cnt["event_new"] += 1

        # 新旧事件都可补齐能源事实，但已存在的非空/confirmed 事实不会被降级覆盖。
        conn.execute(
            """UPDATE project_events SET
                 generation_sources_json=CASE
                   WHEN generation_sources_json IS NULL OR generation_sources_json IN ('', '[]') THEN ?
                   ELSE generation_sources_json END,
                 storage_status=CASE
                   WHEN storage_status IS NULL OR storage_status IN ('', 'not_mentioned') THEN ?
                   ELSE storage_status END,
                 storage_power_mw=COALESCE(storage_power_mw, ?),
                 storage_energy_mwh=COALESCE(storage_energy_mwh, ?),
                 storage_power_mw_raw=COALESCE(NULLIF(storage_power_mw_raw,''), ?),
                 storage_energy_mwh_raw=COALESCE(NULLIF(storage_energy_mwh_raw,''), ?),
                 energy_composition_evidence=COALESCE(NULLIF(energy_composition_evidence,''), ?)
               WHERE id=?""",
            (
                project.get("generation_sources_json") or "[]",
                project.get("storage_status") or "not_mentioned",
                project.get("storage_power_mw"),
                project.get("storage_energy_mwh"),
                project.get("storage_power_mw_raw") or None,
                project.get("storage_energy_mwh_raw") or None,
                project.get("energy_composition_evidence") or None,
                event_id,
            ),
        )

        # 新 S01B 原子 TX/MF 字段优先，且只填充目标事件的空列；历史 backfill 仍可兜底。
        conn.execute(
            """UPDATE project_events SET
                 voltage_kv_raw=COALESCE(NULLIF(voltage_kv_raw,''), ?),
                 voltage_kv=COALESCE(voltage_kv, ?),
                 line_length_km_raw=COALESCE(NULLIF(line_length_km_raw,''), ?),
                 line_length_km=COALESCE(line_length_km, ?),
                 transmission_capacity_mw_raw=COALESCE(NULLIF(transmission_capacity_mw_raw,''), ?),
                 transmission_capacity_mw=COALESCE(transmission_capacity_mw, ?),
                 production_capacity_raw=COALESCE(NULLIF(production_capacity_raw,''), ?),
                 production_capacity_value=COALESCE(production_capacity_value, ?),
                 production_capacity_unit=COALESCE(NULLIF(production_capacity_unit,''), ?),
                 capacity_mw=CASE
                   WHEN ? IN ('输变电建设','新能源制造业') THEN NULL ELSE capacity_mw END,
                 capacity_mw_raw=CASE
                   WHEN ? IN ('输变电建设','新能源制造业') THEN NULL ELSE capacity_mw_raw END
               WHERE id=?""",
            (
                project.get("voltage_kv_raw") or None,
                project.get("voltage_kv"),
                project.get("line_length_km_raw") or None,
                project.get("line_length_km"),
                project.get("transmission_capacity_mw_raw") or None,
                project.get("transmission_capacity_mw"),
                project.get("production_capacity_raw") or None,
                project.get("production_capacity_value"),
                project.get("production_capacity_unit") or None,
                project.get("category"),
                project.get("category"),
                event_id,
            ),
        )

        restore_reviewed_capacity(conn, event_id, reviewed_capacity)
        _refresh_canonical_energy_taxonomy(conn, canonical_id)

        # ── chinese_entities ───────────────────────────────────────────────
        for ent in project.get("entities", []):
            if not ent.get("implementing_entity"):
                continue
            entity_id = _clean(ent.get("entity_id"))
            dupe = _find_existing_entity(conn, event_id, ent)
            if not dupe:
                conn.execute(
                    """INSERT INTO chinese_entities
                       (event_id, implementing_entity, implementing_entity_en,
                        subsidiary_name, subsidiary_name_en, entity_en_quality,
                        involvement_type, subcontract_amount_raw, subcontract_amount, is_central_soe,
                        entity_id, entity_name, group_entity_id, raw_entity_name,
                        identity_status, entity_evidence_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event_id,
                        ent["implementing_entity"],
                        ent.get("implementing_entity_en", ""),
                        ent["subsidiary_name"],
                        ent.get("subsidiary_name_en", ""),
                        ent.get("entity_en_quality", "ai_translated"),
                        ent["involvement_type"],
                        ent["subcontract_amount"],
                        bri_parse.parse_number(ent["subcontract_amount"]),
                        ent.get("is_central_soe"),
                        *(_clean(ent.get(field)) or None for field in ENTITY_IDENTITY_FIELDS),
                    ),
                )
                cnt["entity_new"] += 1
            else:
                if entity_id and not _clean(dupe[1]):
                    # First approved registry link replaces provisional identity facts in place.
                    # A translated old raw/group name cannot label a different new canonical name.
                    logger.info("Link reviewed entity in place: event=%s relation=%s entity_id=%s old=%s/%s new=%s/%s",
                                event_id, dupe[0], entity_id, dupe[2], dupe[3],
                                ent["implementing_entity"], ent.get("subsidiary_name", ""))
                    conn.execute(
                        "UPDATE chinese_entities SET implementing_entity=?, subsidiary_name=?, "
                        "implementing_entity_en=CASE WHEN COALESCE(implementing_entity,'')<>? THEN ? ELSE implementing_entity_en END, "
                        "subsidiary_name_en=CASE WHEN COALESCE(subsidiary_name,'')<>? THEN ? ELSE subsidiary_name_en END, "
                        "entity_en_quality=CASE WHEN COALESCE(implementing_entity,'')<>? OR COALESCE(subsidiary_name,'')<>? "
                        "THEN ? ELSE entity_en_quality END, "
                        "entity_id=?, entity_name=?, group_entity_id=?, identity_status=?, entity_evidence_json=? "
                        "WHERE id=?",
                        (ent["implementing_entity"], _clean(ent.get("subsidiary_name")),
                         ent["implementing_entity"], _clean(ent.get("implementing_entity_en")),
                         _clean(ent.get("subsidiary_name")), _clean(ent.get("subsidiary_name_en")),
                         ent["implementing_entity"], _clean(ent.get("subsidiary_name")),
                         ent.get("entity_en_quality", "ai_translated"), entity_id,
                         _clean(ent.get("entity_name")) or None, _clean(ent.get("group_entity_id")) or None,
                         _clean(ent.get("identity_status")) or None, _clean(ent.get("entity_evidence_json")) or None,
                         dupe[0]),
                    )
                assignments = ", ".join(f"{field}=COALESCE(NULLIF({field}, ''), ?)" for field in ENTITY_IDENTITY_FIELDS)
                conn.execute(
                    f"UPDATE chinese_entities SET {assignments} WHERE id=?",
                    (*(_clean(ent.get(field)) or None for field in ENTITY_IDENTITY_FIELDS), dupe[0]),
                )

    if commit:
        conn.commit()
    return cnt


# ── 跨批次合并辅助 ────────────────────────────────────────────────────────────

def _fetch_event_as_dict(conn: sqlite3.Connection, event_id: str) -> Dict:
    """将数据库中已有的 project_event 行重构为 _merge_group 兼容的 Dict。
    JOIN canonical_projects（项目名/国家/类别）+ JOIN chinese_entities（实体列表）。
    """
    row = conn.execute("""
        SELECT
            pe.project_stage, pe.capacity_mw_raw, pe.capacity_details,
            pe.project_developer, pe.project_developer_en, pe.planned_start_date, pe.planned_complete_date,
            pe.total_investment_usd_raw,
            pe.contract_amount_usd_raw, pe.contract_amount_usd,
            pe.financing_amount_usd_raw, pe.financing_amount_usd,
            pe.investment_date,
            pe.financing_model, pe.financing_model_details,
            pe.source_links, pe.official_website_links, pe.non_official_website_links,
            pe.publish_date, pe.additional_remarks, pe.project_summary,
            cp.project_name, cp.project_name_en, cp.name_en_quality,
            cp.country, cp.country_iso, cp.bri_country, cp.ldc, cp.location, cp.category
        FROM project_events pe
        JOIN canonical_projects cp ON cp.id = pe.canonical_id
        WHERE pe.id = ?
    """, (event_id,)).fetchone()

    if row is None:
        return {}

    ent_rows = conn.execute("""
        SELECT implementing_entity, implementing_entity_en,
               subsidiary_name, subsidiary_name_en, entity_en_quality,
               involvement_type, subcontract_amount_raw, is_central_soe,
               entity_id, entity_name, group_entity_id, raw_entity_name, identity_status, entity_evidence_json
        FROM chinese_entities WHERE event_id = ?
    """, (event_id,)).fetchall()

    entities = [
        {
            "implementing_entity":    r[0] or "",
            "implementing_entity_en": r[1] or "",
            "subsidiary_name":        r[2] or "",
            "subsidiary_name_en":     r[3] or "",
            "entity_en_quality":      r[4] or "ai_translated",
            "involvement_type":       r[5] or "",
            "subcontract_amount":     r[6] or "",
            "is_central_soe":         r[7],
            **dict(zip(ENTITY_IDENTITY_FIELDS, (value or "" for value in r[8:]))),
        }
        for r in ent_rows
    ]

    (project_stage, capacity_mw, capacity_details,
     project_developer, project_developer_en, planned_start_date, planned_complete_date,
     total_investment_usd,
     contract_amount_usd_raw, contract_amount_usd,
     financing_amount_usd_raw, financing_amount_usd,
     investment_date,
     financing_model, financing_model_details,
     source_links_raw, official_website_links_raw, non_official_website_links_raw,
     publish_date, additional_remarks, project_summary,
     project_name, project_name_en, name_en_quality,
     country, country_iso, bri_country, ldc, location, category) = row

    return {
        "record_type":                "具体项目",
        "project_name":               project_name or "",
        "project_name_en":            project_name_en or "",
        "name_en_quality":            name_en_quality or "ai_translated",
        "country":                    country or "",
        "country_iso":                country_iso or "UNK",
        "bri_country":                bri_country or "",
        "ldc":                        ldc or "",
        "location":                   location or "",
        "category":                   category or "",
        "capacity_mw":                capacity_mw or "",
        "capacity_details":           capacity_details or "",
        "project_stage":              project_stage or "",
        "project_developer":          project_developer or "",
        "project_developer_en":       project_developer_en or "",
        "planned_start_date":         planned_start_date or "",
        "planned_complete_date":      planned_complete_date or "",
        "total_investment_usd":       total_investment_usd or "",
        "contract_amount_usd":        contract_amount_usd if contract_amount_usd is not None else "",
        "contract_amount_usd_raw":    contract_amount_usd_raw or "",
        "financing_amount_usd":       financing_amount_usd if financing_amount_usd is not None else "",
        "financing_amount_usd_raw":   financing_amount_usd_raw or "",
        "investment_date":            investment_date or "",
        "financing_model":            financing_model or "",
        "financing_details":          financing_model_details or "",   # _merge_group 使用此 key
        "source_links":               json.loads(source_links_raw) if source_links_raw else [],
        "official_website_links":     json.loads(official_website_links_raw) if official_website_links_raw else [],
        "non_official_website_links": json.loads(non_official_website_links_raw) if non_official_website_links_raw else [],
        "publish_date":               publish_date or "",
        "additional_remarks":         additional_remarks or "",
        "project_summary":            project_summary or "",
        "entities":                   entities,
    }


# ── 非项目记录写入 ────────────────────────────────────────────────────────────

def _write_non_project_records(
    conn: sqlite3.Connection,
    records: List[Dict],
    *, commit: bool = True,
) -> int:
    """将设备订单、一般合作类记录写入 non_project_records 表（扁平化，一实体一行）。"""
    now = datetime.now().isoformat()
    count = 0
    for rec in records:
        if _pending_publication(rec) or _excluded_publication(rec):
            continue
        entities = rec.get("entities", [])
        src = rec["source_links"][0] if rec["source_links"] else ""
        if not entities:
            conn.execute(
                """INSERT INTO non_project_records
                   (record_type, project_name, country, country_iso, category,
                    capacity_mw_raw, capacity_mw, capacity_details, project_stage,
                    implementing_entity, implementing_entity_en,
                    subsidiary_name, subsidiary_name_en, involvement_type,
                    subcontract_amount_raw, subcontract_amount,
                    financing_model, financing_model_details,
                    source_link, publish_date, additional_remarks, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rec["record_type"], rec["project_name"],
                    rec["country"], rec["country_iso"], rec["category"],
                    rec["capacity_mw"], bri_parse.parse_number(rec["capacity_mw"]),
                    rec["capacity_details"], rec["project_stage"],
                    "", "", "", "", "",
                    "", None,
                    rec["financing_model"], rec["financing_details"],
                    src, rec["publish_date"], rec["additional_remarks"], now,
                ),
            )
            count += 1
        else:
            for ent in entities:
                conn.execute(
                    """INSERT INTO non_project_records
                       (record_type, project_name, country, country_iso, category,
                        capacity_mw_raw, capacity_mw, capacity_details, project_stage,
                        implementing_entity, implementing_entity_en,
                        subsidiary_name, subsidiary_name_en, involvement_type,
                        subcontract_amount_raw, subcontract_amount,
                        financing_model, financing_model_details,
                        source_link, publish_date, additional_remarks, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        rec["record_type"], rec["project_name"],
                        rec["country"], rec["country_iso"], rec["category"],
                        rec["capacity_mw"], bri_parse.parse_number(rec["capacity_mw"]),
                        rec["capacity_details"], rec["project_stage"],
                        ent.get("implementing_entity", ""),
                        ent.get("implementing_entity_en", ""),
                        ent.get("subsidiary_name", ""),
                        ent.get("subsidiary_name_en", ""),
                        ent.get("involvement_type", ""),
                        ent.get("subcontract_amount", ""),
                        bri_parse.parse_number(ent.get("subcontract_amount", "")),
                        rec["financing_model"], rec["financing_details"],
                        src, rec["publish_date"], rec["additional_remarks"], now,
                    ),
                )
                count += 1
    if commit:
        conn.commit()
    return count


# ── Input Schema & Tool ────────────────────────────────────────────────────────

class DuplicateMergerInput(BaseModel):
    input_db: str = Field(
        default="../s01b_project_extractor/data/projects.db",
        description="s01b 提取结果数据库路径（extracted_projects 表，imported_to_s02a=0 的记录为待处理）",
    )
    db_path: str = Field(
        default="data/db/bri_re_database.db",
        description="输出 SQLite 数据库文件路径",
    )
    source_urls: list[str] | None = Field(default=None, description="仅处理这些源URL；空列表不处理任何记录")


class DuplicateMergerTool:
    """从 s01 的 projects.db 读取未导入记录，执行四阶段去重合并流程，写入 SQLite 三表结构。"""

    def _run(
        self,
        input_db: str = "../s01b_project_extractor/data/projects.db",
        db_path: str = "data/db/bri_re_database.db",
        cache_db: Optional[str] = None,
        trust_dedup: bool = False,
        source_urls: list[str] | None = None,
    ) -> str:
        source_urls = coordination.validate_source_urls(source_urls)
        self.last_result = {"status": "running", "input_db": os.path.abspath(input_db),
                            "db_path": os.path.abspath(db_path)}
        if cache_db is None:
            # Keep confirmations across recoverable batch failures. Code/model changes
            # use another cache file rather than silently reusing a stale decision.
            version = hashlib.sha256(Path(__file__).read_bytes() + LLM_CONFIRM_MODEL.encode()).hexdigest()[:12]
            target = Path(db_path).resolve()
            cache_db = str(target.with_name(f"{target.stem}.dedup-{version}.db"))
        self.last_result["cache_db"] = cache_db

        # ── 阶段一：加载 & 归组 ──────────────────────────────────────────────
        print(f"\n>>> [阶段一] 加载数据：{input_db}")
        df, record_ids = _load_from_db(input_db, source_urls=source_urls)
        pending_tasks = coordination.pending_count(input_db, source_urls=source_urls) if Path(input_db).exists() else 0
        pending_overall = coordination.pending_count(input_db) if Path(input_db).exists() else 0
        if df.empty:
            _mark_imported(input_db, record_ids)
            self.last_result.update(status="partial" if pending_tasks else "completed", input_rows=0, imported_rows=len(record_ids),
                                    coordination_pending=pending_tasks, coordination_pending_overall=pending_overall, coordination_created=0,
                                    counts={"canonical_new": 0, "event_new": 0, "entity_new": 0,
                                            "event_cross_merged": 0, "non_project_new": 0})
            return f"未发现可发布记录（{len(record_ids)} 条明确排除记录已标记处理；待复核记录仍保留未导入）。"
        raw_count = len(df)
        print(f"  原始行数: {raw_count}")

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("未找到 GEMINI_API_KEY 环境变量，请检查 .env 文件。")
        client = genai.Client(api_key=api_key)
        gen_config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1)

        all_projects = _group_into_logical_projects(df)
        print(f"  归组后逻辑记录数: {len(all_projects)}")

        # ── 从项目名推断空 location（Stage 1 后置补全）─────────────────────────
        if client is not None:
            _infer_location_from_name(
                client, gen_config,
                [p for p in all_projects if p.get("record_type", "具体项目") == "具体项目"],
            )

        # 按 record_type 分流
        projects = [p for p in all_projects if p.get("record_type", "具体项目") == "具体项目"]
        non_projects = [p for p in all_projects if p.get("record_type", "具体项目") != "具体项目"]
        print(f"  具体项目: {len(projects)} 条，非项目记录（设备订单/一般合作）: {len(non_projects)} 条")

        # ── 批内地点归一化（in-memory）：供 Signal 6 锚点与 canonical 地点辅助使用 ──
        n_norm = _apply_location_norm(projects, client)
        if n_norm:
            print(f"  [地点归一化] in-memory 应用 {n_norm} 条 location_norm")

        # ── 阶段二：候选筛查（仅针对具体项目）──────────────────────────────
        print(f"\n>>> [阶段二] 候选重复筛查...")
        candidates = _find_duplicate_candidates(projects)
        print(f"  发现候选重复对: {len(candidates)}")

        # ── 阶段三：LLM 确认 + 合并 ─────────────────────────────────────────
        print(f"\n>>> [阶段三] LLM 确认 + 合并...")
        if candidates:
            merged = _deduplicate_projects(client, gen_config, projects, candidates, cache_db=cache_db)
        else:
            merged = projects
            print(f"  无需合并，直接进入入库阶段")
        print(f"  合并后唯一项目事件数: {len(merged)}")

        # ── 阶段四：SQLite 写入 ──────────────────────────────────────────────
        print(f"\n>>> [阶段四] 写入 SQLite：{db_path}")
        conn = _init_db(db_path)
        conn.close()
        held_rows = set()
        task_ids = []
        # Source flags, identity holds and business rows share one atomic commit.
        # A technical exception rolls all of them back; confirmed pair cache survives.
        with coordination.attached_transaction(input_db, db_path) as conn:
            def on_hold(project, evidence):
                task_ids.append(coordination.save_hold(conn, project, evidence))
                held_rows.update(project["_source_record_ids"])
            counts = _write_to_db(conn, merged, client, gen_config, trust_dedup=trust_dedup,
                                  commit=False, on_hold=on_hold)
            non_project_count = _write_non_project_records(conn, non_projects, commit=False)
            imported_ids = sorted(set(record_ids) - held_rows)
            conn.executemany("UPDATE extracted.extracted_projects SET imported_to_s02a=1 WHERE id=?", [(i,) for i in imported_ids])
        pending_tasks = coordination.pending_count(input_db, source_urls=source_urls)
        pending_overall = coordination.pending_count(input_db)
        self.last_result.update(status="partial" if pending_tasks else "completed", input_rows=raw_count, imported_rows=len(imported_ids),
                                coordination_pending=pending_tasks, coordination_pending_overall=pending_overall, coordination_created=len(set(task_ids)),
                                coordination_task_ids=sorted(set(task_ids)),
                                counts={**counts, "non_project_new": non_project_count})

        db_abs = os.path.abspath(db_path)
        return (
            f"处理完成。\n"
            f"  原始行数:          {raw_count}\n"
            f"  逻辑记录数:        {len(all_projects)}（具体项目 {len(projects)} + 非项目 {len(non_projects)}）\n"
            f"  去重候选对:        {len(candidates)}\n"
            f"  入库项目事件数:    {len(merged)}\n"
            f"  新建 canonical_projects: {counts['canonical_new']} 条\n"
            f"  新建 project_events:     {counts['event_new']} 条\n"
            f"  跨批次合并 project_events: {counts['event_cross_merged']} 条\n"
            f"  新建 chinese_entities:   {counts['entity_new']} 条\n"
            f"  写入 non_project_records: {non_project_count} 条\n"
            f"  数据库路径: {db_abs}"
        )
