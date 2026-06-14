"""duplicate_merger_tool.py
一带一路可再生能源数据库 — 去重合并与 SQLite 入库工具

四阶段处理：
1. 候选筛查：Python 端 fuzzy matching 找潜在跨源重复对
2. LLM 确认：Gemini 确认是否为同一物理项目（阶段可不同）
3. 字段仲裁：LLM 解决冲突字段，合并实体列表
4. SQLite 写入：规范化三表结构（canonical_projects / project_events / chinese_entities）
"""

import glob
import json
import logging
import os
import sqlite3
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from rapidfuzz import fuzz

# ── 枚举/解析单一可信源：健壮上溯找仓库根（含 bri_enums.py），避免脆弱的 parents[N] ──
_root = next(p for p in Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
import bri_enums as enums
import bri_location
import bri_parse

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
    excluded         INTEGER DEFAULT 0,   -- 1=非可再生能源等，排除出统计
    excluded_reason  TEXT,
    created_at       TEXT
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
    capacity_source         TEXT,
    project_developer       TEXT,
    planned_start_date      TEXT,
    planned_complete_date   TEXT,
    total_investment_usd_raw TEXT,
    total_investment_usd    REAL,
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


def _parse_capacity(val: Any) -> Optional[float]:
    """提取装机容量数值，失败返回 None（复用单一可信源 bri_parse）。"""
    return bri_parse.parse_number(val)


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
    for col_def in [
        "ALTER TABLE project_events ADD COLUMN project_summary TEXT",
        "ALTER TABLE project_events ADD COLUMN official_website_links TEXT",
        "ALTER TABLE project_events ADD COLUMN non_official_website_links TEXT",
        "ALTER TABLE chinese_entities ADD COLUMN is_central_soe INTEGER",
        # S2a 数值化新列（idempotent）
        "ALTER TABLE project_events ADD COLUMN capacity_mw_raw TEXT",
        "ALTER TABLE project_events ADD COLUMN storage_power_mw REAL",
        "ALTER TABLE project_events ADD COLUMN storage_energy_mwh REAL",
        "ALTER TABLE project_events ADD COLUMN capacity_source TEXT",
        "ALTER TABLE project_events ADD COLUMN total_investment_usd_raw TEXT",
        "ALTER TABLE chinese_entities ADD COLUMN subcontract_amount_raw TEXT",
        "ALTER TABLE non_project_records ADD COLUMN capacity_mw_raw TEXT",
        "ALTER TABLE non_project_records ADD COLUMN subcontract_amount_raw TEXT",
        "ALTER TABLE canonical_projects ADD COLUMN excluded INTEGER DEFAULT 0",
        "ALTER TABLE canonical_projects ADD COLUMN excluded_reason TEXT",
        "ALTER TABLE canonical_projects ADD COLUMN location_norm TEXT",
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

def _load_from_db(input_db: str) -> tuple[pd.DataFrame, list[int]]:
    """从 s01 projects.db 读取未导入记录（imported_to_s02a=0）。

    Returns:
        (DataFrame with Excel-style column names, list of record IDs to mark later)
    """
    if not os.path.exists(input_db):
        print(f"  [警告] 未找到 projects.db: {input_db}")
        return pd.DataFrame(), []

    conn = sqlite3.connect(input_db)
    df = pd.read_sql(
        "SELECT * FROM extracted_projects WHERE imported_to_s02a = 0", conn
    )
    conn.close()

    if df.empty:
        return df, []

    record_ids: list[int] = df["id"].tolist()

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
        "project_stage":           "Project Stage",
        "project_developer":       "Project Developer",
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
    except Exception as e:
        raise RuntimeError(f"标记 imported_to_s02a 失败: {e}") from e
    finally:
        conn.close()


def _group_into_logical_projects(df: pd.DataFrame) -> List[Dict]:
    """
    将扁平化 Excel 行归组为逻辑项目。
    同一信源 + 同一项目基本信息 → 一个逻辑项目（含多家实体）。
    """
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

        # 收集实体（去重：implementing_entity + involvement_type 为联合键）
        seen_entities: set = set()
        entities: List[Dict] = []
        for _, row in rows.iterrows():
            ent_key = (
                _clean(row.get("Implementing Chinese Entity", "")),
                _clean(row.get("Type of involvement of Chinese Entities", "")),
            )
            if ent_key[0] and ent_key not in seen_entities:
                seen_entities.add(ent_key)
                entities.append({
                    "implementing_entity":    ent_key[0],
                    "implementing_entity_en": _clean(row.get("Implementing Chinese Entity (EN)", "")),
                    "subsidiary_name":        _clean(row.get("Subsidiary Name", "")),
                    "subsidiary_name_en":     _clean(row.get("Subsidiary Name (EN)", "")),
                    "involvement_type":       ent_key[1],
                    "subcontract_amount":     _clean(row.get("Subcontract Amount (Million USD)", "")),
                    "is_central_soe":         row.get("is_central_soe"),
                })

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

        # ── exclude 传播（OR 语义：组内任一成员被排除 → 逻辑项目被排除）──
        def _truthy_excl(v) -> bool:
            return str(v).strip() in ("1", "1.0", "True", "true")
        grp_excluded = 1 if any(_truthy_excl(r.get("excluded", "")) for _, r in rows.iterrows()) else 0
        grp_excl_reason = ""
        if grp_excluded:
            for _, r in rows.iterrows():
                if _truthy_excl(r.get("excluded", "")):
                    grp_excl_reason = _clean(r.get("exclude_reason", ""))
                    if grp_excl_reason:
                        break

        logical_projects.append({
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
            "project_stage":        _clean(first.get("Project Stage", "")),
            "project_developer":    _clean(first.get("Project Developer", "")),
            "planned_start_date":   _clean(first.get("(Planned) Start Date", "")),
            "planned_complete_date":_clean(first.get("(Planned) Complete Date", "")),
            "total_investment_usd": _clean(first.get("Total Project Investment (Million USD)", "")),
            "investment_date":      _clean(first.get("Investment (or Financial Close) Date", "")),
            "financing_model":      _clean(first.get("Financing Model", "")),
            "financing_details":    _clean(first.get("Financing Model Details", "")),
            "source_links":         [src] if src else [],
            "official_website_links":     _collect_links("Official Website link"),
            "non_official_website_links": _collect_links("Non-official website link"),
            "publish_date":         _clean(first.get("Publish Date", "")),
            "additional_remarks":   _clean(first.get("Additional Remarks", "")),
            "project_summary":      _clean(first.get("project_summary", "")),
            "excluded":             grp_excluded,
            "exclude_reason":       grp_excl_reason,
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
      - 相同国家 + 能源类型（由预分组保证，无需循环内检查）
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

    # 预分组：仅在相同国家 + 类别的组内两两比较（O(k²) 代替 O(n²)）
    # P3 (a)：去掉 project_stage 维度——同一物理项目的签约/开工/竣工记录必须能进同一候选组
    groups: Dict[Tuple, List[int]] = defaultdict(list)
    for idx, p in enumerate(projects):
        groups[(p["country"], p["category"])].append(idx)

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
                    cap_a = _parse_capacity(a.get("capacity_mw"))
                    cap_b = _parse_capacity(b.get("capacity_mw"))
                    if (
                        cap_a and cap_b
                        and cap_a >= CAPACITY_MIN_MW
                        and cap_b >= CAPACITY_MIN_MW
                        and abs(cap_a - cap_b) / max(cap_a, cap_b) <= CAPACITY_EXACT_TOLERANCE
                    ):
                        cross_lang_boost = True

                # ── Signal 5：宽松 LLM 候选（所有前置信号均未命中的补救）──
                # 触发条件：名称中度相似（≥ 56）+ 实体重叠 OR 容量在 15% 以内
                broad_llm_boost = False
                if not name_ok and not entity_boost and not cross_lang_boost:
                    if best_score >= BROAD_LLM_SCORE:
                        cap_a = _parse_capacity(a.get("capacity_mw"))
                        cap_b = _parse_capacity(b.get("capacity_mw"))
                        capacity_close = (
                            cap_a and cap_b
                            and cap_a >= CAPACITY_MIN_MW
                            and cap_b >= CAPACITY_MIN_MW
                            and abs(cap_a - cap_b) / max(cap_a, cap_b) <= CAPACITY_BROAD_TOLERANCE
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
                        cap_a = _parse_capacity(a.get("capacity_mw"))
                        cap_b = _parse_capacity(b.get("capacity_mw"))
                        if (
                            cap_a and cap_b
                            and cap_a >= LOCATION_ANCHOR_MIN_MW
                            and cap_b >= LOCATION_ANCHOR_MIN_MW
                            and abs(cap_a - cap_b) / max(cap_a, cap_b) <= LOCATION_ANCHOR_CAP_TOLERANCE
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
                    cap_a = _parse_capacity(a["capacity_mw"])
                    cap_b = _parse_capacity(b["capacity_mw"])
                    if cap_a and cap_b and cap_a > 0 and cap_b > 0:
                        if abs(cap_a - cap_b) / max(cap_a, cap_b) > CAPACITY_TOLERANCE:
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


def _llm_confirm(
    client: genai.Client,
    config: types.GenerateContentConfig,
    a: Dict,
    b: Dict,
) -> bool:
    """调用 Gemini 确认两条记录是否为同一物理项目（阶段可不同）。"""
    src_a = a["source_links"][0] if a["source_links"] else "未知"
    src_b = b["source_links"][0] if b["source_links"] else "未知"
    prompt = f"""
请判断以下两条记录是否描述同一个能源项目（同一物理工程；项目阶段可以不同——签约/开工/竣工/投运属于同一项目生命周期的不同阶段事件）。

记录A：
- 项目名称（中）：{a['project_name']}
- 项目名称（英）：{a.get('project_name_en', '—')}
- 国家：{a['country']}，省/地区/城市：{a['location'] or '未知'}
- 能源类型：{a['category']}，装机容量：{a['capacity_mw'] or '未知'} MW
- 项目阶段：{a['project_stage']}
- 项目开发商：{a.get('project_developer') or '未知'}
- 中方实体：{_fmt_entities(a)}
- 信源：{src_a}

记录B：
- 项目名称（中）：{b['project_name']}
- 项目名称（英）：{b.get('project_name_en', '—')}
- 国家：{b['country']}，省/地区/城市：{b['location'] or '未知'}
- 能源类型：{b['category']}，装机容量：{b['capacity_mw'] or '未知'} MW
- 项目阶段：{b['project_stage']}
- 项目开发商：{b.get('project_developer') or '未知'}
- 中方实体：{_fmt_entities(b)}
- 信源：{src_b}

判断依据（按优先级）：
1. 如果两个项目均没有“location”和“capacity_mw”信息 → 不匹配
2. 省/地区、能源类型、装机容量均一致 → 倾向匹配
    2.1 比较省/地区/城市时，需要注意两个名称不同的location是否位于同一省/地区/城市，如相同则倾向匹配
3. 项目开发商明显不同（非同一企业或集团）→ 倾向不匹配
4. 装机容量差异较大（>10%）→ 倾向不匹配
5. 项目名称仅因来源表述差异（简称/全称/中英混用/省略“项目”“电站”等后缀）而不同，不应视为不同项目的依据
6. 两条记录的项目阶段不同，不构成“不是同一项目”的依据


严格返回 JSON（无其他内容）：
{{"same_project": true或false, "confidence": "high/medium/low", "reasoning": "一句话理由"}}
    """
    try:
        resp = _llm_call_with_retry(
            client.models.generate_content,
            model=LLM_CONFIRM_MODEL, contents=prompt, config=config,
        )
        result = json.loads(resp.text)
        if isinstance(result, list):
            result = result[0]
        if result.get("same_project") is True and result.get("confidence") != "low":
            return True
        if result.get("same_project") is True and result.get("confidence") == "low":
            print(f"    → [低置信度，不合并] {result.get('reasoning', '')}")
        return False
    except Exception as e:
        print(f"    [LLM 确认失败] {e}")
        return False


def _llm_confirm_canonical(
    client: genai.Client,
    config: types.GenerateContentConfig,
    project: Dict,
    db_id: str,
    db_name: str,
    db_name_en: str,
    db_location: str,
    db_capacity: str,
) -> bool:
    """调用 Gemini 确认新记录是否与库中已有 canonical project 为同一物理项目。"""
    prompt = f"""
请判断以下"新记录"与"库中已有项目"是否描述同一个能源项目（同一物理工程）。

新记录：
- 项目名称（中）：{project['project_name']}
- 项目名称（英）：{project.get('project_name_en', '—')}
- 国家：{project['country']}，省/地区/城市：{project['location'] or '未知'}
- 能源类型：{project['category']}，装机容量：{project['capacity_mw'] or '未知'} MW
- 项目阶段：{project['project_stage']}
- 项目开发商：{project.get('project_developer') or '未知'}
- 中方实体：{_fmt_entities(project)}

库中已有项目（ID: {db_id}）：
- 项目名称（中）：{db_name}
- 项目名称（英）：{db_name_en or '—'}
- 国家：{project['country']}，省/地区/城市：{db_location or '未知'}
- 能源类型：{project['category']}，装机容量（代表值）：{db_capacity or '未知'} MW

判断依据（按优先级）：
1. 如果两个项目均没有“location”和“capacity_mw”信息 → 不匹配
2. 省/地区、能源类型、装机容量均一致 → 倾向匹配
    2.1 比较省/地区/城市时，需要注意两个名称不同的location是否位于同一省/地区/城市，如相同则倾向匹配
3. 项目开发商明显不同（非同一企业或集团）→ 倾向不匹配
4. 装机容量差异较大（>10%）→ 倾向不匹配
5. 项目名称仅因来源表述差异（简称/全称/中英混用/省略"项目""电站"等后缀）而不同，不应视为不同项目的依据


严格返回 JSON（无其他内容）：
{{"same_project": true或false, "confidence": "high/medium/low", "reasoning": "一句话理由"}}
    """
    try:
        resp = _llm_call_with_retry(
            client.models.generate_content,
            model=LLM_CONFIRM_MODEL, contents=prompt, config=config,
        )
        result = json.loads(resp.text)
        if isinstance(result, list):
            result = result[0]
        if result.get("same_project") is True and result.get("confidence") != "low":
            return True
        if result.get("same_project") is True and result.get("confidence") == "low":
            print(f"    → [低置信度，不匹配] {result.get('reasoning', '')}")
        return False
    except Exception as e:
        print(f"    [LLM canonical 确认失败] {e}")
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
    try:
        resp = _llm_call_with_retry(
            client.models.generate_content,
            model=GEMINI_MODEL, contents=prompt, config=config,
        )
        return json.loads(resp.text).get("chosen", values[0])
    except Exception:
        return values[0]


def _merge_group(
    client: genai.Client,
    config: types.GenerateContentConfig,
    records: List[Dict],
) -> Dict:
    """将一组确认重复的记录合并为一条。"""
    base = records[0].copy()

    # 合并信源链接（保序去重）
    seen_links: set = set()
    merged_links: List[str] = []
    for r in records:
        for link in r["source_links"]:
            if link and link not in seen_links:
                seen_links.add(link)
                merged_links.append(link)
    base["source_links"] = merged_links

    # exclude OR：任一被合并记录被排除 → 合并结果被排除
    base["excluded"] = 1 if any(r.get("excluded") for r in records) else 0
    if base["excluded"]:
        base["exclude_reason"] = next((r.get("exclude_reason") for r in records if r.get("excluded") and r.get("exclude_reason")), "")

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

    # 合并中方实体（按 implementing_entity + involvement_type 去重）
    seen_ent: set = set()
    merged_entities: List[Dict] = []
    for r in records:
        for e in r["entities"]:
            key = (e["implementing_entity"], e["involvement_type"])
            if e["implementing_entity"] and key not in seen_ent:
                seen_ent.add(key)
                merged_entities.append(e)
    base["entities"] = merged_entities

    # EN 翻译字段：取首个非空值，无需 LLM 仲裁
    for en_field in ["project_name_en"]:
        vals = [r.get(en_field, "") for r in records if r.get(en_field)]
        if vals:
            base[en_field] = vals[0]

    # 字段冲突处理
    conflict_fields = [
        "capacity_mw", "project_developer",
        "planned_start_date", "planned_complete_date",
        "total_investment_usd", "financing_model",
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


def _deduplicate_projects(
    client: genai.Client,
    config: types.GenerateContentConfig,
    projects: List[Dict],
    candidates: List[Tuple[int, int, float]],
) -> List[Dict]:
    """Union-Find 分组后逐对确认（高分自动确认，其余 LLM 核验），合并确认重复的记录。"""
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
        if best_score >= CANONICAL_AUTO_MATCH_SCORE:
            auto_pairs.append((i, j))
        else:
            llm_pairs.append((i, j))

    for i, j in auto_pairs:
        a, b = projects[i], projects[j]
        print(
            f"  [高分自动确认 score={score_map[(i, j)]:.0f}] "
            f"[{a['project_name'][:25]}] vs [{b['project_name'][:25]}]"
        )
        union(i, j)

    if llm_pairs:
        # 并行 LLM 核验（max_workers 由 DEDUP_WORKERS 控制，默认 3；库写仍在主线程串行）
        print(f"  并行 LLM 核验 {len(llm_pairs)} 对候选（workers={DEDUP_WORKERS}）…")

        def _confirm_pair(pair: Tuple[int, int]) -> Tuple[int, int, bool]:
            pi, pj = pair
            a, b = projects[pi], projects[pj]
            print(f"    核验: [{a['project_name'][:25]}] vs [{b['project_name'][:25]}]")
            result = _llm_confirm(client, config, a, b)
            return pi, pj, result

        with ThreadPoolExecutor(max_workers=DEDUP_WORKERS) as exe:
            futures = {exe.submit(_confirm_pair, pair): pair for pair in llm_pairs}
            for fut in as_completed(futures):
                pi, pj, confirmed = fut.result()
                a, b = projects[pi], projects[pj]
                if confirmed:
                    union(pi, pj)
                    print(f"    → [{a['project_name'][:25]}] 确认为同一项目，待合并")
                else:
                    print(f"    → [{a['project_name'][:25]}] 不同项目，各自保留")

    groups: Dict[int, List[int]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    result: List[Dict] = []
    for root, indices in groups.items():
        if len(indices) == 1:
            result.append(projects[indices[0]])
            continue
        # 跨阶段同项目：按 stage 分摊，各阶段内部合并为一条，全部打共享 gid
        # 落库语义 = 一条 canonical + 每阶段一条 event（_write_to_db 按 gid 复用 canonical_id）
        by_stage: Dict[str, List[int]] = defaultdict(list)
        for i in indices:
            by_stage[projects[i]["project_stage"]].append(i)
        gid = f"grp-{root}"
        for stage_indices in by_stage.values():
            recs = [projects[i] for i in stage_indices]
            if len(recs) == 1:
                merged_rec = recs[0]
            else:
                print(f"  合并 {len(recs)} 条 → [{recs[0]['project_name'][:30]}...]")
                merged_rec = _merge_group(client, config, recs)
            if len(by_stage) > 1:
                merged_rec = dict(merged_rec)
                merged_rec["dedup_group_id"] = gid
            result.append(merged_rec)

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
        f"""SELECT capacity_mw FROM project_events
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

    return abs(cap_new - cap_db) / max(cap_new, cap_db) > CANONICAL_CAP_GUARD_TOLERANCE


def _find_or_create_canonical(
    conn: sqlite3.Connection,
    project: Dict,
    client: Optional[genai.Client] = None,
    config: Optional[types.GenerateContentConfig] = None,
    canonical_cache: Optional[Dict[Tuple[str, str], List]] = None,
) -> Tuple[str, bool]:
    """
    查找已有的 canonical_project，或新建一条。

    名称匹配：相同 country_iso + category，比较四个名称对（取最高分）：
      1. incoming CN  vs DB CN
      2. incoming EN  vs DB CN
      3. incoming EN  vs DB EN
      4. incoming CN  vs DB EN

    三级决策：
      score ≥ CANONICAL_AUTO_MATCH_SCORE (97) → 自动匹配（容量守卫：差异 > 20% 且有 LLM 时降级确认）
      CANONICAL_LLM_MIN_SCORE (70) ≤ score < 97 且 client 非空 → LLM 确认
      client 为空时降级：score ≥ FUZZY_THRESHOLD (85) → 自动匹配
      地点辅助（P3b）：score < 70 但规范地名相同 + 容量守卫通过 → 降门槛送 LLM 确认
      其余 → 新建

    返回 (canonical_id, is_new)。
    """
    iso = project["country_iso"] or "UNK"
    cat = project["category"]
    name = project["project_name"]
    incoming_en = (project.get("project_name_en") or "").strip()

    if canonical_cache is not None:
        rows = canonical_cache.get((iso, cat), [])
    else:
        rows = conn.execute(
            "SELECT id, project_name, project_name_en, "
            "COALESCE(NULLIF(location_norm, ''), location, '') "
            "FROM canonical_projects WHERE country_iso = ? AND category = ?",
            (iso, cat),
        ).fetchall()

    best_id: Optional[str] = None
    best_score = 0
    for row_id, row_name, row_name_en, _row_loc in rows:
        score = fuzz.token_sort_ratio(name, row_name or "")
        if incoming_en and row_name:
            score = max(score, fuzz.token_sort_ratio(incoming_en, row_name))
        if incoming_en and row_name_en:
            score = max(score, fuzz.token_sort_ratio(incoming_en, row_name_en))
        if name and row_name_en:
            score = max(score, fuzz.token_sort_ratio(name, row_name_en))
        # 收集满足 LLM 最低阈值的最高分候选（兼容降级模式）
        if score >= CANONICAL_LLM_MIN_SCORE and score > best_score:
            best_id, best_score = row_id, score

    # ── P3b 地点辅助：名称分不足 70 时，同规范地名 + 容量守卫通过 → 仍送 LLM 确认 ──
    # 覆盖跨批次音译变体/异名同项目（名称分可能 <20，加权救不回，走独立通道）
    loc_assist = False
    incoming_loc = _norm_location(project)
    if best_id is None and client is not None and len(incoming_loc) >= 2:
        loc_rows = [r for r in rows if (r[3] or "") == incoming_loc]
        if loc_rows:
            cand = max(loc_rows, key=lambda r: fuzz.token_sort_ratio(name, r[1] or ""))
            if not _capacity_guard(conn, cand[0], project):
                best_id = cand[0]
                best_score = fuzz.token_sort_ratio(name, cand[1] or "")
                loc_assist = True
                print(f"  [地点辅助] {best_id} 同规范地名「{incoming_loc}」，降门槛送 LLM (score={best_score})")

    if best_id:
        if best_score >= CANONICAL_AUTO_MATCH_SCORE and not loc_assist:
            if _capacity_guard(conn, best_id, project) and client is not None:
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
                           LIMIT 1) AS capacity_mw
                   FROM canonical_projects cp
                   WHERE cp.id = ?""",
                (best_id,),
            ).fetchone()
            db_name, db_name_en, db_location, db_capacity = (
                db_row if db_row else ("", "", "", "")
            )
            confirmed = _llm_confirm_canonical(
                client, config, project,
                best_id,
                db_name or "", db_name_en or "",
                db_location or "", db_capacity or "",
            )
            if confirmed:
                print(f"    → LLM 确认匹配 {best_id}")
                return best_id, False
            else:
                print(f"    → LLM 判断为不同项目，新建 canonical")
        elif best_id and best_score >= FUZZY_THRESHOLD:
            # 无 LLM 降级模式：保持原有 ≥ 85 自动匹配行为
            return best_id, False

    # 新建 canonical_project
    new_id = _gen_canonical_id(conn, iso, _get_cat_code(cat))
    name_en = project.get("project_name_en", "")
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
    # 更新内存缓存，使后续项目能匹配到此次新建的 canonical
    if canonical_cache is not None:
        canonical_cache.setdefault((iso, cat), []).append(
            (new_id, name, name_en, _norm_location(project))
        )
    return new_id, True


def _write_to_db(
    conn: sqlite3.Connection,
    projects: List[Dict],
    client: Optional[genai.Client] = None,
    config: Optional[types.GenerateContentConfig] = None,
) -> Dict[str, int]:
    """将合并后的逻辑项目写入三张 SQLite 表，返回各表新增条数。"""
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

    for project in projects:
        # ── canonical_projects ──────────────────────────────────────────────
        gid = project.get("dedup_group_id")
        if gid and gid in group_canonical:
            canonical_id, is_new = group_canonical[gid], False
        else:
            canonical_id, is_new = _find_or_create_canonical(conn, project, client, config, canonical_cache)
            if gid:
                group_canonical[gid] = canonical_id
        if is_new:
            cnt["canonical_new"] += 1

        # ── exclude 传播到 canonical（OR：任一成员排除即排除；可逆、幂等）──
        if project.get("excluded"):
            conn.execute(
                "UPDATE canonical_projects SET excluded=1, "
                "excluded_reason=COALESCE(NULLIF(excluded_reason,''), ?) WHERE id=?",
                (project.get("exclude_reason", "") or None, canonical_id),
            )

        # ── project_events ─────────────────────────────────────────────────
        # 同阶段事件是否已存在（支持重复运行的幂等性）
        existing_event = conn.execute(
            "SELECT id FROM project_events WHERE canonical_id = ? AND project_stage = ?",
            (canonical_id, project["project_stage"]),
        ).fetchone()

        if existing_event:
            event_id = existing_event[0]

            if client is not None:
                # ── 跨批次完整合并（有 LLM 时）──────────────────────────────
                print(f"  [跨批次合并] {event_id} 与新记录执行完整字段合并")
                existing_dict = _fetch_event_as_dict(conn, event_id)
                merged_rec = _merge_group(client, config, [existing_dict, project])
                conn.execute(
                    """UPDATE project_events SET
                           capacity_mw_raw = ?, capacity_mw = ?, capacity_source = ?,
                           capacity_details = ?,
                           project_developer = ?,
                           planned_start_date = ?, planned_complete_date = ?,
                           total_investment_usd_raw = ?, total_investment_usd = ?,
                           investment_date = ?,
                           financing_model = ?, financing_model_details = ?,
                           source_links = ?,
                           official_website_links = ?, non_official_website_links = ?,
                           publish_date = ?, additional_remarks = ?
                       WHERE id = ?""",
                    (
                        merged_rec["capacity_mw"],
                        bri_parse.parse_number(merged_rec["capacity_mw"]),
                        "original",
                        merged_rec["capacity_details"],
                        merged_rec["project_developer"],
                        merged_rec["planned_start_date"],
                        merged_rec["planned_complete_date"],
                        merged_rec["total_investment_usd"],
                        bri_parse.parse_number(merged_rec["total_investment_usd"]),
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
                    project_developer, planned_start_date, planned_complete_date,
                    total_investment_usd_raw, total_investment_usd, investment_date,
                    financing_model, financing_model_details, source_links,
                    official_website_links, non_official_website_links,
                    publish_date, additional_remarks, project_summary, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id, canonical_id,
                    project["project_stage"],
                    project["capacity_mw"], bri_parse.parse_number(project["capacity_mw"]),
                    project["capacity_details"],
                    None, None, "original",  # storage 提取属 v2.1，本次留空
                    project["project_developer"],
                    project["planned_start_date"], project["planned_complete_date"],
                    project["total_investment_usd"], bri_parse.parse_number(project["total_investment_usd"]),
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

        # ── chinese_entities ───────────────────────────────────────────────
        for ent in project.get("entities", []):
            if not ent.get("implementing_entity"):
                continue
            dupe = conn.execute(
                """SELECT 1 FROM chinese_entities
                   WHERE event_id = ? AND implementing_entity = ? AND involvement_type = ?""",
                (event_id, ent["implementing_entity"], ent["involvement_type"]),
            ).fetchone()
            if not dupe:
                conn.execute(
                    """INSERT INTO chinese_entities
                       (event_id, implementing_entity, implementing_entity_en,
                        subsidiary_name, subsidiary_name_en, entity_en_quality,
                        involvement_type, subcontract_amount_raw, subcontract_amount, is_central_soe)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    ),
                )
                cnt["entity_new"] += 1

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
            pe.project_developer, pe.planned_start_date, pe.planned_complete_date,
            pe.total_investment_usd_raw, pe.investment_date,
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
               involvement_type, subcontract_amount_raw, is_central_soe
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
        }
        for r in ent_rows
    ]

    (project_stage, capacity_mw, capacity_details,
     project_developer, planned_start_date, planned_complete_date,
     total_investment_usd, investment_date,
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
        "planned_start_date":         planned_start_date or "",
        "planned_complete_date":      planned_complete_date or "",
        "total_investment_usd":       total_investment_usd or "",
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
) -> int:
    """将设备订单、一般合作类记录写入 non_project_records 表（扁平化，一实体一行）。"""
    now = datetime.now().isoformat()
    count = 0
    for rec in records:
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


class DuplicateMergerTool:
    """从 s01 的 projects.db 读取未导入记录，执行四阶段去重合并流程，写入 SQLite 三表结构。"""

    def _run(
        self,
        input_db: str = "../s01b_project_extractor/data/projects.db",
        db_path: str = "data/db/bri_re_database.db",
    ) -> str:
        # 初始化 Gemini
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return "错误：未找到 GEMINI_API_KEY 环境变量，请检查 .env 文件。"

        client = genai.Client(api_key=api_key)
        gen_config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
        )

        # ── 阶段一：加载 & 归组 ──────────────────────────────────────────────
        print(f"\n>>> [阶段一] 加载数据：{input_db}")
        df, record_ids = _load_from_db(input_db)
        if df.empty:
            return f"未发现待处理记录（{input_db} 中 imported_to_s02a=0 的记录为零）。"
        raw_count = len(df)
        print(f"  原始行数: {raw_count}")

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
            merged = _deduplicate_projects(client, gen_config, projects, candidates)
        else:
            merged = projects
            print(f"  无需合并，直接进入入库阶段")
        print(f"  合并后唯一项目事件数: {len(merged)}")

        # ── 阶段四：SQLite 写入 ──────────────────────────────────────────────
        print(f"\n>>> [阶段四] 写入 SQLite：{db_path}")
        conn = _init_db(db_path)
        counts = _write_to_db(conn, merged, client, gen_config)
        non_project_count = _write_non_project_records(conn, non_projects)
        conn.close()

        # ── 阶段五：标记已导入记录 ───────────────────────────────────────────
        print(f"\n>>> [阶段五] 标记 projects.db 中已导入记录...")
        _mark_imported(input_db, record_ids)

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
