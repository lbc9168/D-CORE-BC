"""Codex 季度分拣队列导出 CLI：从各数据源生成 queue_T*.json + guide_T*.md。

用法：
    uv run python -m codex_review.export_queue --task T3 --quarter 2026Q3 [选项]
    uv run python -m codex_review.export_queue --task T1 --quarter 2026Q3 [选项]
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import logging
import re
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, field_validator

import bri_enums
from codex_review.ledger import is_settled, load_ledger, t1_key, t2_key, t3_key
from codex_review.schemas import (
    EventContext,
    ProjectContext,
    T1QueueFile,
    T1QueueItem,
    T2QueueFile,
    T2QueueItem,
    T3QueueFile,
    T3QueueItem,
)

# ──────────────────────────────────────────────
# 常量与默认路径
# ──────────────────────────────────────────────

_MODULE_DIR = Path(__file__).resolve().parent
_WORKTREE_ROOT = _MODULE_DIR.parent

_DEFAULT_MISSES_CSV = (
    _WORKTREE_ROOT / "supporting_docs" / "entity_reparse_misses_20260706_023835.csv"
)
# 主工作区审计清单，worktree 无副本（T2 固定清单来源①）
_DEFAULT_AUDIT_CSV = Path(
    r"E:\Projects\BRI_RE_Database_v2\supporting_docs\numeric_audit_2026-07-04.csv"
)
# T2 MF 边界扫描关键词（来自项目计划原文，不自行扩充）
_MF_KEYWORDS = frozenset(["锂矿", "电解铝", "蓝氨"])
_DEFAULT_REGISTRY = _WORKTREE_ROOT / "supporting_docs" / "company_hierarchy_v4.json"
_DEFAULT_OUT_DIR = _MODULE_DIR / "runs"
_DEFAULT_LEDGER = _MODULE_DIR / "ledger.json"
_GUIDES_DIR = _MODULE_DIR / "guides"
_LOGS_DIR = _MODULE_DIR / "logs"

# 主库在主工作区，worktree 无数据副本，见 plan §2.4
_DEFAULT_DB = Path(
    r"E:\Projects\BRI_RE_Database_v2\s02a_duplicate_merger\data\db\bri_re_database.db"
)

# country_iso 排除集合（近似 s03 passes_baseline 的大陆港澳台判定）
_EXCLUDE_ISO = frozenset(["CHN", "HKG", "MAC", "TWN"])

_QUARTER_RE = re.compile(r"^20\d{2}Q[1-4]$")

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 入口参数校验模型（项目规则 4）
# ──────────────────────────────────────────────


class T1ExportParams(BaseModel):
    """T1 导出的入口参数，含格式与路径存在性校验。"""

    quarter: str
    db: Path
    threshold: int
    out_dir: Path
    ledger_path: Path

    @field_validator("quarter")
    @classmethod
    def _check_quarter(cls, v: str) -> str:
        """校验 quarter 格式为 20xxQ[1-4]。"""
        if not _QUARTER_RE.match(v):
            raise ValueError(f"quarter 格式非法，期望 20xxQ[1-4]，收到 {v!r}")
        return v

    @field_validator("db")
    @classmethod
    def _check_db(cls, v: Path) -> Path:
        """校验 db 路径存在。"""
        if not v.exists():
            raise ValueError(f"db 路径不存在：{v}")
        return v


class T3ExportParams(BaseModel):
    """T3 导出的入口参数，含格式与路径存在性校验。"""

    quarter: str
    misses_csv: Path
    registry: Path
    out_dir: Path
    ledger_path: Path

    @field_validator("quarter")
    @classmethod
    def _check_quarter(cls, v: str) -> str:
        """校验 quarter 格式为 20xxQ[1-4]。"""
        if not _QUARTER_RE.match(v):
            raise ValueError(f"quarter 格式非法，期望 20xxQ[1-4]，收到 {v!r}")
        return v

    @field_validator("misses_csv")
    @classmethod
    def _check_misses_csv(cls, v: Path) -> Path:
        """校验 misses_csv 路径存在。"""
        if not v.exists():
            raise ValueError(f"misses_csv 路径不存在：{v}")
        return v

    @field_validator("registry")
    @classmethod
    def _check_registry(cls, v: Path) -> Path:
        """校验 registry JSON 路径存在。"""
        if not v.exists():
            raise ValueError(f"registry 路径不存在：{v}")
        return v


class T2ExportParams(BaseModel):
    """T2 导出的入口参数，含格式与路径存在性校验。"""

    quarter: str
    db: Path
    audit_csv: Path
    extra_ids: list[str]
    out_dir: Path
    ledger_path: Path

    @field_validator("quarter")
    @classmethod
    def _check_quarter(cls, v: str) -> str:
        """校验 quarter 格式为 20xxQ[1-4]。"""
        if not _QUARTER_RE.match(v):
            raise ValueError(f"quarter 格式非法，期望 20xxQ[1-4]，收到 {v!r}")
        return v

    @field_validator("db")
    @classmethod
    def _check_db(cls, v: Path) -> Path:
        """校验 db 路径存在。"""
        if not v.exists():
            raise ValueError(f"db 路径不存在：{v}")
        return v

    @field_validator("audit_csv")
    @classmethod
    def _check_audit_csv(cls, v: Path) -> Path:
        """校验 audit_csv 路径存在。"""
        if not v.exists():
            raise ValueError(f"audit_csv 路径不存在：{v}")
        return v


# ──────────────────────────────────────────────
# T1 辅助函数
# ──────────────────────────────────────────────


def _score(a: dict, b: dict) -> float:
    """计算两个 canonical 行的名称相似度（token_sort_ratio，当双方 en 均非空时取中英文最大值）。"""
    from rapidfuzz import fuzz

    score = fuzz.token_sort_ratio(a["project_name"], b["project_name"])
    a_en = (a.get("project_name_en") or "").strip()
    b_en = (b.get("project_name_en") or "").strip()
    if a_en and b_en:
        score = max(score, fuzz.token_sort_ratio(a_en, b_en))
    return score


def _is_visible(row: dict, events: list[dict]) -> bool:
    """判断单侧项目是否"可见"：近似 s03 passes_baseline，只影响 both_sides_visible 排序，非权威。"""
    # country_iso 不在大陆港澳台
    if (row.get("country_iso") or "") in _EXCLUDE_ISO:
        return False
    cat = row.get("category") or ""
    if bri_enums.is_hidden_category(cat):
        return False
    if bri_enums.is_excluded_category(cat):
        return False
    # 存在 publish_date 年份 >=2022 的事件
    has_recent = any(
        ev.get("publish_date") and ev["publish_date"][:4] >= "2022"
        for ev in events
    )
    if not has_recent:
        return False
    # 存在非 UNNAMED_ENTITY_PLACEHOLDER 的实体名
    entity_names = row.get("_entity_names", [])
    has_named = any(n != bri_enums.UNNAMED_ENTITY_PLACEHOLDER for n in entity_names)
    return has_named


def _load_projects(conn: sqlite3.Connection) -> list[dict]:
    """从 canonical_projects 加载 excluded=0 的行，返回含 id/project_name/project_name_en/country/country_iso/category 的字典列表。"""
    cur = conn.execute(
        "SELECT id, project_name, project_name_en, country, country_iso, category "
        "FROM canonical_projects WHERE excluded=0"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _load_events(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """从 project_events 加载全部行，返回以 canonical_id 为键的事件列表字典。"""
    cur = conn.execute(
        "SELECT id, canonical_id, project_stage, capacity_mw, capacity_mw_raw, "
        "publish_date, project_summary, source_links FROM project_events"
    )
    cols = [d[0] for d in cur.description]
    events_by_cid: dict[str, list[dict]] = {}
    for row in cur.fetchall():
        ev = dict(zip(cols, row))
        events_by_cid.setdefault(ev["canonical_id"], []).append(ev)
    return events_by_cid


def _load_entity_names(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """从 chinese_entities 加载全部行，返回以 canonical_id 为键的实体名去重列表。"""
    cur = conn.execute(
        "SELECT ce.event_id, ce.implementing_entity, ce.subsidiary_name, "
        "pe.canonical_id "
        "FROM chinese_entities ce "
        "JOIN project_events pe ON ce.event_id = pe.id"
    )
    names_by_cid: dict[str, set[str]] = {}
    for row in cur.fetchall():
        _, impl, sub, cid = row
        s = names_by_cid.setdefault(cid, set())
        if impl and impl.strip():
            s.add(impl.strip())
        if sub and sub.strip():
            s.add(sub.strip())
    return {k: list(v) for k, v in names_by_cid.items()}


def _build_event_context(ev: dict) -> EventContext:
    """将数据库 event 行转为 EventContext，source_links 解析失败按空列表并 log warning。"""
    raw_links = ev.get("source_links")
    links: list[str] = []
    if raw_links:
        try:
            parsed = json.loads(raw_links)
            if isinstance(parsed, list):
                links = [str(x) for x in parsed if x]
            else:
                logger.warning("source_links 非列表，事件 id=%s，值=%r", ev.get("id"), raw_links)
        except json.JSONDecodeError:
            logger.warning("source_links JSON 解析失败，事件 id=%s，值=%r", ev.get("id"), raw_links)
    return EventContext(
        event_id=ev["id"],
        project_stage=ev.get("project_stage") or "",
        capacity_mw=ev.get("capacity_mw"),
        capacity_mw_raw=ev.get("capacity_mw_raw"),
        publish_date=ev.get("publish_date"),
        project_summary=ev.get("project_summary"),
        source_links=links,
    )


def _build_project_context(row: dict, events: list[dict], entity_names: list[str]) -> ProjectContext:
    """将数据库 canonical 行、事件列表、实体名列表组装为 ProjectContext。"""
    return ProjectContext(
        canonical_id=row["id"],
        project_name=row["project_name"],
        project_name_en=row.get("project_name_en"),
        country=row.get("country") or "",
        country_iso=row.get("country_iso"),
        category=row.get("category") or "",
        events=[_build_event_context(ev) for ev in events],
        entity_names=entity_names,
    )


# ──────────────────────────────────────────────
# registry 覆盖集合构建
# ──────────────────────────────────────────────


def _build_covered_set(groups: list[dict]) -> set[str]:
    """从 registry groups 列表构建所有已覆盖名称的精确匹配集合（strip 后）。"""
    covered: set[str] = set()
    for g in groups:
        name_cn = g.get("canonical_name_cn", "")
        if name_cn:
            covered.add(name_cn.strip())
        for alias in g.get("aliases", []):
            if alias:
                covered.add(alias.strip())
        for sub in g.get("subsidiaries", []):
            for field in ("name_cn", "legal_full_name_cn"):
                val = sub.get(field, "")
                if val:
                    covered.add(val.strip())
    return covered


# ──────────────────────────────────────────────
# 日志初始化
# ──────────────────────────────────────────────


def _setup_logging(task: str) -> None:
    """初始化 logging：同时输出 console 和带时间戳的日志文件。"""
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = _LOGS_DIR / f"export_{task}_{timestamp}.log"

    fmt = "%(asctime)s [%(name)s] %(levelname)s %(message)s"
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers, force=True)
    logger.info("日志文件：%s", log_file)


# ──────────────────────────────────────────────
# T1 核心导出逻辑
# ──────────────────────────────────────────────


def export_t1(
    *,
    quarter: str,
    db: Path,
    threshold: int = 85,
    out_dir: Path,
    ledger_path: Path,
) -> None:
    """导出 T1 漏合并候选对队列到 <out_dir>/<quarter>/queue_T1.json + guide_T1.md。"""
    # 入口参数校验（项目规则 4）
    params = T1ExportParams(
        quarter=quarter,
        db=Path(db),
        threshold=threshold,
        out_dir=Path(out_dir),
        ledger_path=Path(ledger_path),
    )

    # 加载台账（只读，不写）
    ledger = load_ledger(params.ledger_path)

    # 只读打开库（绝不写库）
    conn = sqlite3.connect(f"file:{params.db}?mode=ro", uri=True)
    try:
        projects = _load_projects(conn)
        events_by_cid = _load_events(conn)
        entity_names_by_cid = _load_entity_names(conn)
    finally:
        conn.close()

    logger.info("加载 canonical_projects（excluded=0）：%d 行", len(projects))

    # 过滤名称为空的行并统计
    valid_projects = [p for p in projects if p.get("project_name") and p["project_name"].strip()]
    skipped_no_name = len(projects) - len(valid_projects)
    if skipped_no_name:
        logger.info("跳过名称为空的行：%d 条", skipped_no_name)

    # 按 (country_iso, category) 分组
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for p in valid_projects:
        key = (p.get("country_iso") or "", p.get("category") or "")
        groups[key].append(p)

    # 两两比对，收集候选对
    items: list[T1QueueItem] = []
    skipped_settled = 0
    skipped_below_threshold = 0

    for (iso, cat), members in groups.items():
        if len(members) < 2:
            continue
        for a, b in itertools.combinations(members, 2):
            score = _score(a, b)
            if score <= threshold:
                skipped_below_threshold += 1
                continue
            # 组装 pair_id
            a_ctx = _build_project_context(
                a,
                events_by_cid.get(a["id"], []),
                entity_names_by_cid.get(a["id"], []),
            )
            b_ctx = _build_project_context(
                b,
                events_by_cid.get(b["id"], []),
                entity_names_by_cid.get(b["id"], []),
            )
            pair_id = t1_key(a_ctx, b_ctx)
            if is_settled(ledger, pair_id):
                skipped_settled += 1
                logger.debug("台账已定案，跳过：%s", pair_id)
                continue

            # both_sides_visible：近似 passes_baseline，只影响排序非入队
            a["_entity_names"] = a_ctx.entity_names
            b["_entity_names"] = b_ctx.entity_names
            vis_a = _is_visible(a, events_by_cid.get(a["id"], []))
            vis_b = _is_visible(b, events_by_cid.get(b["id"], []))

            items.append(
                T1QueueItem(
                    pair_id=pair_id,
                    similarity=score / 100.0,
                    both_sides_visible=vis_a and vis_b,
                    a=a_ctx,
                    b=b_ctx,
                )
            )

    # 排序：both_sides_visible 优先，再按 similarity 降序
    items.sort(key=lambda x: (not x.both_sides_visible, -x.similarity))

    logger.info(
        "统计：有效行 %d，低相似跳过 %d，台账跳过 %d，入队 %d 对",
        len(valid_projects),
        skipped_below_threshold,
        skipped_settled,
        len(items),
    )
    print(
        f"T1 导出：有效行 {len(valid_projects)}，"
        f"低相似跳过 {skipped_below_threshold}，"
        f"台账跳过 {skipped_settled}，"
        f"入队 {len(items)} 对"
    )

    # 组装 T1QueueFile 并 Pydantic 校验（先校验再落盘）
    queue_file = T1QueueFile(
        task="T1",
        quarter=params.quarter,
        generated_at=datetime.now(timezone.utc).isoformat(),
        source_db=str(params.db),
        items=items,
    )

    # 写出文件
    quarter_dir = Path(params.out_dir) / params.quarter
    quarter_dir.mkdir(parents=True, exist_ok=True)

    out_json = quarter_dir / "queue_T1.json"
    out_json.write_text(
        json.dumps(queue_file.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("队列文件已写出：%s", out_json)

    # 复制分拣指南
    guide_src = _GUIDES_DIR / "guide_T1.md"
    guide_dst = quarter_dir / "guide_T1.md"
    if guide_src.exists():
        shutil.copy2(guide_src, guide_dst)
        logger.info("分拣指南已复制：%s → %s", guide_src, guide_dst)
    else:
        logger.warning("分拣指南模板不存在，跳过复制：%s", guide_src)

    print(f"输出目录：{quarter_dir}")


# ──────────────────────────────────────────────
# T3 核心导出逻辑
# ──────────────────────────────────────────────


def export_t3(
    *,
    quarter: str,
    misses_csv: Path,
    registry: Path,
    out_dir: Path,
    ledger_path: Path,
) -> None:
    """导出 T3 实体归属补录队列到 <out_dir>/<quarter>/queue_T3.json + guide_T3.md。"""
    # 入口参数校验（项目规则 4）
    params = T3ExportParams(
        quarter=quarter,
        misses_csv=Path(misses_csv),
        registry=Path(registry),
        out_dir=Path(out_dir),
        ledger_path=Path(ledger_path),
    )

    # 加载台账（只读，不写）
    ledger = load_ledger(params.ledger_path)

    # 加载 registry，构建覆盖集合与 registry_groups 列表
    try:
        with open(params.registry, "r", encoding="utf-8") as f:
            reg_data: dict[str, Any] = json.load(f)
    except json.JSONDecodeError as e:
        logger.error("registry JSON 解析失败：%s", e)
        raise ValueError(f"registry JSON 解析失败：{params.registry}") from e

    groups: list[dict] = reg_data.get("groups", [])
    covered_set = _build_covered_set(groups)
    registry_groups = [g["canonical_name_cn"] for g in groups if g.get("canonical_name_cn")]
    logger.info("registry 加载完成：%d 组，覆盖名称 %d 个", len(groups), len(covered_set))

    # 读 misses CSV（utf-8-sig 容错 BOM）
    rows: list[tuple[str, int]] = []
    try:
        with open(params.misses_csv, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get("name") or "").strip()
                count_str = (row.get("count") or "").strip()
                if not name or not count_str:
                    continue
                try:
                    count = int(count_str)
                except ValueError as e:
                    logger.error("CSV count 解析失败，行=%r，错误=%s", row, e)
                    raise
                rows.append((name, count))
    except OSError as e:
        logger.error("读 misses CSV 失败：%s", e)
        raise

    logger.info("misses CSV 读入 %d 行", len(rows))

    # 逐行过滤：registry 覆盖 + 台账去重
    items: list[T3QueueItem] = []
    skipped_covered = 0
    skipped_settled = 0

    for name, count in rows:
        if name.strip() in covered_set:
            skipped_covered += 1
            logger.debug("registry 已覆盖，跳过：%s", name)
            continue
        key = t3_key(name)
        if is_settled(ledger, key):
            skipped_settled += 1
            logger.debug("台账已定案，跳过：%s", name)
            continue
        items.append(
            T3QueueItem(
                item_id=key,
                raw_name=name,
                freq=count,
                registry_groups=registry_groups,
            )
        )

    # freq 降序排序
    items.sort(key=lambda x: x.freq, reverse=True)

    logger.info(
        "统计：读入 %d 条，registry 已覆盖跳过 %d 条，台账已定案跳过 %d 条，入队 %d 条",
        len(rows),
        skipped_covered,
        skipped_settled,
        len(items),
    )
    print(
        f"T3 导出：读入 {len(rows)} 条，"
        f"registry 已覆盖跳过 {skipped_covered} 条，"
        f"台账已定案跳过 {skipped_settled} 条，"
        f"入队 {len(items)} 条"
    )

    # 组装 T3QueueFile 并 Pydantic 校验（先校验再落盘）
    queue_file = T3QueueFile(
        task="T3",
        quarter=params.quarter,
        generated_at=datetime.now(timezone.utc).isoformat(),
        items=items,
    )

    # 写出文件
    quarter_dir = Path(params.out_dir) / params.quarter
    quarter_dir.mkdir(parents=True, exist_ok=True)

    out_json = quarter_dir / "queue_T3.json"
    out_json.write_text(
        json.dumps(queue_file.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("队列文件已写出：%s", out_json)

    # 复制分拣指南
    guide_src = _GUIDES_DIR / "guide_T3.md"
    guide_dst = quarter_dir / "guide_T3.md"
    if guide_src.exists():
        shutil.copy2(guide_src, guide_dst)
        logger.info("分拣指南已复制：%s → %s", guide_src, guide_dst)
    else:
        logger.warning("分拣指南模板不存在，跳过复制：%s", guide_src)

    print(f"输出目录：{quarter_dir}")


# ──────────────────────────────────────────────
# T2 辅助函数
# ──────────────────────────────────────────────


def _load_fixed_list_ids(audit_csv: Path) -> dict[str, str]:
    """从审计 CSV 读取附带备注含"范围复核"的行，返回 project_id → 拼接备注的字典。"""
    result: dict[str, list[str]] = {}
    try:
        with open(audit_csv, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                note = (row.get("附带备注") or "").strip()
                if "范围复核" not in note:
                    continue
                pid = (row.get("project_id") or "").strip()
                if not pid:
                    continue
                result.setdefault(pid, []).append(note)
    except OSError as e:
        logger.error("读审计 CSV 失败：%s", e)
        raise
    # 同一 project_id 多行备注拼接
    return {pid: "；".join(notes) for pid, notes in result.items()}


def _find_no_entity_ids(
    projects: list[dict],
    entity_names_by_cid: dict[str, list[str]],
) -> set[str]:
    """扫描 excluded=0 的项目，返回没有任何具名中企的 project_id 集合。"""
    no_entity: set[str] = set()
    for p in projects:
        pid = p["id"]
        names = entity_names_by_cid.get(pid, [])
        # 全部为空或占位符 → 视为无具名中企
        has_named = any(
            n and n.strip() and n.strip() != bri_enums.UNNAMED_ENTITY_PLACEHOLDER
            for n in names
        )
        if not has_named:
            no_entity.add(pid)
    return no_entity


def _find_mf_ids(
    projects: list[dict],
    events_by_cid: dict[str, list[dict]],
) -> dict[str, str]:
    """扫描 excluded=0 的项目，返回 project_id → 命中关键词 的字典（仅含命中项）。"""
    mf_hits: dict[str, str] = {}
    for p in projects:
        pid = p["id"]
        name = p.get("project_name") or ""
        # 先在 project_name 中查找
        for kw in _MF_KEYWORDS:
            if kw in name:
                mf_hits[pid] = kw
                break
        if pid in mf_hits:
            continue
        # 再在各事件 project_summary 中查找
        for ev in events_by_cid.get(pid, []):
            summary = ev.get("project_summary") or ""
            for kw in _MF_KEYWORDS:
                if kw in summary:
                    mf_hits[pid] = kw
                    break
            if pid in mf_hits:
                break
    return mf_hits


# ──────────────────────────────────────────────
# T2 核心导出逻辑
# ──────────────────────────────────────────────

# 触发来源优先级（值越小优先级越高）
_T2_PRIORITY: dict[str, int] = {
    "fixed_list": 0,
    "mf_boundary_scan": 1,
    "no_chinese_entity_scan": 2,
}


def export_t2(
    *,
    quarter: str,
    db: Path,
    audit_csv: Path,
    extra_ids: list[str],
    out_dir: Path,
    ledger_path: Path,
) -> None:
    """导出 T2 范围复核队列到 <out_dir>/<quarter>/queue_T2.json + guide_T2.md。"""
    # 入口参数校验（项目规则 4）
    params = T2ExportParams(
        quarter=quarter,
        db=Path(db),
        audit_csv=Path(audit_csv),
        extra_ids=list(extra_ids),
        out_dir=Path(out_dir),
        ledger_path=Path(ledger_path),
    )

    # 加载台账（只读，不写）
    ledger = load_ledger(params.ledger_path)

    # 只读打开库（绝不写库）
    conn = sqlite3.connect(f"file:{params.db}?mode=ro", uri=True)
    try:
        # 加载全部 excluded=0 的项目（_load_projects 只拉 excluded=0）
        projects = _load_projects(conn)
        events_by_cid = _load_events(conn)
        entity_names_by_cid = _load_entity_names(conn)
        # 同时加载 excluded 状态供固定清单校验
        cur = conn.execute("SELECT id, excluded FROM canonical_projects")
        excluded_map: dict[str, int] = {row[0]: row[1] for row in cur.fetchall()}
    finally:
        conn.close()

    logger.info("加载 canonical_projects（excluded=0）：%d 行", len(projects))

    # 构建活跃 project 集合（excluded=0）
    active_ids: set[str] = {p["id"] for p in projects}

    # ── 来源① 固定清单 ──
    fixed_notes = _load_fixed_list_ids(params.audit_csv)
    # extra_ids 追加（audit_note 为空串，供后续拼接）
    for pid in params.extra_ids:
        fixed_notes.setdefault(pid, "")

    skipped_excluded_fixed = 0
    skipped_notfound_fixed = 0

    # project_id → {trigger, note} 候选集合
    candidates: dict[str, dict[str, str]] = {}

    for pid, note in fixed_notes.items():
        exc = excluded_map.get(pid)
        if exc is None:
            logger.warning("固定清单 project_id 在库中查无此 id，跳过：%s", pid)
            skipped_notfound_fixed += 1
            continue
        if exc == 1:
            logger.info("固定清单 project_id 已排除（excluded=1），跳过：%s", pid)
            skipped_excluded_fixed += 1
            continue
        candidates[pid] = {"trigger": "fixed_list", "note": note}

    # ── 来源② 无中企扫描 ──
    no_entity_ids = _find_no_entity_ids(projects, entity_names_by_cid)
    for pid in no_entity_ids:
        if pid in candidates:
            # 已有更高或相等优先级来源，只拼接 note
            existing = candidates[pid]
            if _T2_PRIORITY["no_chinese_entity_scan"] < _T2_PRIORITY[existing["trigger"]]:
                existing["trigger"] = "no_chinese_entity_scan"
            existing["note"] = (
                existing["note"] + "；无中企扫描：全部事件无具名中企实体"
                if existing["note"]
                else "无中企扫描：全部事件无具名中企实体"
            )
        else:
            candidates[pid] = {
                "trigger": "no_chinese_entity_scan",
                "note": "无中企扫描：全部事件无具名中企实体",
            }

    # ── 来源③ MF 边界扫描 ──
    mf_hits = _find_mf_ids(projects, events_by_cid)
    for pid, kw in mf_hits.items():
        mf_note = f"MF 边界关键词：{kw}"
        if pid in candidates:
            existing = candidates[pid]
            if _T2_PRIORITY["mf_boundary_scan"] < _T2_PRIORITY[existing["trigger"]]:
                existing["trigger"] = "mf_boundary_scan"
            existing["note"] = (
                existing["note"] + "；" + mf_note if existing["note"] else mf_note
            )
        else:
            candidates[pid] = {"trigger": "mf_boundary_scan", "note": mf_note}

    # ── 组装 QueueItem，台账去重 ──
    items_fixed: list[T2QueueItem] = []
    items_rest: list[T2QueueItem] = []
    skipped_settled = 0

    # 构建 project_id → project 行的查找表
    project_row_map: dict[str, dict] = {p["id"]: p for p in projects}

    for pid, cand in candidates.items():
        trigger = cand["trigger"]
        item_id = t2_key(pid, trigger)
        if is_settled(ledger, item_id):
            skipped_settled += 1
            logger.debug("台账已定案，跳过：%s (trigger=%s)", pid, trigger)
            continue

        row = project_row_map.get(pid)
        if row is None:
            # 固定清单里指向 excluded 或不存在的 id，已在上面处理，此处理论上不到达
            logger.warning("候选 project_id 在活跃项目中查无，跳过：%s", pid)
            continue

        project_ctx = _build_project_context(
            row,
            events_by_cid.get(pid, []),
            entity_names_by_cid.get(pid, []),
        )
        item = T2QueueItem(
            item_id=item_id,
            project_id=pid,
            trigger=trigger,
            audit_note=cand["note"] or None,
            project=project_ctx,
        )
        if trigger == "fixed_list":
            items_fixed.append(item)
        else:
            items_rest.append(item)

    # 排序：fixed_list 在前，其余按 project_id
    items_fixed.sort(key=lambda x: x.project_id)
    items_rest.sort(key=lambda x: x.project_id)
    items = items_fixed + items_rest

    logger.info(
        "统计：固定清单 %d 条（已排除跳 %d，查无跳 %d），台账已定案跳 %d，入队 %d 条",
        len(fixed_notes),
        skipped_excluded_fixed,
        skipped_notfound_fixed,
        skipped_settled,
        len(items),
    )
    print(
        f"T2 导出：固定清单 {len(fixed_notes)} 条，"
        f"已排除跳过 {skipped_excluded_fixed}，查无跳过 {skipped_notfound_fixed}，"
        f"台账跳过 {skipped_settled}，入队 {len(items)} 条"
    )

    # 组装 T2QueueFile 并 Pydantic 校验（先校验再落盘）
    queue_file = T2QueueFile(
        task="T2",
        quarter=params.quarter,
        generated_at=datetime.now(timezone.utc).isoformat(),
        source_db=str(params.db),
        items=items,
    )

    # 写出文件
    quarter_dir = Path(params.out_dir) / params.quarter
    quarter_dir.mkdir(parents=True, exist_ok=True)

    out_json = quarter_dir / "queue_T2.json"
    out_json.write_text(
        json.dumps(queue_file.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("队列文件已写出：%s", out_json)

    # 复制分拣指南
    guide_src = _GUIDES_DIR / "guide_T2.md"
    guide_dst = quarter_dir / "guide_T2.md"
    if guide_src.exists():
        shutil.copy2(guide_src, guide_dst)
        logger.info("分拣指南已复制：%s → %s", guide_src, guide_dst)
    else:
        logger.warning("分拣指南模板不存在，跳过复制：%s", guide_src)

    print(f"输出目录：{quarter_dir}")


# ──────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    """构建 argparse 解析器。"""
    p = argparse.ArgumentParser(
        description="Codex 季度分拣队列导出工具",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--task", choices=["T1", "T2", "T3"], required=True, help="任务类型")
    p.add_argument("--quarter", required=True, help="季度，格式 20xxQ[1-4]，例如 2026Q3")
    # T1/T2 共用
    p.add_argument(
        "--db",
        default=str(_DEFAULT_DB),
        help="主库 SQLite 路径（T1/T2；默认主工作区主库）",
    )
    p.add_argument(
        "--threshold",
        type=int,
        default=85,
        help="T1 名称相似度阈值（整数，score > threshold 才入队，默认 85）",
    )
    # T2 参数
    p.add_argument(
        "--audit-csv",
        default=str(_DEFAULT_AUDIT_CSV),
        help="T2 审计清单 CSV（utf-8-sig；默认主工作区 numeric_audit_2026-07-04.csv）",
    )
    p.add_argument(
        "--extra-ids",
        default="",
        help="T2 额外追加的 project_id，逗号分隔（例如 ISR-HB-0001,EGY-PV-0002）",
    )
    # T3 参数
    p.add_argument(
        "--misses-csv",
        default=str(_DEFAULT_MISSES_CSV),
        help="实体解析未命中 CSV（列 name,count）",
    )
    p.add_argument(
        "--registry",
        default=str(_DEFAULT_REGISTRY),
        help="集团层级 JSON（company_hierarchy_v4.json）",
    )
    p.add_argument("--out-dir", default=str(_DEFAULT_OUT_DIR), help="输出根目录")
    p.add_argument("--ledger", default=str(_DEFAULT_LEDGER), help="台账 JSON 路径")
    return p


def main() -> None:
    """CLI 主入口：解析参数并 dispatch 到对应任务。"""
    parser = _build_parser()
    args = parser.parse_args()

    _setup_logging(args.task)

    extra_ids = [x.strip() for x in args.extra_ids.split(",") if x.strip()] if args.extra_ids else []

    _dispatch: dict[str, Any] = {
        "T1": lambda: export_t1(
            quarter=args.quarter,
            db=Path(args.db),
            threshold=args.threshold,
            out_dir=Path(args.out_dir),
            ledger_path=Path(args.ledger),
        ),
        "T2": lambda: export_t2(
            quarter=args.quarter,
            db=Path(args.db),
            audit_csv=Path(args.audit_csv),
            extra_ids=extra_ids,
            out_dir=Path(args.out_dir),
            ledger_path=Path(args.ledger),
        ),
        "T3": lambda: export_t3(
            quarter=args.quarter,
            misses_csv=Path(args.misses_csv),
            registry=Path(args.registry),
            out_dir=Path(args.out_dir),
            ledger_path=Path(args.ledger),
        ),
    }

    try:
        _dispatch[args.task]()
    except (ValueError, FileNotFoundError) as e:
        logger.error("导出失败：%s", e)
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
