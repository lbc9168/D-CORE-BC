import concurrent.futures
import json
import hashlib
import logging
import os
import re
import sqlite3
import sys
import threading
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

# ── 枚举/解析单一可信源：健壮上溯找仓库根（含 bri_enums.py），避免脆弱的 parents[N] ──
# 必须在导入 guardrails 之前就绪：format_checks/batch_checks 顶部直接 import bri_enums。
_root = next(p for p in Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from s01b_project_extractor.guardrails.format_checks import run_format_checks, run_format_checks_with_ids, VALID_RECORD_TYPES
from s01b_project_extractor.guardrails.batch_checks import run_batch_checks
from s01b_project_extractor.guardrails.nonenergy_check import (
    check_nonenergy, DETERMINISTIC_GUARD_SOURCE,
)
from s01b_project_extractor.capacity_parser import parse_capacity_mw
from s01b_project_extractor.backups import backup_databases
from s01b_project_extractor.guardrails.developer_check import clean_developer
from s01b_project_extractor.prompts.policy_fragments import ENTITY_POLICY_VERSION
from s01b_project_extractor.tools.field_validation import extraction_field_state

logger = logging.getLogger(__name__)


def _normalize_record_type(rt) -> str:
    """Record Type 兜底归一：合法三类原样返回；空/缺失/非法（如把类别「新能源制造业」或事件词「中标/收购」当 Record Type）一律归「具体项目」，避免真项目被 s02a 误分流到 non_project_records。"""
    s = str(rt or "").strip()
    return s if s in VALID_RECORD_TYPES else "具体项目"


def _merge_capacity_details(llm_details: str, parser_note: str | None) -> str:
    """合并 LLM 原值与解析器说明到 capacity_details 列；两者均非空时用分号分隔，否则取非空一方。"""
    a = str(llm_details or "").strip()
    b = str(parser_note or "").strip()
    if a and b:
        return f"{a}；{b}"
    return a or b


def _entity_record_metadata(entity: dict, project: dict, *, title: str = "", content: str = "") -> dict:
    """持久化主体链接与未发布候选，避免名称门禁删除证据后只剩 Unknown。"""
    raw_name = (entity.get("_entity_raw_name") or entity.get("Subsidiary Name")
                or entity.get("Implementing Chinese Entity", ""))
    identity = entity.get("_entity_verification_status", "unmatched_needs_review")
    if identity == "unmatched_needs_review" and entity.get("_identity_evidence_status") == "verified":
        identity = "source_chinese_pending_registry"
    metadata = {
        "_entity_id": entity.get("_entity_id"),
        "_entity_name": entity.get("_entity_name") or raw_name,
        "_group_entity_id": entity.get("_group_entity_id"),
        "_raw_entity_name": raw_name,
        "_identity_status": identity,
        "_entity_evidence_json": json.dumps({
            "project_assessment": project.get("project_assessment"),
            "policy_version": ENTITY_POLICY_VERSION,
            "entity": entity,
            "common_info": project.get("common_info", {}),
            "withheld_capacity_fields": project.get("common_info", {}).get("withheld_capacity_fields", []),
            "field_validation": extraction_field_state(project.get("common_info", {})),
            "source": {"title": title, "content": content},
            "field_review_warnings": project.get("field_review_warnings", []),
            "structure_validation_errors": project.get("structure_validation_errors", []),
            "s5_ignored_corrections": project.get("s5_ignored_corrections", []),
            "summary_warning": project.get("summary_warning"),
            "candidates": project.get("entity_candidates", project.get("chinese_entities", [])),
        }, ensure_ascii=False),
    }
    if "entity_candidates" in project and entity not in project.get("chinese_entities", []):
        # 同一项目的已确认成员正常流转，未确认成员独立留在提取库，补录后可重放。
        foreign = entity.get("_entity_verification_status") == "verified_non_chinese"
        not_participating = entity.get("_participation_evidence_status") == "not_participating"
        rejected = foreign or not_participating
        metadata.update({
            "_excluded": 1, "_exclude_source": "entity_verification",
            "_publication_status": "excluded" if rejected else "pending_review",
            "_needs_review": int(not rejected),
            "_exclude_reason": ("已核实非中企" if foreign else "未参与当前目标项目" if not_participating
                                else "企业身份或参与关系待核实"),
        })
    return metadata


def _publication_status(row: dict) -> str:
    """确定性范围排除优先；待核实与确认排除保持不同的下游含义。"""
    if row.get("_excluded") and row.get("_exclude_source") in ("extraction", DETERMINISTIC_GUARD_SOURCE):
        return "excluded"
    status = row.get("_publication_status")
    if status is not None:
        if status not in {"eligible", "pending_review", "excluded"}:
            raise ValueError(f"非法企业发布状态: {status!r}")
        return status
    if row.get("_excluded"):
        return "pending_review" if row.get("_needs_review") else "excluded"
    return "eligible"


# 仓库根已在文件顶部 import guardrails 前插入 sys.path，这里直接导入枚举/解析单一可信源。
import bri_enums as enums
import bri_parse
from review import queue as review_queue

load_dotenv()

# 工具文件位于 src/s01b_project_extractor/tools/，上溯三层到子项目根目录
_S01B_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_COUNTRY_DB  = str(_S01B_ROOT / "WorldCountryList.json")
_DEFAULT_PROJECTS_DB = str(_S01B_ROOT / "data" / "projects.db")
_DEFAULT_REVIEW_DB   = str(_S01B_ROOT / "data" / "review.db")
_DEFAULT_RAW_NEWS_DB = str(
    _S01B_ROOT.parent / "s01a_news_crawler" / "data" / "raw_news.db"
)

# supporting_docs 位于工作区根目录（_S01B_ROOT 的父级）
_SUPPORTING_DOCS = _S01B_ROOT.parent / "supporting_docs"

# 运行期缓存（每个进程只读一次文件）
_entity_lookup_cache: str = ""        # prompt 注入用（中文层级文本，旧 v1 hierarchy）
_hierarchy_data_cache: dict = {}      # Python 查表用（原始 dict，旧 v1 hierarchy）
_hierarchy_v2_data_cache: dict = {}   # Python 查表用（v2 hierarchy，新解析器专用）
_soe_set_cache: set = set()           # Python 查表用（央企名称 set）
_name_cache: dict = {}                # 中→英翻译缓存（company_name_cache.json）
_name_cache_lock = threading.Lock()   # 写缓存时的线程锁
_last_batch_id: str = ""              # side-channel: 供 guardrail 读取最近批次 ID


# developer 多公司串分隔符 与 中文字符检测（翻译入口守卫用）
_DEV_SEP_RE = re.compile(r"[;；、，,/]+")
_CJK_CHAR_RE = re.compile(r"[一-鿿]")
_CACHE_KEY_MAX_LEN = 30


def _normalize_implementing_entity(value: str) -> str:
    """
    规范化母公司字段。
    - 空值统一为 Unknown，避免被 guardrail 必填规则拦截
    - 非空值去除首尾空白
    """
    s = str(value or "").strip()
    return s if s else "Unknown"


def get_last_batch_id() -> str:
    """返回最近一次成功写入 DB 的 batch_id（供 guardrail side-channel 使用）。"""
    return _last_batch_id


def reset_last_batch_id() -> None:
    """重置 side-channel batch_id（guardrail 读取后调用，防止重试时使用陈旧值）。"""
    global _last_batch_id
    _last_batch_id = ""


def _get_entity_lookup_text() -> str:
    """从 company_hierarchy.json 构建纯中文母子公司层级文本（供 prompt 注入）。"""
    global _entity_lookup_cache
    if _entity_lookup_cache:
        return _entity_lookup_cache
    try:
        data = _load_hierarchy_data()
        lines: list[str] = []
        for company in data.get("companies", []):
            cn = company["company_name_cn"]
            parent_cn = company.get("parent_company_cn")
            if not parent_cn:
                lines.append(f"\n{cn}")
            else:
                lines.append(f"\n{cn}  ← 二级集团，母公司：{parent_cn}")
            for sub2 in company.get("subsidiaries_level2", []):
                lines.append(f"  · {sub2['name_cn']}")
                for sub3 in sub2.get("subsidiaries_level3", []):
                    lines.append(f"      - {sub3['name_cn']}")
        _entity_lookup_cache = "\n".join(lines)
        print(f">>> 已加载母子公司对照表：{len(data.get('companies', []))} 个集团条目")
    except Exception as e:
        print(f">>> [警告] 无法加载 company_hierarchy.json: {e}")
        _entity_lookup_cache = "（对照表加载失败，请根据常识判断母子公司关系）"
    return _entity_lookup_cache


def _load_hierarchy_data() -> dict:
    """加载 company_hierarchy.json 原始数据（带缓存）。"""
    global _hierarchy_data_cache
    if _hierarchy_data_cache:
        return _hierarchy_data_cache
    try:
        _hierarchy_data_cache = json.loads(
            (_SUPPORTING_DOCS / "company_hierarchy.json").read_text(encoding="utf-8")
        )
    except Exception as e:
        print(f">>> [警告] 无法加载 company_hierarchy.json: {e}")
        _hierarchy_data_cache = {"companies": []}
    return _hierarchy_data_cache


def _load_hierarchy_v2_data() -> dict:
    """加载 company_hierarchy_v3.json（schema_version=2）原始数据（带缓存）。"""
    global _hierarchy_v2_data_cache
    if _hierarchy_v2_data_cache:
        return _hierarchy_v2_data_cache
    try:
        _hierarchy_v2_data_cache = json.loads(
            (_SUPPORTING_DOCS / "company_hierarchy_v3.json").read_text(encoding="utf-8")
        )
        n = len(_hierarchy_v2_data_cache.get("groups", []))
        logger.info("已加载 company_hierarchy_v3.json：%d 组", n)
    except Exception as e:
        logger.error("无法加载 company_hierarchy_v3.json: %s", e)
        _hierarchy_v2_data_cache = {"schema_version": 2, "groups": []}
    return _hierarchy_v2_data_cache


def _get_soe_set() -> set:
    """从 soe.md 解析官方央企名单，返回名称 set（带缓存）。"""
    global _soe_set_cache
    if _soe_set_cache:
        return _soe_set_cache
    try:
        names: set[str] = set()
        for line in (_SUPPORTING_DOCS / "soe.md").read_text(encoding="utf-8").splitlines():
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) == 2 and parts[0].isdigit():
                names.add(parts[1])
        _soe_set_cache = names
        print(f">>> 已加载央企名录：{len(names)} 家")
    except Exception as e:
        print(f">>> [警告] 无法加载 soe.md: {e}")
        _soe_set_cache = set()
    return _soe_set_cache


def _load_name_cache() -> dict:
    """加载 company_name_cache.json（带缓存）。"""
    global _name_cache
    if _name_cache:
        return _name_cache
    try:
        raw = json.loads(
            (_SUPPORTING_DOCS / "company_name_cache.json").read_text(encoding="utf-8")
        )
        _name_cache = {k: v for k, v in raw.items() if not k.startswith("_")}
        print(f">>> 已加载企业英文名缓存：{len(_name_cache)} 条")
    except Exception as e:
        print(f">>> [警告] 无法加载 company_name_cache.json: {e}")
        _name_cache = {}
    return _name_cache


def _save_name_cache(cache: dict) -> None:
    """将更新后的缓存写回 company_name_cache.json（线程安全）。"""
    path = _SUPPORTING_DOCS / "company_name_cache.json"
    try:
        # 读取原文件以保留 _comment 等元字段
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
        # 用新 cache 覆盖（保留 _ 开头的元字段）
        merged = {k: v for k, v in existing.items() if k.startswith("_")}
        merged.update(cache)
        path.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        print(f">>> [警告] 无法写回 company_name_cache.json: {e}")


def _resolve_is_central_soe(entity_cn: str) -> int:
    """Python 端确定性判断是否央企（不依赖 LLM）。

    优先级：① v3 登记册（company_hierarchy_v3.json 命中组的 is_central_soe）
             → ② soe.md 名单兜底（透传实体也需要值）
             → ③ 0（未命中，非央企）
    """
    if not entity_cn:
        return 0
    # ① v2 登记册：按集团 canonical_name_cn 精确匹配
    v2_data = _load_hierarchy_v2_data()
    for grp in v2_data.get("groups", []):
        if grp["canonical_name_cn"] == entity_cn:
            return 1 if grp.get("is_central_soe") else 0
    # ② soe.md 兜底
    soe_set = _get_soe_set()
    if entity_cn in soe_set:
        return 1
    return 0


def _resolve_entity_en(name_cn: str, client: "genai.Client") -> str:
    """
    查 company_name_cache.json → 未命中则调用 Gemini 翻译 → 写回缓存。
    优先返回 en_abbr（如有），否则返回 en。
    """
    if not name_cn or name_cn in ("Unknown", "未知", "不详"):
        return name_cn or ""
    # ── ASCII 直通：无中文字符（英文名/代号）原样返回——不查缓存、不翻译、不写缓存 ──
    if not _CJK_CHAR_RE.search(str(name_cn)):
        return str(name_cn)
    with _name_cache_lock:
        cache = _load_name_cache()
        if name_cn in cache:
            entry = cache[name_cn]
            return entry.get("en_abbr") or entry.get("en", "")
    # 缓存未命中 → 调用 Gemini 翻译
    success = True
    try:
        translate_prompt = (
            f"请将以下中国企业名称翻译为英文官方名称（如有官方英文名优先使用，否则给出合理译名）。"
            f"只输出英文名称，不要任何解释：\n{name_cn}"
        )
        resp = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=translate_prompt,
        )
        en_name = resp.text.strip().strip('"').strip("'")
    except Exception as e:
        print(f">>> [警告] 翻译失败 ({name_cn}): {e}")
        en_name = name_cn      # 翻译失败时回退中文
        success = False        # 但**不写缓存**：避免把失败结果（=中文）固化、永久阻止重试（防 503 等瞬时故障污染共享缓存）
    # 垃圾键守卫：多公司串（应在上游拆串）与超长键不固化进缓存；翻译结果照常返回
    if success and not _DEV_SEP_RE.search(name_cn) and len(name_cn) <= _CACHE_KEY_MAX_LEN:
        with _name_cache_lock:
            cache = _load_name_cache()
            if name_cn not in cache:  # 再次检查（double-check locking）
                cache[name_cn] = {"en": en_name, "en_abbr": None, "verified": False, "source": "ai"}
                _save_name_cache(cache)
    return en_name


def _resolve_developer_en(dev_str, client) -> str:
    """业主字段可含多家公司（;／、等分隔）：拆串逐家走 _resolve_entity_en 再以 '; ' 拼回，杜绝整串垃圾缓存键。"""
    s = str(dev_str or "").strip()
    if not s:
        return ""
    parts = [p.strip() for p in _DEV_SEP_RE.split(s) if p.strip()]
    if len(parts) <= 1:
        return _resolve_entity_en(s, client)
    return "; ".join(_resolve_entity_en(p, client) for p in parts)


# ── 国家数据库 ─────────────────────────────────────────────────────────────────

def _load_country_db(json_path: str) -> dict:
    """
    从 JSON 文件加载国家映射表。
    每个国家的正式中文名和所有 aliases_cn 均展开为独立键，指向同一条记录。
    失败时返回最小化内置表。
    """
    try:
        with open(json_path, encoding="utf-8") as f:
            countries = json.load(f)
        db = {}
        for c in countries:
            record = {
                "iso":     c["iso"],
                "bri":     c["bri"],
                "ldc":     c["ldc"],
                "name_en": c.get("name_en", ""),
            }
            db[c["name_cn"]] = record
            for alias in c.get("aliases_cn", []):
                db[alias] = record
        n_countries = len(countries)
        n_keys = len(db)
        print(f">>> 已加载国家列表：{n_countries} 个国家，{n_keys} 个名称/别名键")
        return db
    except Exception as e:
        print(f">>> [警告] 无法读取国家列表 {json_path}: {e}，使用内置最小列表")
        return {"中国": {"iso": "CHN", "bri": "Yes", "ldc": "No", "name_en": "China"}}


def _get_country_meta(country_name: str, db: dict) -> dict:
    """根据国家名查询 ISO / BRI / LDC 信息，支持精确与模糊匹配。"""
    empty = {"iso": "NA", "bri": "NA", "ldc": "NA"}
    if not country_name or country_name == "NA":
        return empty

    country_name = str(country_name).strip()

    # 1. 精确匹配（中文名）
    if country_name in db:
        return db[country_name]

    # 2. 模糊匹配（中文名互含）
    for db_name, data in db.items():
        if db_name in country_name or country_name in db_name:
            return data

    # 3. 英文名匹配
    for db_name, data in db.items():
        en = data.get("name_en", "")
        if en and (en.lower() in country_name.lower() or country_name.lower() in en.lower()):
            return data

    return empty


# ── SQLite 初始化 ──────────────────────────────────────────────────────────────

def _init_projects_db(db_path: str) -> sqlite3.Connection:
    """初始化 projects.db，存储有效提取项目（含增量导入标志）。"""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS extracted_projects (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            record_type             TEXT,
            project_name            TEXT,
            project_name_en         TEXT,
            name_en_quality         TEXT,
            country                 TEXT,
            country_iso             TEXT,
            bri_country             TEXT,
            ldc                     TEXT,
            location                TEXT,
            category                TEXT,
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
            voltage_kv              REAL,
            line_length_km_raw      TEXT,
            line_length_km          REAL,
            transmission_capacity_mw_raw TEXT,
            transmission_capacity_mw REAL,
            production_capacity_raw TEXT,
            production_capacity_value REAL,
            production_capacity_unit TEXT,
            capacity_source         TEXT,
            project_stage           TEXT,
            project_developer       TEXT,
            project_developer_en    TEXT,
            project_developer_evidence TEXT,
            multiple_chinese        TEXT,
            implementing_entity     TEXT,
            implementing_entity_en  TEXT,
            is_central_soe          INTEGER,
            subsidiary_name         TEXT,
            subsidiary_name_en      TEXT,
            involvement_type        TEXT,
            subcontract_amount_usd_raw TEXT,
            subcontract_amount_usd  REAL,
            planned_start_date      TEXT,
            planned_complete_date   TEXT,
            total_investment_usd_raw TEXT,
            total_investment_usd    REAL,
            contract_amount_usd_raw TEXT,
            contract_amount_usd     REAL,
            financing_amount_usd_raw TEXT,
            financing_amount_usd    REAL,
            investment_date         TEXT,
            financing_model         TEXT,
            financing_model_details TEXT,
            source_link             TEXT,
            publish_date            TEXT,
            official_website        TEXT,
            non_official_website    TEXT,
            additional_remarks      TEXT,
            project_summary         TEXT,
            batch_id                TEXT,
            imported_to_s02a        INTEGER DEFAULT 0,
            excluded                INTEGER DEFAULT 0,
            exclude_reason          TEXT,
            exclude_source          TEXT,
            needs_review            INTEGER DEFAULT 0,
            created_at              TEXT
        )
    """)
    # 确保旧表也有新列（兼容升级）
    for col_def in [
        "ALTER TABLE extracted_projects ADD COLUMN project_summary TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN is_central_soe INTEGER",
        "ALTER TABLE extracted_projects ADD COLUMN capacity_mw_raw TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN storage_power_mw REAL",
        "ALTER TABLE extracted_projects ADD COLUMN storage_energy_mwh REAL",
        "ALTER TABLE extracted_projects ADD COLUMN generation_sources_json TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN storage_status TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN storage_power_mw_raw TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN storage_energy_mwh_raw TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN energy_composition_evidence TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN voltage_kv_raw TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN voltage_kv REAL",
        "ALTER TABLE extracted_projects ADD COLUMN line_length_km_raw TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN line_length_km REAL",
        "ALTER TABLE extracted_projects ADD COLUMN transmission_capacity_mw_raw TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN transmission_capacity_mw REAL",
        "ALTER TABLE extracted_projects ADD COLUMN production_capacity_raw TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN production_capacity_value REAL",
        "ALTER TABLE extracted_projects ADD COLUMN production_capacity_unit TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN capacity_source TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN subcontract_amount_usd_raw TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN total_investment_usd_raw TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN is_unnamed_placeholder INTEGER DEFAULT 0",
        "ALTER TABLE extracted_projects ADD COLUMN excluded INTEGER DEFAULT 0",
        "ALTER TABLE extracted_projects ADD COLUMN exclude_reason TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN exclude_source TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN project_developer_en TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN project_developer_evidence TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN needs_review INTEGER DEFAULT 0",
        "ALTER TABLE extracted_projects ADD COLUMN contract_amount_usd_raw TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN contract_amount_usd REAL",
        "ALTER TABLE extracted_projects ADD COLUMN financing_amount_usd_raw TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN financing_amount_usd REAL",
        "ALTER TABLE extracted_projects ADD COLUMN entity_id TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN entity_name TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN group_entity_id TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN raw_entity_name TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN identity_status TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN entity_evidence_json TEXT",
        "ALTER TABLE extracted_projects ADD COLUMN publication_status TEXT",
    ]:
        try:
            conn.execute(col_def)
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
            # 已存在列是幂等迁移的预期边界；其他数据库错误必须中止。
    conn.commit()
    return conn


def mark_format_failed_needs_review(db_path: str, failed_ids: set[int]) -> None:
    """
    把 format_checks 失败的记录在 extracted_projects 打 needs_review=1。

    使用事务 + executemany 批量更新（规则：单次写入超 50 条用 executemany）。
    空集合时直接返回，不开事务。
    """
    if not failed_ids:
        return
    params = [(rid,) for rid in failed_ids]
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.executemany(
                "UPDATE extracted_projects SET needs_review=1 WHERE id=?",
                params,
            )
    except sqlite3.Error as e:
        logger.error("mark_format_failed_needs_review 写库失败: %s | db=%s", e, db_path)
    finally:
        conn.close()


def _init_review_db(db_path: str) -> sqlite3.Connection:
    """初始化 review.db，存储无中方参与和被过滤的文章记录。"""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS no_chinese_records (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            title          TEXT,
            source_link    TEXT UNIQUE,
            publish_date   TEXT,
            batch_id       TEXT,
            created_at     TEXT
        );
        CREATE TABLE IF NOT EXISTS discarded_records (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            title          TEXT,
            source_link    TEXT UNIQUE,
            publish_date   TEXT,
            discard_reason TEXT,
            batch_id       TEXT,
            created_at     TEXT
        );
    """)
    conn.commit()
    return conn


# 单一可信源的生成配置：JSON 输出 + 低温度，供 extract_one 复用
_GENERATION_CONFIG = types.GenerateContentConfig(
    response_mime_type="application/json",
    temperature=0.14,
)


# ── Prompt 构建 ────────────────────────────────────────────────────────────────

def _build_prompt(title: str, content: str, date: str) -> str:
    entity_lookup = _get_entity_lookup_text()
    # 枚举取自单一可信源 bri_enums；category 给 LLM 的列表排除 Unknown（与原 prompt 一致）
    _cat_list = ", ".join(c for c in enums.valid_categories() if c != "Unknown")
    _involve_list = enums.prompt_value_list("involvement_type")
    _financing_list = enums.prompt_value_list("financing_model")
    _stage_list = enums.prompt_value_list("project_stage")
    _source_list = enums.prompt_value_list("generation_source")
    _storage_list = enums.prompt_value_list("storage_status")
    return f"""
    你是一个能源项目审核专家。请从标题或正文提取中国参与的海外电力项目（发电或独立储能）、新能源制造业和输变电工程。

    文章发布日期：{date}
    文章标题：{title}
    文章正文：
    {content[:15000]}
    (内容截断)

    ### 任务目标
    1. **判断**：确认项目中是否有中国企业参与（包括作为业主、承包商、供应商等）。
    2. **筛选**：剔除无关新闻。
    3. **提取**：提取项目详细信息，特别是多方参与和多子项的复杂情况。
    4. **摘要**：为每个有效项目生成一段简短的事实性摘要。

    ### 中国企业参与判断规则（该规则优先级最高）
    如果新闻正文中无任何中国企业名称出现，或项目各环节参与者均为非中国企业，则直接标记为"no_chinese_participation"。

    ### 内容筛选规则
    在提取数据前，请务必执行以下"去除无关信息"规则。符合任一规则的，直接标记为 "irrelevant"：

    1. **去除"与能源无关的基建项目"**：
       - 如果项目主体是学校、医院、机场、铁路、港口、农业灌溉、供水管网、工业园区等。
       - 即使文中提到"电力配套"、"供电工程"、"屋顶光伏改造"（若仅作为附属设施且未提及具体装机容量），也视为无关。
       - **原则**：我们要找的是"发电厂"，而不是"用电户"。

    2. **去除"项目检修和改造信息"**：
        - 如果项目是进行"检修"、"维护"、"改造"、"升级"等，且未提及具体装机容量，则视为无关。

    3. **去除"模糊的组件供应信息"**：
        - 如项目只涉及"组件供应"、"组件销售"等，且未提及具体装机容量或电站项目信息，则视为无关。

    4. **去除只在获批阶段，且无中企参与信息的项目信息**
        - 如果新闻中只提到某国政府部门"批准"或"审批通过"一系列项目，或者某些项目"获批"或"获得批准"，且未提及与中国企业相关的具体的中标或签约信息，则视为无关。

    5. **去除"商务会谈信息"**
        - 如果新闻中只提到各企业领导之间、或者企业与政府部门领导之间的访问、会议交流等，没有提到具体的项目信息，则视为无关。

    ### 关键处理规则
    1. **能源分类 v2 与容量隔离**：
       - 储能不计为发电源。光伏+储能归「光伏」，风电+储能归「风电」；只有风+光等两种及以上发电源才归「综合能源」。单一新能源+柴油/燃气备用仍按新能源类别；明确独立储能才归「储能」。
       - **Capacity (MW)**: 只填发电装机，禁止把 BESS MW 加入或自行求和。
       - **Storage Power / Storage Energy**: 只有项目明确包含储能时，分别逐字填写 MW 与 MWh/GWh 原文短语。
       - **Capacity Details**: 可保留子项明细，例如："PV: 100MW, BESS: 50MW/100MWh"。
       - 技术标签不是四选一：光储仅允许光伏，风储仅允许风电，风光/风光储仅允许综合能源；只有 confirmed 储能才生成带「储」标签。

    2. **TX/MF 专用规格**：
       - TX 的 Capacity (MW) 强制为空；电压、线路长度、输送容量分别写入 TX Voltage/Line Length/Transmission Capacity Evidence。
       - MF 的 Capacity (MW) 强制为空；年产能写入 MF Production Capacity Evidence。普通工业、矿冶化工、传统蓄电池制造不得因产能数字归 MF。
       - 四个 Evidence 必须逐字连续来自标题或正文，非对应类别及未提及字段留空；未提及无需证据。
       - 发电项目内部配套升压站/送出线路不另拆 TX；独立线路、变电站、电网改造、供电工程才归 TX。

    3. **单一能源项目**：
       - **Category**: 选对应的能源种类（如"光伏"）。
       - **Capacity (MW)**: 填数字。
       - **Capacity Details**: 从新闻原文中提取与能源种类和装机容量相关的信息填入，如没有信息则不填。

    3. **项目开发商/业主（Project Developer，证据白名单）**：
       - 只有原文明示业主/开发商/投资建设方、EPC 发包方、项目股权持有方、特许经营权人或 PPA 卖电方时才填；无证据留空，并把连续原文短语写入 Project Developer Evidence。
       - 承包商、设备商、融资机构、购电方、审批政府不是业主。合格投标方/合格开发商名单、入围名单、短名单、候选人、资格预审通过方和参与竞标方（qualified/eligible bidder、shortlisted/prequalified developer）只代表投标资格，除非原文进一步明确最终中标、正式授标、签署 PPA 或持有/开发该具体项目，否则不得填写。
       - 区域或批次级候选名单不得复制到每个具体项目。多个已确定业主用分号 ";" 隔开，只写公司名。

    4. **中方企业填写规则**：
       - 所有参与的中方企业（包括总包、分包、设备商、股权投资方、金融机构等）均需列入 `chinese_entities`。
       - 对于中方金融机构（如进出口银行、国家开发银行、中国银行、工商银行、建设银行、农业银行、民生银行、中国出口信用保险公司等），其 "Type of involvement" 必须填写 **"融资方"**，同时在 "Financing Model Details" 中填写该机构名称。
       - 注意区分：**"融资方"** 仅用于纯金融机构（只提供贷款/担保，不参与工程实施）；**"投融资+EPC"** 用于同一企业既作为股权投资方又承担 EPC 工程任务的情况。

    5. **日期推断与回填规则 (Date Inference)**：
       - **优先级 1 (明确日期)**：如果文中包含具体日期（如"2025年11月15日"），直接填入 `YYYY-MM-DD`。
       - **优先级 2 (近日/日前)**：如果文中仅提及 **"近日"、"日前"** 且表示动作**已发生**，请使用 **文章发布日期 ({date})** 的年份和月份，并将日期统一设为 **01日** (格式：`YYYY-MM-01`)。
       - **优先级 3 (预计年份)**：如果仅提及"预计2026年投产"，填入 `2026-01-01`。
       - **关键词严格映射**：
         - **(Planned) Start Date**：仅对应 [开工, 奠基, 破土动工, 浇筑第一罐混凝土]。**严禁**填入签约、中标、备案日期。
         - **(Planned) Commercial Operation Date**：仅对应 [并网, 投产, 商业运行, 竣工, 全容量并网]。

    6. **融资模式（Financing Models）规则**：
       - 如果文中提到"贷款"、"融资"、"信贷"、"银团贷款"、"双边贷款"、"买方信贷"、"优惠贷款"等，则填入"信贷融资"。
       - 如果文中提到"债券"、"公司债"、"点心债"、"绿色债券"等，则填入"债券融资"。
       - 如果文中提到"股权"、"股权融资"、"股权投资"等，则填入"股权融资"。
       - 如果文中提到"混合融资"，或者同一项目同时涉及信贷融资、债券融资、股权融资中的两项或两项以上，则填入"混合融资"。
       - 如果无法判断，则填入"Unknown"。

    ### 实体标准化知识库 (Knowledge Base)
    **提取 "Implementing Chinese Entity"（母公司）和 "Subsidiary Name"（子公司）时，请强制参考以下对照表。**

    **母子公司层级对照表**（文中出现下列任何企业名称（含简称），必须按层级填写）：
    格式说明：无缩进行为顶级集团；"  · " 缩进为二级子公司；"      - " 缩进为三级子公司。
{entity_lookup}

    **填写规则**：
    - 识别到对照表中任何企业名称，按层级填写：
      * "Implementing Chinese Entity" = 该企业所属的顶级集团（无缩进的主条目）
      * "Subsidiary Name" = 在对照表中找到对应的子公司条目时，填入对照表中的规范名称（"  · " 或 "      - " 条目中的名称）；若文中名称无法在对照表中匹配，则填入文中原始名称
    - 若文中直接出现顶级集团名称，则 Subsidiary Name 留空。
    - 若某企业不在对照表中，根据上下文判断；无法判断则直接填入 Implementing Entity，Subsidiary Name 留空。
    - "Implementing Chinese Entity" 不能为空；如确实无法判断，必须填写 "Unknown"。

    ### 输出格式 (JSON)
    请严格返回符合以下 Schema 的 JSON：
    {{{{
      "analysis": {{{{
        "project_subject": "String",
        "primary_function": "String",
        "is_power_generation_plant": "Boolean",
        "reasoning": "String"
      }}}},
      "status": "String",
      "projects": [
        {{{{
          "project_summary": "String — 按类别概括：发电写装机，储能写MW/MWh，TX写kV/km/MW或MVA，MF写年产能；禁止把TX/MF写成发电装机。",
          "common_info": {{{{
            "Record Type": "String",
            "Project Name": "String",
            "Project Name (EN)": "String",
            "Country": "String",
            "Location(State)": "String",
            "Category": "String",
            "Generation Sources": ["只能选 {_source_list}；TX/MF为空"],
            "Storage Status": "只能选 {_storage_list}；TX/MF为not_mentioned",
            "Storage Power": "String — confirmed 储能功率原文短语或空",
            "Storage Energy": "String — confirmed 储能能量原文短语或空",
            "Energy Composition Evidence": "String — 发电源与储能状态的逐字证据",
            "Capacity (MW)": "String",
            "Capacity Details": "String",
            "TX Voltage Evidence": "String — 仅TX连续逐字证据或空",
            "TX Line Length Evidence": "String — 仅TX连续逐字证据或空",
            "TX Transmission Capacity Evidence": "String — 仅TX连续逐字证据或空",
            "MF Production Capacity Evidence": "String — 仅MF连续逐字证据或空",
            "Project Stage": "String",
            "Project Developer": "String",
            "Project Developer Evidence": "String — 支撑业主判断的连续原文短语；developer 为空时也为空",
            "(Planned) Start Date": "String",
            "(Planned) Complete Date": "String",
            "Total Project Investment (Million USD)": "String",
            "Investment (or Financial Close) Date": "String",
            "Financing Model": "String",
            "Financing Model Details": "String",
            "Official Website link": "String",
            "Non-official website link": "String",
            "Additional Remarks": "String"
          }}}},
          "chinese_entities": [
            {{{{
              "Implementing Chinese Entity": "String",
              "Subsidiary Name": "String",
              "Type of involvement of Chinese Entities": "String",
              "Subcontract Amount (Million USD)": "String"
            }}}}
          ]
        }}}}
      ]
    }}}}

    ### 字段填写补充说明
    - **Record Type**: 描述本条记录的性质，只能选 [具体项目, 设备订单, 一般合作]。
        - **具体项目**：新闻指向一个可识别的具体工程项目（有项目名、地点、装机容量等信息）。
        - **设备订单**：中国企业向某国/某地区签订设备或零部件供货合同，但新闻中没有出现具体的工程项目名称或明确的目标电站。
        - **一般合作**：MOU/框架协议/战略合作协议等，尚无具体项目可指。
        - **判断优先级**：只要新闻中出现了具体的项目名称或电站，即判定为"具体项目"，即使中方角色是设备供应商。
    - **Project Name**: 【必须使用中文填写】格式："国家+区域/省份+装机容量(MW)+项目类型+"项目"+补充说明"。示例："巴基斯坦旁遮普省200MW光伏项目"、"埃塞俄比亚500MW水电项目（二期）"。国家名、项目类型必须用中文；区域/省份无通用中文译名时可保留英文（如"Sindh省"）；【严禁】直接照抄英文原文（如"Pakistan 200MW Solar Project"）。
    - **Project Name (EN)**: 项目名称的英文版本。优先使用项目的官方英文名（如"Karot Hydropower Project"）；若无官方名，则按格式翻译："Country + Capacity MW + Energy Type + Project/Plant [补充说明]"，示例："Pakistan 200MW Solar PV Project (Punjab Province)"、"Ethiopia 500MW Hydropower Project (Phase II)"。
    - **Category**: 只能选 [{_cat_list}]。
        - 如果项目是建设"光伏组件厂"、"锂电池制造基地"、"多晶硅工厂"、"新能源装备产业园"，则视为"新能源制造业"。
            - **区分关键**：看产出单位。如果是"GW/年（产能）"、"万吨/年"，则是工厂。如果是"MW/GW（装机容量）"，才是电站。
        - 如果项目仅涉及"输电线路"、"变电站扩建"、"电网改造"、"升压站建设"等，则选择"输变电建设"。
        - 如果项目是建设"火电厂"、"燃气轮机厂"、"天然气发电厂"、"煤电"，且不涉及与其他新能源发电形式结合，则视为"火电"。
    - **project_summary**: 用2-3句话概括核心事实。发电写装机，储能写MW/MWh，TX写电压/里程/输送容量，MF写年产能；仅基于标题或正文，不做推测。
    - **status**: 枚举值 "valid_project" | "no_chinese_participation" | "irrelevant"
    - **Type of involvement of Chinese Entities**: 只能选 [{_involve_list}]。
    - **Financing Model**: 只能选 [{_financing_list}]。
    - **Project Stage**: 只能选 [{_stage_list}]。
        - Project Stage判断标准补充："完工"被视为"竣工"；"并网发电"被视为"投入运营"；"收购, 中标, 签约, 开工, 在建"你自行判断，以新闻原文为准。
        - 如果项目只涉及到"投标"、"招标"、"竞标"、"投标意向"、"竞标意向"，且未提及与中国企业相关的具体的中标或签约信息，则视为"拟开发"。
    - **未知信息处理**: 找不到的信息，除非是设定必须填入"Unknown"的情况，其余皆不填。特别地，"Implementing Chinese Entity" 为空时必须填 "Unknown"。
    - **Official/Non-official Website link** 和 **Additional Remarks**: 不填写任何信息。
    """


def extract_one(title: str, content: str, date: str, *, client, model: str = "gemini-2.5-flash-lite") -> dict:
    """对单篇文章运行分阶段提取，并按环境配置复用 Gemini 或构建 OpenAI/DeepSeek 路由。"""
    from s01b_project_extractor.llm.client import LLMClient, LLMConfig, build_stage_router_from_env
    from s01b_project_extractor.stages.orchestrator import extract_staged
    # 只有默认 provider 为 Gemini 时才复用批处理入口的连接；OpenAI/DeepSeek 由统一 client 自行创建。
    default_provider = os.getenv("S01B_PROVIDER", "gemini").strip().lower()
    default_llm = None
    if default_provider == "gemini":
        default_model = os.getenv("S01B_MODEL", model).strip()
        default_llm = LLMClient(LLMConfig(provider="gemini", model=default_model), _client=client)
    router = build_stage_router_from_env(default_client=default_llm)
    return extract_staged(title, content, date, llm=router)


def _load_recorded_results(path: str | None) -> dict[str, dict]:
    """Validate recorded extraction output; source fingerprints replace old row IDs."""
    if path is None:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != "s01b-recorded-results-v1" or not isinstance(payload.get("articles"), list):
        raise ValueError("Invalid s01b-recorded-results-v1 cache")
    recorded = {}
    for item in payload["articles"]:
        if not isinstance(item, dict) or set(item) != {"source", "result"}:
            raise ValueError("Recorded article must contain source and result without database IDs")
        source, result = item["source"], item["result"]
        if not isinstance(source, dict) or set(source) != {"url", "title", "publish_date", "content_sha256"} or any(not isinstance(value, str) for value in source.values()):
            raise ValueError("Invalid recorded article source")
        if not source["url"].strip() or not source["title"].strip() or not re.fullmatch(r"[0-9a-f]{64}", source["content_sha256"]):
            raise ValueError("Recorded source requires URL, title and content SHA256")
        try:
            if not re.match(r"^\d{4}-\d{2}-\d{2}(?:$|[T ])", source["publish_date"]):
                raise ValueError("Non-ISO publication date")
            datetime.fromisoformat(source["publish_date"])
        except ValueError as exc:
            raise ValueError("Recorded source publish_date must be an ISO date or datetime") from exc
        if source["url"] in recorded:
            raise ValueError(f"Duplicate recorded source URL: {source['url']}")
        if not isinstance(result, dict) or set(result) != {"status", "analysis", "projects"} or not isinstance(result["status"], str) or result["status"] not in {"valid_project", "chinese_unnamed", "no_chinese_participation", "irrelevant"}:
            raise ValueError("Invalid recorded extraction result status or fields")
        if not isinstance(result["analysis"], dict) or not isinstance(result["projects"], list) or any(not isinstance(project, dict) for project in result["projects"]):
            raise ValueError("Recorded analysis/projects have invalid types")
        if result["status"] in {"valid_project", "chinese_unnamed"} and not result["projects"]:
            raise ValueError("Recorded project result must contain projects")
        if any(set(project) & {"id", "canonical_id", "event_id", "extracted_project_id", "imported_to_s02a"} for project in result["projects"]):
            raise ValueError("Recorded projects must not contain old database IDs")
        recorded[source["url"]] = item
    return recorded


# ── Input Schema ──────────────────────────────────────────────────────────────

class ProjectExtractorInput(BaseModel):
    raw_news_db_path: str = Field(
        default=_DEFAULT_RAW_NEWS_DB,
        description="原始新闻数据库路径（s01a 输出的 raw_news.db）",
    )
    batch_id: str = Field(
        default="",
        description="要处理的批次 ID（为空则处理所有未提取的关键词匹配记录）",
    )
    projects_db_path: str = Field(
        default=_DEFAULT_PROJECTS_DB,
        description="有效项目输出数据库路径（SQLite，跨批次增量累积）",
    )
    review_db_path: str = Field(
        default=_DEFAULT_REVIEW_DB,
        description="审核记录数据库路径（无中方参与 + 被剔除文章，SQLite）",
    )
    country_db_path: str = Field(
        default=_DEFAULT_COUNTRY_DB,
        description="国家数据库 JSON 文件路径，用于补充 ISO、BRI、LDC 信息及别名匹配",
    )


# ── Tool ──────────────────────────────────────────────────────────────────────

class ProjectExtractorTool:
    """从 raw_news.db 读取新闻，调用 AI 提取项目信息并分类写入 SQLite 数据库。"""

    def _run(
        self,
        raw_news_db_path: str = _DEFAULT_RAW_NEWS_DB,
        batch_id: str = "",
        projects_db_path: str = _DEFAULT_PROJECTS_DB,
        review_db_path: str = _DEFAULT_REVIEW_DB,
        country_db_path: str = _DEFAULT_COUNTRY_DB,
        raw_ids: list[int] | None = None,
        max_workers: int = 4,
        backup_existing: bool = True,
        cached_results_path: str | None = None,
    ) -> str:
        """从 raw_news.db 读取新闻，调用 AI 提取项目信息并写入数据库。"""
        _load_recorded_results(cached_results_path)
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("未找到 GEMINI_API_KEY 环境变量，请检查 .env 文件。")

        client = genai.Client(api_key=api_key)
        country_db = _load_country_db(country_db_path)

        try:
            conn = sqlite3.connect(Path(raw_news_db_path).resolve().as_uri() + "?mode=ro", uri=True)
            conditions, params = ["passed_keyword_filter=1"], []
            if batch_id:
                conditions.append("batch_id=?")
                params.append(batch_id)
            if raw_ids is not None:
                if not raw_ids or any(type(value) is not int or value <= 0 for value in raw_ids):
                    raise ValueError("raw_ids 必须为非空正整数列表")
                conditions.append("id IN (" + ",".join("?" for _ in raw_ids) + ")")
                params.extend(raw_ids)
            rows = conn.execute(
                "SELECT url,title,content,publish_date FROM raw_news WHERE "
                + " AND ".join(conditions) + " ORDER BY id", params,
            ).fetchall()
            conn.close()
        except sqlite3.Error as e:
            raise RuntimeError(f"读取 raw_news.db 失败: {raw_news_db_path}: {e}") from e

        if not rows:
            self.last_result = {"status": "completed", "batch_id": batch_id, "failed_urls": [], "input_articles": 0, "skipped_articles": 0, "reused_cached_articles": 0}
            return f"raw_news.db 中无关键词匹配记录（batch_id={batch_id or '全部'}），跳过。"

        df = pd.DataFrame(rows, columns=["原文链接", "文章标题", "正文内容", "发布日期"])
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        effective_batch_id = batch_id or f"all_{ts}"
        return self._process_df(df, effective_batch_id, ts, client,
                                country_db, projects_db_path, review_db_path,
                                max_workers=max_workers, backup_existing=backup_existing,
                                cached_results_path=cached_results_path)

    def _run_from_db(
        self,
        batch_id: str,
        raw_news_db_path: str,
        projects_db_path: str = _DEFAULT_PROJECTS_DB,
        review_db_path: str = _DEFAULT_REVIEW_DB,
        country_db_path: str = _DEFAULT_COUNTRY_DB,
        raw_ids: list[int] | None = None,
        max_workers: int = 4,
        backup_existing: bool = True,
        cached_results_path: str | None = None,
    ) -> str:
        """从 raw_news.db 按 batch_id 读取，供 run_bulk_extract 直接调用。"""
        return self._run(
            raw_news_db_path=raw_news_db_path,
            batch_id=batch_id,
            projects_db_path=projects_db_path,
            review_db_path=review_db_path,
            country_db_path=country_db_path,
            raw_ids=raw_ids, max_workers=max_workers, backup_existing=backup_existing,
            cached_results_path=cached_results_path,
        )

    # ── 核心处理逻辑 ────────────────────────────────────────────────────────

    def _process_df(
        self,
        df: "pd.DataFrame",
        batch_id: str,
        ts: str,
        client: "genai.Client",
        country_db: dict,
        projects_db_path: str,
        review_db_path: str,
        max_workers: int = 4,
        backup_existing: bool = True,
        cached_results_path: str | None = None,
    ) -> str:
        """处理一个 DataFrame，执行 AI 提取并写入数据库。"""
        recorded_results = _load_recorded_results(cached_results_path)
        # 备份必须先于 _init_* 的 schema 迁移；备份失败不得继续写库。
        if backup_existing:
            try:
                backup_databases([projects_db_path, review_db_path])
            except (OSError, sqlite3.Error, ValueError) as exc:
                raise RuntimeError(f"提取前备份失败，已停止: {exc}") from exc
        # 加载已处理过的 URL（去重）
        already_processed: set[str] = set()
        try:
            _chk = _init_projects_db(projects_db_path)
            cur = _chk.cursor()
            cur.execute("SELECT DISTINCT source_link FROM extracted_projects WHERE source_link IS NOT NULL AND source_link != ''")
            already_processed.update(r[0] for r in cur.fetchall())
            _chk.close()
        except sqlite3.Error as e:
            raise RuntimeError(f"预加载 projects.db 去重记录失败: {projects_db_path}: {e}") from e
        try:
            _chk2 = _init_review_db(review_db_path)
            cur2 = _chk2.cursor()
            cur2.execute("SELECT source_link FROM no_chinese_records WHERE source_link IS NOT NULL AND source_link != '' "
                         "UNION SELECT source_link FROM discarded_records WHERE source_link IS NOT NULL AND source_link != ''")
            already_processed.update(r[0] for r in cur2.fetchall())
            _chk2.close()
        except sqlite3.Error as e:
            raise RuntimeError(f"预加载 review.db 去重记录失败: {review_db_path}: {e}") from e
        if already_processed:
            print(f">>> 增量模式：已跳过 {len(already_processed)} 个已处理 URL")

        # 调试：统计本次真正新文章数
        _new_urls = [u for u in df["原文链接"].tolist()
                     if u and str(u) not in already_processed]
        print(f">>> 本次 raw_news.db 提供 {len(df)} 条，"
              f"其中真正未处理 {len(_new_urls)} 条（将进入AI提取）")
        if not _new_urls:
            print(">>> [警告] 全部文章均被 already_processed 跳过，输出将为0条。")

        valid_rows, no_chinese_rows, discarded_rows, all_ai_results = [], [], [], []
        unnamed_review_links: list[str] = []  # source_link of chinese_unnamed articles for review_queue
        entity_review_items: dict[tuple[str, str], dict] = {}
        total = len(df)
        print(f"开始处理 {total} 条数据（并发 {max_workers} 线程）...")

        pre_filter_keywords = [
            "光伏", "太阳能", "风电", "风力", "水电", "地热", "储能",
            "电力", "电站", "发电", "MW", "GW",
        ]

        # 预计算 publish_date 前向填充（顺序依赖，必须在并发前完成）
        filled_dates: list[str] = []
        _last_valid = ""
        for _, _r in df.iterrows():
            _d = str(_r.get("发布日期", ""))
            if _d and _d not in ("nan", "NA", "None", "") and any(c.isdigit() for c in _d):
                _last_valid = _d
            elif _last_valid:
                _d = _last_valid
            filled_dates.append(_d)
        df = df.copy()
        df["发布日期"] = filled_dates

        _lock = threading.Lock()
        _skip_count = [0]  # 用列表包装以便在闭包中修改
        _reused_count = [0]
        failed_articles: list[dict] = []
        _fail_count = [0]  # worker 隔离的单篇异常计数（Design 1：失败篇不落任何表，仅记日志，重跑自动重试）

        def _process_article(row_num_row: tuple) -> None:
            row_num, row = row_num_row
            title     = str(row.get("文章标题", ""))
            content   = str(row.get("正文内容", ""))
            pub_date  = str(row.get("发布日期", ""))
            orig_link = str(row.get("原文链接", ""))

            # 增量去重：原子性检查并占位，防止并发重复处理同一 URL
            if orig_link and orig_link not in ("nan", "NA", "None"):
                with _lock:
                    if orig_link in already_processed:
                        _skip_count[0] += 1
                        return
                    already_processed.add(orig_link)

            # 标题也是本篇证据；仅看正文会漏掉标题明确能源对象的设备订单。
            if not any(k in title + "\n" + content for k in pre_filter_keywords):
                print(f"[{row_num}/{total}] Python预筛剔除: {title[:25]}...")
                with _lock:
                    discarded_rows.append({
                        "文章标题": title,
                        "发布日期": pub_date,
                        "原文链接": orig_link,
                        "正文内容": content,
                        "拒绝原因": "Python关键词预筛不匹配",
                    })
                return

            print("--------------------------------")
            print(f"[{row_num}/{total}] AI处理中: {title[:25]}...")
            cached = recorded_results.get(orig_link)
            if cached is not None:
                source = cached["source"]
                if source["title"] != title or source["publish_date"] != pub_date or source["content_sha256"] != hashlib.sha256(content.encode("utf-8")).hexdigest():
                    raise ValueError(f"Recorded source fingerprint mismatch: {orig_link}; model fallback blocked")
                result = deepcopy(cached["result"])
                audit_result = deepcopy(result)
                with _lock:
                    _reused_count[0] += 1
            else:
                result = extract_one(title, content, pub_date, client=client)
                audit_result = result

            analysis  = result.get("analysis", {})
            status    = result.get("status")
            reasoning = analysis.get("reasoning", "无详细理由")
            subject   = analysis.get("project_subject", "未知主体")

            print(f"  -> 分析: [{subject}] - {reasoning}")
            print(f"  -> 结论: {status}")

            local_valid: list[dict]          = []
            local_no_chinese: list[dict]     = []
            local_discarded: list[dict]      = []
            local_unnamed_review: list[str]  = []

            if status == "irrelevant":
                local_discarded.append({
                    "文章标题": title,
                    "发布日期": pub_date,
                    "原文链接": orig_link,
                    "正文内容": content,
                    "拒绝原因": f"[{subject}] {reasoning}",
                })

            elif status == "no_chinese_participation":
                local_no_chinese.append({
                    "文章标题": title,
                    "发布日期": pub_date,
                    "原文链接": orig_link,
                    "备注": "AI判定无中方参与",
                })

            elif status == "valid_project":
                for proj in result.get("projects", []):
                    common = proj.get("common_info", {})
                    entities = proj.get("entity_candidates", proj.get("chinese_entities", []))
                    project_summary = proj.get("project_summary", "")
                    country_name = common.get("Country", "")
                    meta = _get_country_meta(country_name, country_db)

                    # ── Layer 0 确定性业主守卫：developer 与同事件承包方/设备商重名 → 剔除
                    # （先清洗再翻译，避免给被剔部件白跑翻译）──
                    dev_cn, dev_dropped = clean_developer(
                        common.get("Project Developer", ""), entities, common.get("Category", ""),
                        common.get("Project Developer Evidence", "") or "",
                        title=title, content=content, project_name=common.get("Project Name", ""),
                    )
                    if dev_dropped:
                        logger.info("developer 守卫剔除 %s（项目=%s）",
                                    dev_dropped, common.get("Project Name", ""))

                    base = {
                        "Record Type": _normalize_record_type(common.get("Record Type")),
                        "Project Name": common.get("Project Name", ""),
                        "Project Name (EN)": common.get("Project Name (EN)", ""),
                        "Name EN Quality": common.get("name_en_quality", "ai_translated"),
                        "Country": country_name,
                        "Country ISO": meta["iso"],
                        "BRI country": meta["bri"],
                        "LDCs": meta["ldc"],
                        "Location (State)": common.get("Location(State)", ""),
                        "Category": common.get("Category", ""),
                        "Capacity (MW)": common.get("Capacity (MW)", ""),
                        "Capacity Details": common.get("Capacity Details", ""),
                        # 容量解析结果（来自 s2_basic._apply_capacity_post_process，stage 层单一解析点）
                        # _capacity_parsed_by_stage=True 仅在 common_info 含 capacity_mw_parsed 键时（已由 stage 解析）
                        "_capacity_parsed_by_stage": "capacity_mw_parsed" in common,
                        "_capacity_mw_parsed": common.get("capacity_mw_parsed"),
                        "_capacity_mw_raw_phrase": common.get("capacity_mw_raw_phrase", ""),
                        "_capacity_details_note": common.get("capacity_details_note"),
                        "_generation_sources_json": common.get("generation_sources_json", "[]"),
                        "_storage_status": common.get("storage_status_normalized", "not_mentioned"),
                        "_storage_power_mw_raw": common.get("storage_power_mw_raw_phrase", ""),
                        "_storage_energy_mwh_raw": common.get("storage_energy_mwh_raw_phrase", ""),
                        "_storage_power_mw": common.get("storage_power_mw_parsed"),
                        "_storage_energy_mwh": common.get("storage_energy_mwh_parsed"),
                        "_energy_composition_evidence": common.get(
                            "energy_composition_evidence_normalized", ""
                        ),
                        "_voltage_kv_raw": common.get("voltage_kv_raw_phrase", ""),
                        "_voltage_kv": common.get("voltage_kv_parsed"),
                        "_line_length_km_raw": common.get("line_length_km_raw_phrase", ""),
                        "_line_length_km": common.get("line_length_km_parsed"),
                        "_transmission_capacity_mw_raw": common.get(
                            "transmission_capacity_mw_raw_phrase", ""
                        ),
                        "_transmission_capacity_mw": common.get(
                            "transmission_capacity_mw_parsed"
                        ),
                        "_production_capacity_raw": common.get("production_capacity_raw_phrase", ""),
                        "_production_capacity_value": common.get(
                            "production_capacity_value_parsed"
                        ),
                        "_production_capacity_unit": common.get(
                            "production_capacity_unit_normalized"
                        ),
                        "Project Stage": common.get("Project Stage", ""),
                        "Project Developer": dev_cn,
                        # 业主英文：拆串逐家走 _resolve_entity_en（缓存+Gemini）后 '; ' 拼回
                        "Project Developer (EN)": _resolve_developer_en(dev_cn, client),
                        "Project Developer Evidence": common.get("Project Developer Evidence", ""),
                        "Multiple Chinese Enterprises Involved": (
                            "Yes" if len(entities) > 1 else "No"
                        ),
                        "(Planned) Start Date": common.get("(Planned) Start Date", ""),
                        "(Planned) Complete Date": common.get("(Planned) Complete Date", ""),
                        "Total Project Investment (Million USD)": common.get(
                            "Total Project Investment (Million USD)", ""
                        ),
                        # 三类金额原文短语（S3/S34 stage 注入，供 *_raw 落库；概念不互斥，见 prompt B.5）
                        "_total_investment_usd_raw_phrase": common.get(
                            "total_investment_usd_raw_phrase", ""
                        ),
                        "Contract Amount (Million USD)": common.get(
                            "Contract Amount (Million USD)", ""
                        ),
                        "_contract_amount_usd_raw_phrase": common.get(
                            "contract_amount_usd_raw_phrase", ""
                        ),
                        "Financing Amount (Million USD)": common.get(
                            "Financing Amount (Million USD)", ""
                        ),
                        "_financing_amount_usd_raw_phrase": common.get(
                            "financing_amount_usd_raw_phrase", ""
                        ),
                        "Investment (or Financial Close) Date": common.get(
                            "Investment (or Financial Close) Date", ""
                        ),
                        "Financing Model": common.get("Financing Model", ""),
                        "Financing Model Details": common.get("Financing Model Details", ""),
                        "Publish Date": pub_date,
                        "Source Link": orig_link,
                        "Official Website link": "",
                        "Non-official website link": "",
                        "Additional Remarks": "",
                        "Project Summary": project_summary,
                        # ── exclude 出口：能源范围与中企身份门禁任一失败均不得自动发布 ──
                        "_excluded": int(
                            not proj.get("in_scope", True) or proj.get("entity_gate_excluded", False)
                        ),
                        "_exclude_reason": (
                            str(proj.get("out_of_scope_reason", ""))
                            if not proj.get("in_scope", True)
                            else str(proj.get("entity_review_reason", ""))
                        ),
                        "_exclude_source": (
                            "extraction" if not proj.get("in_scope", True)
                            else ("entity_verification" if proj.get("entity_gate_excluded", False) else "")
                        ),
                        "_needs_review": int(bool(proj.get("needs_review", False))),
                        "_publication_status": proj.get("publication_status"),
                        "_entity_review_reason": str(proj.get("entity_review_reason") or "; ".join(proj.get("structure_validation_errors", []) + proj.get("field_review_warnings", []))),
                        "_entity_evidence_json": json.dumps({
                            "project_assessment": proj.get("project_assessment"),
                            "policy_version": ENTITY_POLICY_VERSION,
                            "candidates": proj.get("entity_candidates", entities),
                            "field_review_warnings": proj.get("field_review_warnings", []),
                            "structure_validation_errors": proj.get("structure_validation_errors", []),
                            "s5_ignored_corrections": proj.get("s5_ignored_corrections", []),
                            "summary_warning": proj.get("summary_warning"),
                        }, ensure_ascii=False),
                        "_entity_review_entities": str(proj.get("entity_review_entities", "")),
                        "_entity_review_reason_type": str(
                            proj.get("entity_review_reason_type") or ("project_fields" if proj.get("structure_validation_errors") or proj.get("field_review_warnings") else "entity_identity")
                        ),
                    }

                    # ── Layer 0 确定性非能源兜底：LLM 判定不作数，命中即强制排除（2026-07-02 审计治本）──
                    if not base["_excluded"] or base.get("_exclude_source") == "entity_verification":
                        _guard_reason = check_nonenergy(
                            base["Project Name"], project_summary, base["Category"],
                            base["Capacity (MW)"], base["Capacity Details"],
                        )
                        if _guard_reason:
                            base["_excluded"] = 1
                            base["_exclude_reason"] = _guard_reason
                            base["_exclude_source"] = DETERMINISTIC_GUARD_SOURCE

                    if not entities:
                        row_data = base.copy()
                        row_data.update({
                            "Implementing Chinese Entity": "Unknown",
                            "Implementing Chinese Entity (EN)": "Unknown",
                            "Is Central SOE": None,
                            "Subsidiary Name": "",
                            "Subsidiary Name (EN)": "",
                            "Type of involvement of Chinese Entities": "",
                            "Subcontract Amount (Million USD)": "",
                        })
                        local_valid.append(row_data)
                    else:
                        for entity in entities:
                            entity_cn = _normalize_implementing_entity(
                                entity.get("Implementing Chinese Entity", "")
                            )
                            sub_cn    = entity.get("Subsidiary Name", "")
                            # Unknown 视为未知，不做央企判定/翻译调用
                            if entity_cn == "Unknown":
                                soe_int = None
                                entity_en = "Unknown"
                            else:
                                # Python 后处理：is_central_soe 查表
                                soe_int = _resolve_is_central_soe(entity_cn)
                                # Python 后处理：英文名查缓存/翻译
                                entity_en = _resolve_entity_en(entity_cn, client)
                            sub_en    = _resolve_entity_en(sub_cn, client) if sub_cn else ""
                            row_data = base.copy()
                            row_data.update(_entity_record_metadata(entity, proj, title=title, content=content))
                            if base.get("_excluded") and base.get("_exclude_source") in (
                                "extraction", DETERMINISTIC_GUARD_SOURCE,
                            ):
                                for key in ("_excluded", "_exclude_source", "_exclude_reason"):
                                    row_data[key] = base[key]
                            row_data.update({
                                "Implementing Chinese Entity": entity_cn,
                                "Implementing Chinese Entity (EN)": entity_en,
                                "Is Central SOE": soe_int,
                                "Subsidiary Name": sub_cn,
                                "Subsidiary Name (EN)": sub_en,
                                "Type of involvement of Chinese Entities": entity.get(
                                    "Type of involvement of Chinese Entities", ""
                                ),
                                "Subcontract Amount (Million USD)": entity.get(
                                    "Subcontract Amount (Million USD)", ""
                                ),
                            })
                            local_valid.append(row_data)

            elif status == "chinese_unnamed":
                # 中企未具名：项目信息写库，实体行用占位符，后续人工补录公司名
                for proj in result.get("projects", []):
                    common = proj.get("common_info", {})
                    project_summary = proj.get("project_summary", "")
                    country_name = common.get("Country", "")
                    meta = _get_country_meta(country_name, country_db)
                    dev_cn, dev_dropped = clean_developer(
                        common.get("Project Developer", ""),
                        [],
                        common.get("Category", ""),
                        common.get("Project Developer Evidence", "") or "",
                        title=title, content=content, project_name=common.get("Project Name", ""),
                    )
                    if dev_dropped:
                        logger.info(
                            "developer 证据守卫剔除 %s（未具名中企项目=%s）",
                            dev_dropped,
                            common.get("Project Name", ""),
                        )

                    base = {
                        "Record Type": _normalize_record_type(common.get("Record Type")),
                        "Project Name": common.get("Project Name", ""),
                        "Project Name (EN)": common.get("Project Name (EN)", ""),
                        "Name EN Quality": common.get("name_en_quality", "ai_translated"),
                        "Country": country_name,
                        "Country ISO": meta["iso"],
                        "BRI country": meta["bri"],
                        "LDCs": meta["ldc"],
                        "Location (State)": common.get("Location(State)", ""),
                        "Category": common.get("Category", ""),
                        "Capacity (MW)": common.get("Capacity (MW)", ""),
                        "Capacity Details": common.get("Capacity Details", ""),
                        # 容量解析结果（来自 s2_basic._apply_capacity_post_process，stage 层单一解析点）
                        "_capacity_parsed_by_stage": "capacity_mw_parsed" in common,
                        "_capacity_mw_parsed": common.get("capacity_mw_parsed"),
                        "_capacity_mw_raw_phrase": common.get("capacity_mw_raw_phrase", ""),
                        "_capacity_details_note": common.get("capacity_details_note"),
                        "_generation_sources_json": common.get("generation_sources_json", "[]"),
                        "_storage_status": common.get("storage_status_normalized", "not_mentioned"),
                        "_storage_power_mw_raw": common.get("storage_power_mw_raw_phrase", ""),
                        "_storage_energy_mwh_raw": common.get("storage_energy_mwh_raw_phrase", ""),
                        "_storage_power_mw": common.get("storage_power_mw_parsed"),
                        "_storage_energy_mwh": common.get("storage_energy_mwh_parsed"),
                        "_energy_composition_evidence": common.get(
                            "energy_composition_evidence_normalized", ""
                        ),
                        "_voltage_kv_raw": common.get("voltage_kv_raw_phrase", ""),
                        "_voltage_kv": common.get("voltage_kv_parsed"),
                        "_line_length_km_raw": common.get("line_length_km_raw_phrase", ""),
                        "_line_length_km": common.get("line_length_km_parsed"),
                        "_transmission_capacity_mw_raw": common.get(
                            "transmission_capacity_mw_raw_phrase", ""
                        ),
                        "_transmission_capacity_mw": common.get(
                            "transmission_capacity_mw_parsed"
                        ),
                        "_production_capacity_raw": common.get("production_capacity_raw_phrase", ""),
                        "_production_capacity_value": common.get(
                            "production_capacity_value_parsed"
                        ),
                        "_production_capacity_unit": common.get(
                            "production_capacity_unit_normalized"
                        ),
                        "Project Stage": common.get("Project Stage", ""),
                        "Project Developer": dev_cn,
                        # 业主英文：拆串逐家走 _resolve_entity_en（缓存+Gemini）后 '; ' 拼回
                        # unnamed 路径无实体角色可交叉，但仍执行证据/候选名单守卫。
                        "Project Developer (EN)": _resolve_developer_en(dev_cn, client),
                        "Project Developer Evidence": common.get("Project Developer Evidence", ""),
                        "Multiple Chinese Enterprises Involved": "No",
                        "(Planned) Start Date": common.get("(Planned) Start Date", ""),
                        "(Planned) Complete Date": common.get("(Planned) Complete Date", ""),
                        "Total Project Investment (Million USD)": common.get(
                            "Total Project Investment (Million USD)", ""
                        ),
                        # 三类金额原文短语（S3/S34 stage 注入，供 *_raw 落库；概念不互斥，见 prompt B.5）
                        "_total_investment_usd_raw_phrase": common.get(
                            "total_investment_usd_raw_phrase", ""
                        ),
                        "Contract Amount (Million USD)": common.get(
                            "Contract Amount (Million USD)", ""
                        ),
                        "_contract_amount_usd_raw_phrase": common.get(
                            "contract_amount_usd_raw_phrase", ""
                        ),
                        "Financing Amount (Million USD)": common.get(
                            "Financing Amount (Million USD)", ""
                        ),
                        "_financing_amount_usd_raw_phrase": common.get(
                            "financing_amount_usd_raw_phrase", ""
                        ),
                        "Investment (or Financial Close) Date": common.get(
                            "Investment (or Financial Close) Date", ""
                        ),
                        "Financing Model": common.get("Financing Model", ""),
                        "Financing Model Details": common.get("Financing Model Details", ""),
                        "Publish Date": pub_date,
                        "Source Link": orig_link,
                        "Official Website link": "",
                        "Non-official website link": "",
                        "Additional Remarks": "",
                        "Project Summary": project_summary,
                        # 占位实体标记（写库时用于设置 is_unnamed_placeholder=1）
                        "_is_unnamed_placeholder": True,
                        "_publication_status": "pending_review",
                        # ── exclude 出口：未具名中企必须进入复核，不得自动发布 ──
                        "_excluded": 1,
                        "_exclude_reason": (
                            str(proj.get("out_of_scope_reason", ""))
                            if not proj.get("in_scope", True)
                            else str(
                                proj.get("entity_review_reason", "")
                                or "原文明示中企参与但未具名，待人工复核"
                            )
                        ),
                        "_exclude_source": (
                            "extraction" if not proj.get("in_scope", True)
                            else "entity_verification"
                        ),
                        "_needs_review": 1,
                    }

                    # ── Layer 0 确定性非能源兜底：LLM 判定不作数，命中即强制排除（2026-07-02 审计治本）──
                    if not base["_excluded"] or base.get("_exclude_source") == "entity_verification":
                        _guard_reason = check_nonenergy(
                            base["Project Name"], project_summary, base["Category"],
                            base["Capacity (MW)"], base["Capacity Details"],
                        )
                        if _guard_reason:
                            base["_excluded"] = 1
                            base["_exclude_reason"] = _guard_reason
                            base["_exclude_source"] = DETERMINISTIC_GUARD_SOURCE

                    row_data = base.copy()
                    row_data.update({
                        "Implementing Chinese Entity": enums.UNNAMED_ENTITY_PLACEHOLDER,
                        "Implementing Chinese Entity (EN)": "",
                        "Is Central SOE": None,
                        "Subsidiary Name": "",
                        "Subsidiary Name (EN)": "",
                        "Type of involvement of Chinese Entities": "Unknown",
                        "Subcontract Amount (Million USD)": "",
                    })
                    local_valid.append(row_data)
                    local_unnamed_review.append(orig_link)

            with _lock:
                all_ai_results.append({"index": row_num, "title": title, "result": audit_result})
                valid_rows.extend(local_valid)
                no_chinese_rows.extend(local_no_chinese)
                discarded_rows.extend(local_discarded)
                if status == "chinese_unnamed":
                    unnamed_review_links.extend(local_unnamed_review)

        def _process_article_safe(row_num_row: tuple) -> None:
            """worker 隔离：单篇任何异常只记日志(含 traceback)+计数，不落任何表；失败篇不进去重集，重跑 run_bulk_extract 会自动重试（Design 1）。"""
            try:
                _process_article(row_num_row)
            except Exception as e:
                row_num, row = row_num_row
                with _lock:
                    _fail_count[0] += 1
                    failed_articles.append({"url": str(row.get("原文链接", "")), "title": str(row.get("文章标题", "")), "error": str(e), "error_type": type(e).__name__})
                logger.error(
                    "文章处理失败（已隔离跳过，不中断批次；未落库，重跑将重试）row=%s link=%s title=%s: %s",
                    row_num, str(row.get("原文链接", ""))[:120],
                    str(row.get("文章标题", ""))[:40], e, exc_info=True,
                )

        # 并发处理所有文章（每篇经 _process_article_safe 隔离，单篇异常不拖垮整批）
        rows_indexed = [(i + 1, row) for i, (_, row) in enumerate(df.iterrows())]
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            list(executor.map(_process_article_safe, rows_indexed))
        if _fail_count[0]:
            logger.error("本批 %s：%d 篇因异常被隔离跳过（未落库，重跑 run_bulk_extract 会自动重试）",
                         batch_id, _fail_count[0])

        if _skip_count[0]:
            print(f">>> 共跳过 {_skip_count[0]} 条已处理文章（已在数据库中）")

        # ── 写入 SQLite 数据库 ────────────────────────────────────────────────
        created_at  = datetime.now().isoformat()
        proj_conn = None
        review_conn = None

        def _dump_failed_parse_results(err_msg: str) -> str:
            """入库失败时兜底保存解析结果，避免长批次重跑导致结果丢失。"""
            json_dir = Path(projects_db_path).parent
            json_path = json_dir / f"All_AI_Results_{ts}_failed.json"
            payload = {
                "batch_id": batch_id,
                "created_at": created_at,
                "error": err_msg,
                "stats": {
                    "valid_rows": len(valid_rows),
                    "no_chinese_rows": len(no_chinese_rows),
                    "discarded_rows": len(discarded_rows),
                    "all_ai_results": len(all_ai_results),
                },
                "all_ai_results": all_ai_results,
                "valid_rows": valid_rows,
                "no_chinese_rows": no_chinese_rows,
                "discarded_rows": discarded_rows,
            }
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            return str(json_path)

        try:
            proj_conn = _init_projects_db(projects_db_path)
            review_conn = _init_review_db(review_db_path)

            # 写入 projects.db（有中国主体的有效项目）
            for row_data in valid_rows:
                # 入库前兜底标准化，防止任何路径写入空 implementing_entity
                impl_entity = _normalize_implementing_entity(
                    row_data.get("Implementing Chinese Entity", "")
                )
                impl_entity_en = str(
                    row_data.get("Implementing Chinese Entity (EN)", "")
                ).strip() or ("Unknown" if impl_entity == "Unknown" else "")
                # 数值字段：原文短语写入 capacity_mw_raw，确定性解析结果写入 capacity_mw（REAL）
                # 解析结果来自 stage 层（s2_basic._apply_capacity_post_process）的单一解析点；
                # _capacity_parsed_by_stage=True 表示 common_info 已含 capacity_mw_parsed（新路径）；
                # False 表示旧路径/legacy 数据，回退到就地解析，确保任何路径恰好过一次解析器。
                if row_data.get("_capacity_parsed_by_stage"):
                    cap_raw = row_data.get("_capacity_mw_raw_phrase") or row_data.get("Capacity (MW)", "")
                    cap_mw_val = row_data.get("_capacity_mw_parsed")
                    _cap_details_note = row_data.get("_capacity_details_note")
                else:
                    # 旧路径兜底：就地解析（保证任何路径的 capacity 恰好过一次解析器）
                    cap_raw = row_data.get("Capacity (MW)", "")
                    cap_mw_val, _cap_details_note = parse_capacity_mw(
                        str(cap_raw) if cap_raw else None,
                        category=row_data.get("Category"),
                    )
                sub_raw = row_data.get("Subcontract Amount (Million USD)", "")
                # 三类金额原文短语（stage 层注入 *_raw_phrase，写入对应 *_raw 列；概念不互斥，见 prompt B.5）
                inv_raw = row_data.get("_total_investment_usd_raw_phrase") or ""
                inv_val_field = row_data.get("Total Project Investment (Million USD)", "")
                contract_raw = row_data.get("_contract_amount_usd_raw_phrase") or ""
                contract_val_field = row_data.get("Contract Amount (Million USD)", "")
                financing_raw = row_data.get("_financing_amount_usd_raw_phrase") or ""
                financing_val_field = row_data.get("Financing Amount (Million USD)", "")
                is_unnamed_ph = 1 if row_data.get("_is_unnamed_placeholder") else 0
                cursor = proj_conn.execute(
                    """INSERT INTO extracted_projects
                       (record_type, project_name, project_name_en, name_en_quality, country, country_iso,
                        bri_country, ldc, location, category, capacity_mw_raw, capacity_mw, capacity_details,
                        storage_power_mw, storage_energy_mwh, capacity_source,
                        project_stage, project_developer, project_developer_en, project_developer_evidence,
                        multiple_chinese, implementing_entity,
                        implementing_entity_en, is_central_soe, subsidiary_name, subsidiary_name_en,
                        involvement_type, subcontract_amount_usd_raw, subcontract_amount_usd, planned_start_date,
                        planned_complete_date, total_investment_usd_raw, total_investment_usd,
                        contract_amount_usd_raw, contract_amount_usd,
                        financing_amount_usd_raw, financing_amount_usd, investment_date,
                        financing_model, financing_model_details,
                        source_link, publish_date, official_website, non_official_website,
                        additional_remarks, project_summary, batch_id, imported_to_s02a,
                        excluded, exclude_reason, exclude_source,
                        is_unnamed_placeholder, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)""",
                    (
                        row_data.get("Record Type", "具体项目"),
                        row_data.get("Project Name", ""),
                        row_data.get("Project Name (EN)", ""),
                        row_data.get("Name EN Quality", ""),
                        row_data.get("Country", ""),
                        row_data.get("Country ISO", ""),
                        row_data.get("BRI country", ""),
                        row_data.get("LDCs", ""),
                        row_data.get("Location (State)", ""),
                        row_data.get("Category", ""),
                        cap_raw,
                        cap_mw_val,
                        # #1：capacity_details 合并 LLM 原值与解析器说明（产能/输送类说明落库）
                        _merge_capacity_details(
                            row_data.get("Capacity Details", ""), _cap_details_note
                        ),
                        row_data.get("_storage_power_mw"),
                        row_data.get("_storage_energy_mwh"),
                        "original",  # capacity_source：新提取数值来自原文
                        row_data.get("Project Stage", ""),
                        row_data.get("Project Developer", ""),
                        row_data.get("Project Developer (EN)", ""),
                        row_data.get("Project Developer Evidence", ""),
                        row_data.get("Multiple Chinese Enterprises Involved", ""),
                        impl_entity,
                        impl_entity_en,
                        row_data.get("Is Central SOE"),
                        row_data.get("Subsidiary Name", ""),
                        row_data.get("Subsidiary Name (EN)", ""),
                        row_data.get("Type of involvement of Chinese Entities", ""),
                        sub_raw,
                        bri_parse.parse_number(sub_raw),
                        row_data.get("(Planned) Start Date", ""),
                        row_data.get("(Planned) Complete Date", ""),
                        inv_raw or None,
                        bri_parse.parse_number(inv_val_field),
                        contract_raw or None,
                        bri_parse.parse_number(contract_val_field),
                        financing_raw or None,
                        bri_parse.parse_number(financing_val_field),
                        row_data.get("Investment (or Financial Close) Date", ""),
                        row_data.get("Financing Model", ""),
                        row_data.get("Financing Model Details", ""),
                        row_data.get("Source Link", ""),
                        row_data.get("Publish Date", ""),
                        row_data.get("Official Website link", ""),
                        row_data.get("Non-official website link", ""),
                        row_data.get("Additional Remarks", ""),
                        row_data.get("Project Summary", ""),
                        batch_id,
                        int(row_data.get("_excluded", 0)),
                        row_data.get("_exclude_reason", "") or None,
                        row_data.get("_exclude_source", "") or None,
                        is_unnamed_ph,
                        created_at,
                    ),
                )
                proj_conn.execute(
                    """UPDATE extracted_projects SET generation_sources_json=?, storage_status=?,
                       storage_power_mw_raw=?, storage_energy_mwh_raw=?, energy_composition_evidence=?,
                       voltage_kv_raw=?, voltage_kv=?, line_length_km_raw=?, line_length_km=?,
                       transmission_capacity_mw_raw=?, transmission_capacity_mw=?,
                       production_capacity_raw=?, production_capacity_value=?, production_capacity_unit=?,
                       needs_review=?, entity_id=?, entity_name=?, group_entity_id=?,
                       raw_entity_name=?, identity_status=?, entity_evidence_json=?, publication_status=?
                       WHERE id=?""",
                    (
                        row_data.get("_generation_sources_json", "[]"),
                        row_data.get("_storage_status", "not_mentioned"),
                        row_data.get("_storage_power_mw_raw") or None,
                        row_data.get("_storage_energy_mwh_raw") or None,
                        row_data.get("_energy_composition_evidence") or None,
                        row_data.get("_voltage_kv_raw") or None,
                        row_data.get("_voltage_kv"),
                        row_data.get("_line_length_km_raw") or None,
                        row_data.get("_line_length_km"),
                        row_data.get("_transmission_capacity_mw_raw") or None,
                        row_data.get("_transmission_capacity_mw"),
                        row_data.get("_production_capacity_raw") or None,
                        row_data.get("_production_capacity_value"),
                        row_data.get("_production_capacity_unit"),
                        int(row_data.get("_needs_review", 0)),
                        row_data.get("_entity_id"),
                        row_data.get("_entity_name"),
                        row_data.get("_group_entity_id"),
                        row_data.get("_raw_entity_name"),
                        row_data.get("_identity_status"),
                        row_data.get("_entity_evidence_json"),
                        _publication_status(row_data),
                        cursor.lastrowid,
                    ),
                )
                if row_data.get("_needs_review") and not row_data.get("_is_unnamed_placeholder"):
                    review_key = (
                        str(row_data.get("Source Link", "")),
                        str(row_data.get("Project Name", "")),
                    )
                    entity_review_items.setdefault(review_key, {
                        "canonical_id": str(cursor.lastrowid),
                        "source_link": review_key[0],
                        "reason_type": row_data.get("_entity_review_reason_type") or "entity_identity",
                        "current_value": (row_data.get("Project Name", "") if row_data.get("_entity_review_reason_type") == "project_fields" else row_data.get("_entity_review_entities") or row_data.get(
                            "Implementing Chinese Entity", ""
                        )),
                        "evidence": json.dumps({
                            "reason": row_data.get("_entity_review_reason") or row_data.get("_exclude_reason", ""),
                            "project_name": row_data.get("Project Name", ""),
                            "publication_status": _publication_status(row_data),
                            "entity_evidence": json.loads(row_data.get("_entity_evidence_json") or "{}"),
                        }, ensure_ascii=False),
                    })
            global _last_batch_id
            _last_batch_id = batch_id

            # 写入 review.db → no_chinese_records
            for row in no_chinese_rows:
                try:
                    review_conn.execute(
                        """INSERT OR IGNORE INTO no_chinese_records
                           (title, source_link, publish_date, batch_id, created_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        (
                            row.get("文章标题", ""),
                            row.get("原文链接", ""),
                            row.get("发布日期", ""),
                            batch_id,
                            created_at,
                        ),
                    )
                except sqlite3.Error as e:
                    # INSERT OR IGNORE 不应失败，记录异常供排查
                    raise RuntimeError(f"写入 no_chinese_records 失败: {e}") from e
            review_conn.commit()

            # 写入 review.db → discarded_records
            for row in discarded_rows:
                try:
                    review_conn.execute(
                        """INSERT OR IGNORE INTO discarded_records
                           (title, source_link, publish_date, discard_reason, batch_id, created_at)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            row.get("文章标题", ""),
                            row.get("原文链接", ""),
                            row.get("发布日期", ""),
                            row.get("拒绝原因", ""),
                            batch_id,
                            created_at,
                        ),
                    )
                except sqlite3.Error as e:
                    # INSERT OR IGNORE 不应失败，记录异常供排查
                    raise RuntimeError(f"写入 discarded_records 失败: {e}") from e
            review_conn.commit()

            # 写入 review_queue：未具名与登记表未命中条目在单个事务中批量入队。
            queue_items = [
                {
                    "canonical_id": None,
                    "source_link": link,
                    "reason_type": "unnamed_entity",
                    "field": None,
                    "current_value": None,
                    "suggested_value": None,
                    "critic_confidence": None,
                    "evidence": "S1 判 chinese_unnamed，待补公司名",
                }
                for link in unnamed_review_links
            ]
            queue_items.extend(
                {
                    "canonical_id": item["canonical_id"],
                    "source_link": item["source_link"],
                    "reason_type": "low_confidence_field" if item["reason_type"] == "project_fields" else item["reason_type"],
                    "field": (
                        None if item["reason_type"] == "project_fields" else
                        "involvement_type"
                        if item["reason_type"] == "entity_relation"
                        else "implementing_entity"
                    ),
                    "current_value": item["current_value"],
                    "suggested_value": None,
                    "critic_confidence": None,
                    "evidence": item["evidence"],
                }
                for item in entity_review_items.values()
            )
            if queue_items:
                review_queue.init_queue(review_db_path)  # 幂等建表
                try:
                    review_queue.add_items(review_db_path, queue_items)
                except (sqlite3.Error, ValueError) as e:
                    raise RuntimeError(f"批量写入 review_queue 失败: {e}") from e
            proj_conn.commit()
        except Exception as e:
            fallback_path = _dump_failed_parse_results(str(e))
            raise RuntimeError(
                f"写入数据库失败，解析结果已保存至: {fallback_path}，原始错误: {e}"
            ) from e
        finally:
            if proj_conn is not None:
                proj_conn.close()
            if review_conn is not None:
                review_conn.close()

        # ── 批级 guardrail（format + batch）：bulk/单批写库后统一校验，修掉原 bulk 裸奔 ──
        # 不删记录、不抛错（bulk 无 LLM 重跑循环）；仅 logging.error 记录，交由人工复核。
        # format_checks 失败的记录同步打 needs_review=1，供人工复核队列消费。
        try:
            _vconn = sqlite3.connect(projects_db_path)
            _vconn.row_factory = sqlite3.Row
            _vrows = [dict(r) for r in _vconn.execute(
                "SELECT * FROM extracted_projects WHERE batch_id=?", (batch_id,)).fetchall()]
            _vconn.close()
            _fmt_errors, _fmt_failed_ids = run_format_checks_with_ids(_vrows)
            if _fmt_failed_ids:
                mark_format_failed_needs_review(projects_db_path, _fmt_failed_ids)
            _batch_errors, _batch_warns = run_batch_checks(_vrows, total_input_articles=len(df))
            if _fmt_errors or _batch_errors:
                logger.error("批 %s guardrail 异常 format=%d batch=%d 示例=%s",
                             batch_id, len(_fmt_errors), len(_batch_errors),
                             (_fmt_errors[:3] + _batch_errors[:3]))
        except sqlite3.Error as e:
            raise RuntimeError(f"批 {batch_id} guardrail 校验读库失败: {e}") from e

        # AI 原始结果 JSON
        json_path_str = ""
        if all_ai_results:
            json_dir = Path(projects_db_path).parent
            json_path = json_dir / f"All_AI_Results_{ts}.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(all_ai_results, f, ensure_ascii=False, indent=2)
            json_path_str = f"\n  AI原始结果: {len(all_ai_results)} 条 → {json_path}"

        self.last_result = {
            "status": "partial" if failed_articles else "completed", "batch_id": batch_id,
            "input_articles": total, "skipped_articles": _skip_count[0],
            "reused_cached_articles": _reused_count[0],
            "failed_urls": [item["url"] for item in failed_articles], "failures": failed_articles,
            "valid_rows": len(valid_rows), "no_chinese_rows": len(no_chinese_rows),
            "discarded_rows": len(discarded_rows),
            "projects_db_path": str(Path(projects_db_path).resolve()),
            "review_db_path": str(Path(review_db_path).resolve()),
        }
        report_path = Path(projects_db_path).parent / f"extraction_batch_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"
        report_path.write_text(json.dumps(self.last_result, ensure_ascii=False, indent=2), encoding="utf-8")
        self.last_result["report_path"] = str(report_path)

        if not valid_rows and not no_chinese_rows and not discarded_rows:
            return "处理完成，但未生成任何输出（无符合条件的数据）。"

        return (
            f"提取完成。batch_id={batch_id}\n"
            f"  有效项目:  {len(valid_rows)} 条 → {os.path.abspath(projects_db_path)}\n"
            f"  无中方参与: {len(no_chinese_rows)} 条 → {os.path.abspath(review_db_path)}\n"
            f"  被剔除新闻: {len(discarded_rows)} 条 → {os.path.abspath(review_db_path)}"
            + json_path_str
        )
