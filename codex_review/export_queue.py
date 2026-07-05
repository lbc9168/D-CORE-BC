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
from codex_review.ledger import is_settled, load_ledger, t1_key, t3_key
from codex_review.schemas import (
    EventContext,
    ProjectContext,
    T1QueueFile,
    T1QueueItem,
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
_DEFAULT_REGISTRY = _WORKTREE_ROOT / "supporting_docs" / "company_hierarchy_v2.json"
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
# T2 占位（后续任务实现）
# ──────────────────────────────────────────────

_NOT_IMPL = {"T2": "T2 范围复核尚未实现"}


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
    # T1 参数
    p.add_argument(
        "--db",
        default=str(_DEFAULT_DB),
        help="主库 SQLite 路径（T1 专用；默认主工作区主库）",
    )
    p.add_argument(
        "--threshold",
        type=int,
        default=85,
        help="T1 名称相似度阈值（整数，score > threshold 才入队，默认 85）",
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
        help="集团层级 JSON（company_hierarchy_v2.json）",
    )
    p.add_argument("--out-dir", default=str(_DEFAULT_OUT_DIR), help="输出根目录")
    p.add_argument("--ledger", default=str(_DEFAULT_LEDGER), help="台账 JSON 路径")
    return p


def main() -> None:
    """CLI 主入口：解析参数并 dispatch 到对应任务。"""
    parser = _build_parser()
    args = parser.parse_args()

    _setup_logging(args.task)

    # dispatch 表：T2 暂未实现，T1/T3 已实现
    _dispatch: dict[str, Any] = {
        "T1": lambda: export_t1(
            quarter=args.quarter,
            db=Path(args.db),
            threshold=args.threshold,
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

    if args.task in _NOT_IMPL:
        print(f"[ERROR] {_NOT_IMPL[args.task]}，请等待后续任务实现。", file=sys.stderr)
        sys.exit(1)

    try:
        _dispatch[args.task]()
    except (ValueError, FileNotFoundError) as e:
        logger.error("导出失败：%s", e)
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
