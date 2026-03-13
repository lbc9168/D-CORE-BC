"""duplicate_merger_tool.py
一带一路可再生能源数据库 — 去重合并与 SQLite 入库工具

四阶段处理：
1. 候选筛查：Python 端 fuzzy matching 找潜在跨源重复对
2. LLM 确认：Gemini 确认是否为同一项目（同阶段）
3. 字段仲裁：LLM 解决冲突字段，合并实体列表
4. SQLite 写入：规范化三表结构（canonical_projects / project_events / chinese_entities）
"""

import glob
import json
import os
import sqlite3
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Type

import pandas as pd
from crewai.tools import BaseTool
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from rapidfuzz import fuzz

load_dotenv()

# ── 常量 ──────────────────────────────────────────────────────────────────────

CATEGORY_CODE: Dict[str, str] = {
    "光伏": "PV",
    "风电": "WD",
    "水电": "HY",
    "地热及余热": "GT",
    "生物质": "BM",
    "储能": "ES",
    "综合能源": "HB",
}
FUZZY_THRESHOLD = 85          # CN 名称 token_sort_ratio 阈值
FUZZY_THRESHOLD_EN = 80       # EN 名称 token_sort_ratio 阈值（AI 生成，略低）
FUZZY_PARTIAL_THRESHOLD = 90  # CN 名称 partial_ratio 阈值（捕获子串包含关系）
FUZZY_ENTITY_ASSIST = 60      # 实体重叠辅助：名称达到此分 + 实体重叠 → 送 LLM
CAPACITY_TOLERANCE = 0.10     # 装机容量容错率（10%）
CAPACITY_BYPASS_SCORE = 92    # 任一名称信号 ≥ 此分时豁免容量检查
CAPACITY_EXACT_TOLERANCE = 0.01  # Signal 4 容量精确匹配误差上限（1%）
CAPACITY_MIN_MW = 0.5            # Signal 4 最低容量门槛（过滤无容量或极小容量项目）
GEMINI_MODEL = "gemini-2.5-flash-lite"

# ── SQLite 建表 SQL ────────────────────────────────────────────────────────────

SCHEMA_SQL = """
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
    category         TEXT,
    created_at       TEXT
);

CREATE TABLE IF NOT EXISTS project_events (
    id                      TEXT PRIMARY KEY,   -- e.g. IND-PV-0001-01
    canonical_id            TEXT NOT NULL REFERENCES canonical_projects(id),
    project_stage           TEXT,
    capacity_mw             TEXT,
    capacity_details        TEXT,
    project_developer       TEXT,
    planned_start_date      TEXT,
    planned_complete_date   TEXT,
    total_investment_usd    TEXT,
    investment_date         TEXT,
    chinese_banks_involved  TEXT,
    financing_model         TEXT,
    financing_model_details TEXT,
    source_links            TEXT,   -- JSON 数组
    publish_date            TEXT,
    additional_remarks      TEXT,
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
    subcontract_amount      TEXT
);
"""


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _clean(val: Any) -> str:
    """转为字符串并清理 NA / nan 值。"""
    s = str(val).strip()
    return "" if s in ("nan", "None", "NA", "N/A", "<NA>", "NaN") else s


def _parse_capacity(val: Any) -> Optional[float]:
    """提取装机容量数值，失败返回 None。"""
    try:
        return float(_clean(val))
    except (ValueError, TypeError):
        return None


def _get_cat_code(category: str) -> str:
    return CATEGORY_CODE.get(category, "XX")


# ── 数据库初始化 ────────────────────────────────────────────────────────────────

def _init_db(db_path: str) -> sqlite3.Connection:
    parent = os.path.dirname(os.path.abspath(db_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def _gen_canonical_id(conn: sqlite3.Connection, iso: str, cat_code: str) -> str:
    """生成新的 canonical_project_id，格式 {ISO3}-{CAT}-{NNNN}。"""
    row = conn.execute(
        "SELECT id FROM canonical_projects WHERE id LIKE ? ORDER BY id DESC LIMIT 1",
        (f"{iso}-{cat_code}-%",),
    ).fetchone()
    new_num = (int(row[0].split("-")[-1]) + 1) if row else 1
    return f"{iso}-{cat_code}-{new_num:04d}"


def _gen_event_id(conn: sqlite3.Connection, canonical_id: str) -> str:
    """生成新的 project_event_id，格式 {canonical_id}-{NN}。"""
    row = conn.execute(
        "SELECT id FROM project_events WHERE canonical_id = ? ORDER BY id DESC LIMIT 1",
        (canonical_id,),
    ).fetchone()
    new_num = (int(row[0].split("-")[-1]) + 1) if row else 1
    return f"{canonical_id}-{new_num:02d}"


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
        "chinese_banks":           "Involvement of Chinese Banks",
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
    try:
        conn = sqlite3.connect(input_db)
        placeholders = ",".join("?" * len(record_ids))
        conn.execute(
            f"UPDATE extracted_projects SET imported_to_s02a = 1 WHERE id IN ({placeholders})",
            record_ids,
        )
        conn.commit()
        conn.close()
        print(f"  已标记 {len(record_ids)} 条记录为 imported_to_s02a=1")
    except Exception as e:
        print(f"  [警告] 标记 imported 失败: {e}")


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
                })

        src = _clean(first.get("Source Link", ""))
        logical_projects.append({
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
            "chinese_banks":        _clean(first.get("Involvement of Chinese Banks", "")),
            "financing_model":      _clean(first.get("Financing Model", "")),
            "financing_details":    _clean(first.get("Financing Model Details", "")),
            "source_links":         [src] if src else [],
            "publish_date":         _clean(first.get("Publish Date", "")),
            "additional_remarks":   _clean(first.get("Additional Remarks", "")),
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
    ents_a = {e["implementing_entity"] for e in a.get("entities", []) if e["implementing_entity"]}
    ents_b = {e["implementing_entity"] for e in b.get("entities", []) if e["implementing_entity"]}
    return bool(ents_a & ents_b)


def _find_duplicate_candidates(projects: List[Dict]) -> List[Tuple[int, int]]:
    """
    找出潜在的跨源重复对，采用多信号 OR 逻辑以最大化召回率。

    硬过滤（必须同时满足）：
      - 相同国家 + 能源类型 + 项目阶段
      - 不共享信源链接

    软信号（满足任一即送 LLM 确认）：
      - 名称信号：CN token_sort_ratio / EN token_sort_ratio / CN partial_ratio
      - 实体辅助：共有中方实体 + 名称中度相似（≥ FUZZY_ENTITY_ASSIST）
      - Signal 4（跨语言容量信道）：容量在 1% 误差内且 ≥ 0.5MW
        适用于翻译机制加入前的历史数据：一方以英文作 project_name，
        另一方以中文，名称模糊得分极低（5~30），但容量精确匹配。

    容量豁免：
      - 任一名称信号得分 ≥ CAPACITY_BYPASS_SCORE 时跳过容量检查
        （名称已高度吻合，容量差异由 LLM 判断）
      - Signal 4 路径已在触发时完成容量验证，不再重复检查
    """
    candidates: List[Tuple[int, int]] = []
    n = len(projects)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = projects[i], projects[j]

            # ── 硬过滤 ──────────────────────────────────────────────────────
            if a["country"] != b["country"]:
                continue
            if a["category"] != b["category"]:
                continue
            if a["project_stage"] != b["project_stage"]:
                continue
            if set(a["source_links"]) & set(b["source_links"]):
                continue

            # ── 名称多信号匹配 ───────────────────────────────────────────────
            name_ok, best_score = _name_similarity(a, b)

            # ── 实体重叠辅助（名称中度相似 + 同一中方实体）────────────────
            entity_boost = (
                not name_ok
                and best_score >= FUZZY_ENTITY_ASSIST
                and _entity_overlap(a, b)
            )

            # ── Signal 4：跨语言容量信道（安全网）──────────────────────────
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

            if not name_ok and not entity_boost and not cross_lang_boost:
                continue

            # ── 容量检查（名称高度相似时豁免；Signal 4 路径已验证容量）──
            if not cross_lang_boost and best_score < CAPACITY_BYPASS_SCORE:
                cap_a = _parse_capacity(a["capacity_mw"])
                cap_b = _parse_capacity(b["capacity_mw"])
                if cap_a and cap_b and cap_a > 0 and cap_b > 0:
                    if abs(cap_a - cap_b) / max(cap_a, cap_b) > CAPACITY_TOLERANCE:
                        continue

            candidates.append((i, j))

    return candidates


# ── 阶段三：LLM 确认 & 合并 ────────────────────────────────────────────────────

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
    """调用 Gemini 确认两条记录是否为同一项目（同阶段）。"""
    src_a = a["source_links"][0] if a["source_links"] else "未知"
    src_b = b["source_links"][0] if b["source_links"] else "未知"
    prompt = f"""
请判断以下两条记录是否描述同一个可再生能源项目（同一工程、同一阶段）。

记录A：
- 项目名称（中）：{a['project_name']}
- 项目名称（英）：{a.get('project_name_en', '—')}
- 国家/地区：{a['country']} / {a['location']}
- 能源类型：{a['category']}，装机容量：{a['capacity_mw']} MW
- 项目阶段：{a['project_stage']}
- 中方实体：{_fmt_entities(a)}
- 信源：{src_a}

记录B：
- 项目名称（中）：{b['project_name']}
- 项目名称（英）：{b.get('project_name_en', '—')}
- 国家/地区：{b['country']} / {b['location']}
- 能源类型：{b['category']}，装机容量：{b['capacity_mw']} MW
- 项目阶段：{b['project_stage']}
- 中方实体：{_fmt_entities(b)}
- 信源：{src_b}

严格返回 JSON（无其他内容）：
{{"same_project": true或false, "confidence": "high/medium/low", "reasoning": "一句话理由"}}
    """
    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL, contents=prompt, config=config
        )
        result = json.loads(resp.text)
        return result.get("same_project") is True and result.get("confidence") != "low"
    except Exception as e:
        print(f"    [LLM 确认失败] {e}")
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
        resp = client.models.generate_content(
            model=GEMINI_MODEL, contents=prompt, config=config
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
            time.sleep(0.5)
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
    candidates: List[Tuple[int, int]],
) -> List[Dict]:
    """Union-Find 分组后逐对 LLM 确认，合并确认重复的记录。"""
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

    for i, j in candidates:
        a, b = projects[i], projects[j]
        print(f"  LLM 核验: [{a['project_name'][:25]}] vs [{b['project_name'][:25]}]")
        if _llm_confirm(client, config, a, b):
            union(i, j)
            print(f"    → 确认为同一项目，待合并")
        else:
            print(f"    → 不同项目，各自保留")
        time.sleep(1)

    groups: Dict[int, List[int]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    result: List[Dict] = []
    for indices in groups.values():
        if len(indices) == 1:
            result.append(projects[indices[0]])
        else:
            recs = [projects[i] for i in indices]
            print(f"  合并 {len(recs)} 条 → [{recs[0]['project_name'][:30]}...]")
            result.append(_merge_group(client, config, recs))

    return result


# ── 阶段四：SQLite 写入 ────────────────────────────────────────────────────────

def _find_or_create_canonical(
    conn: sqlite3.Connection,
    project: Dict,
) -> Tuple[str, bool]:
    """
    查找已有的 canonical_project，或新建一条。
    匹配条件：相同 country_iso + category + 任一名称对模糊度 ≥ 85%

    比较的四个名称对（取最高分）：
      1. incoming CN  vs DB CN   — 原有逻辑
      2. incoming EN  vs DB CN   — 旧批次英文 project_name vs 库中英文名（跨语言）
      3. incoming EN  vs DB EN   — 双方均有英文名
      4. incoming CN  vs DB EN   — 新批次中文名 vs 库中已有英文名

    返回 (canonical_id, is_new)。
    """
    iso = project["country_iso"] or "UNK"
    cat = project["category"]
    name = project["project_name"]
    incoming_en = (project.get("project_name_en") or "").strip()

    rows = conn.execute(
        "SELECT id, project_name, project_name_en FROM canonical_projects WHERE country_iso = ? AND category = ?",
        (iso, cat),
    ).fetchall()

    best_id: Optional[str] = None
    best_score = 0
    for row_id, row_name, row_name_en in rows:
        # 1. incoming CN vs DB CN（原有）
        score = fuzz.token_sort_ratio(name, row_name or "")
        # 2. incoming EN vs DB CN（旧批次以英文为 project_name 时命中）
        if incoming_en and row_name:
            score = max(score, fuzz.token_sort_ratio(incoming_en, row_name))
        # 3. incoming EN vs DB EN
        if incoming_en and row_name_en:
            score = max(score, fuzz.token_sort_ratio(incoming_en, row_name_en))
        # 4. incoming CN vs DB EN
        if name and row_name_en:
            score = max(score, fuzz.token_sort_ratio(name, row_name_en))
        if score >= FUZZY_THRESHOLD and score > best_score:
            best_id, best_score = row_id, score

    if best_id:
        return best_id, False

    # 新建 canonical_project
    new_id = _gen_canonical_id(conn, iso, _get_cat_code(cat))
    conn.execute(
        """INSERT INTO canonical_projects
           (id, project_name, project_name_en, name_en_quality,
            country, country_iso, bri_country, ldc, location, category, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            new_id, name,
            project.get("project_name_en", ""),
            project.get("name_en_quality", "ai_translated"),
            project["country"], iso,
            project["bri_country"], project["ldc"],
            project["location"], cat,
            datetime.now().isoformat(),
        ),
    )
    return new_id, True


def _write_to_db(
    conn: sqlite3.Connection,
    projects: List[Dict],
) -> Dict[str, int]:
    """将合并后的逻辑项目写入三张 SQLite 表，返回各表新增条数。"""
    cnt = {"canonical_new": 0, "event_new": 0, "entity_new": 0}

    for project in projects:
        # ── canonical_projects ──────────────────────────────────────────────
        canonical_id, is_new = _find_or_create_canonical(conn, project)
        if is_new:
            cnt["canonical_new"] += 1

        # ── project_events ─────────────────────────────────────────────────
        # 同阶段事件是否已存在（支持重复运行的幂等性）
        existing_event = conn.execute(
            "SELECT id FROM project_events WHERE canonical_id = ? AND project_stage = ?",
            (canonical_id, project["project_stage"]),
        ).fetchone()

        if existing_event:
            event_id = existing_event[0]
            # 仅追加新信源链接
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
                   (id, canonical_id, project_stage, capacity_mw, capacity_details,
                    project_developer, planned_start_date, planned_complete_date,
                    total_investment_usd, investment_date, chinese_banks_involved,
                    financing_model, financing_model_details, source_links,
                    publish_date, additional_remarks, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id, canonical_id,
                    project["project_stage"],
                    project["capacity_mw"],     project["capacity_details"],
                    project["project_developer"],
                    project["planned_start_date"], project["planned_complete_date"],
                    project["total_investment_usd"], project["investment_date"],
                    project["chinese_banks"],
                    project["financing_model"],    project["financing_details"],
                    json.dumps(project["source_links"], ensure_ascii=False),
                    project["publish_date"],   project["additional_remarks"],
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
                        involvement_type, subcontract_amount)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event_id,
                        ent["implementing_entity"],
                        ent.get("implementing_entity_en", ""),
                        ent["subsidiary_name"],
                        ent.get("subsidiary_name_en", ""),
                        ent.get("entity_en_quality", "ai_translated"),
                        ent["involvement_type"],
                        ent["subcontract_amount"],
                    ),
                )
                cnt["entity_new"] += 1

    conn.commit()
    return cnt


# ── Input Schema & Tool ────────────────────────────────────────────────────────

class DuplicateMergerInput(BaseModel):
    input_db: str = Field(
        default="../s01_data_collector_filter/data/projects.db",
        description="s01 提取结果数据库路径（extracted_projects 表，imported_to_s02a=0 的记录为待处理）",
    )
    db_path: str = Field(
        default="data/db/bri_re_database.db",
        description="输出 SQLite 数据库文件路径",
    )


class DuplicateMergerTool(BaseTool):
    name: str = "duplicate_merger_tool"
    description: str = (
        "从 s01 的 projects.db 读取未导入记录，执行四阶段去重合并流程，"
        "将规范化数据写入 SQLite 三表结构"
        "（canonical_projects + project_events + chinese_entities）。"
        "处理完成后标记已导入记录（imported_to_s02a=1），防止重复入库。"
        "返回处理摘要和数据库路径。"
    )
    args_schema: Type[BaseModel] = DuplicateMergerInput

    def _run(
        self,
        input_db: str = "../s01_data_collector_filter/data/projects.db",
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

        projects = _group_into_logical_projects(df)
        print(f"  归组后逻辑项目数: {len(projects)}")

        # ── 阶段二：候选筛查 ─────────────────────────────────────────────────
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
        counts = _write_to_db(conn, merged)
        conn.close()

        # ── 阶段五：标记已导入记录 ───────────────────────────────────────────
        print(f"\n>>> [阶段五] 标记 projects.db 中已导入记录...")
        _mark_imported(input_db, record_ids)

        db_abs = os.path.abspath(db_path)
        return (
            f"处理完成。\n"
            f"  原始行数:          {raw_count}\n"
            f"  逻辑项目数:        {len(projects)}\n"
            f"  去重候选对:        {len(candidates)}\n"
            f"  入库项目事件数:    {len(merged)}\n"
            f"  新建 canonical_projects: {counts['canonical_new']} 条\n"
            f"  新建 project_events:     {counts['event_new']} 条\n"
            f"  新建 chinese_entities:   {counts['entity_new']} 条\n"
            f"  数据库路径: {db_abs}"
        )
