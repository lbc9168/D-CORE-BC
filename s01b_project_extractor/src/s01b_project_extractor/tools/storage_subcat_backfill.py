"""储能/光伏子项/子类回填 CLI（流水线常设阶段 2B-7 的执行体）。

三步固定顺序（步骤 1 先行，保证步骤 2 的 HB 判定能看到最新储能值）：
    1.  储能回填：全部未排除项目的 project_events，fill-if-null
        extract_storage(capacity_details) → storage_power_mw / storage_energy_mwh。
    1b. 光伏子项回填：仅综合能源(HB)项目事件（纯光伏项目 capacity_mw 即光伏容量，不重复），
        fill-if-null extract_pv_capacity(capacity_details) → pv_capacity_mw。
    2.  子类回填：category 有子类定义（enums.subcategory_parents）且未排除且 subcategory IS NULL
        的 canonical，规则判定命中才写（DB 只存有证据值；manual 写入的非空值永不覆盖）。

安全范式抄 tools/txmf_backfill.py：dry-run 默认 + mode=ro 只读；--apply 时 WAL 备份 +
三重断言 + busy_timeout=30s + 单事务 executemany + 幂等自检（apply 后 would_change 必须=0）。

用法：uv run --no-sync python -m s01b_project_extractor.tools.storage_subcat_backfill [--db PATH] [--apply] [--out CSV]
"""
from __future__ import annotations

import argparse
import csv
import logging
import sqlite3
from s01b_project_extractor.tools.field_validation import withheld_fields, record_repairs
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ValidationError, field_validator

from s01b_project_extractor.storage_parser import (
    extract_pv_capacity,
    mentions_pv,
)
from s01b_project_extractor.energy_taxonomy import extract_energy_facts
from s01b_project_extractor.subcategory_rules import classify_pv

import bri_enums as enums

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_DB = str(_PROJECT_ROOT / "s02a_duplicate_merger" / "data" / "db" / "bri_re_database.db")
_SUPPORTING_DOCS = _PROJECT_ROOT / "supporting_docs"

_ES_CATEGORY = "储能"
_HB_CATEGORY = "综合能源"
_PV_CATEGORY = "光伏"
_EVENT_COLUMNS = ("storage_power_mw", "storage_energy_mwh", "pv_capacity_mw")
_REQUIRED_EVENT_COLUMNS = _EVENT_COLUMNS
_REQUIRED_CANONICAL_COLUMNS = ("subcategory",)


class RunParams(BaseModel):
    """run() 入口参数校验：db_path 必须存在，apply 默认 False。"""

    db_path: str
    apply: bool = False

    @field_validator("db_path")
    @classmethod
    def _db_must_exist(cls, v: str) -> str:
        """数据库文件必须存在。"""
        if not Path(v).exists():
            raise ValueError(f"数据库文件不存在：{v}")
        return v


def _validate_db(db_path: str) -> None:
    """校验库文件存在、两表新列齐全、canonical_projects 非空；否则抛 RuntimeError。"""
    if not Path(db_path).exists():
        raise RuntimeError(f"数据库文件不存在：{db_path}")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        ev_cols = {row[1] for row in conn.execute("PRAGMA table_info(project_events)")}
        missing = [c for c in _REQUIRED_EVENT_COLUMNS if c not in ev_cols]
        cp_cols = {row[1] for row in conn.execute("PRAGMA table_info(canonical_projects)")}
        missing += [f"canonical_projects.{c}" for c in _REQUIRED_CANONICAL_COLUMNS if c not in cp_cols]
        if missing:
            raise RuntimeError(
                f"缺列 {missing}（需先跑 scripts/migrate_add_subcat_amount_columns.py --apply）"
            )
        (n,) = conn.execute("SELECT COUNT(*) FROM canonical_projects").fetchone()
        if n == 0:
            raise RuntimeError("canonical_projects 为空，上一阶段输出无效")
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# 扫描：构建变更计划（只读，不写库）
# ══════════════════════════════════════════════════════════════════

def _scan(db_path: str) -> tuple[list[dict], list[dict], list[dict]]:
    """只读扫描，返回 (事件级变更计划, 子类变更计划, misses)。

    事件级每条：{event_id, 列: 值, ...}（仅含实际会写的列，fill-if-null）。
    子类每条：{canonical_id, subcategory}。
    misses 每条：{event_id/canonical_id, category, reason, capacity_details, summary}。
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        withheld = withheld_fields(conn)
        events = conn.execute(
            """
            SELECT e.id AS event_id, e.canonical_id, c.category,
                   e.capacity_details, e.project_summary, e.additional_remarks,
                   e.storage_power_mw, e.storage_energy_mwh, e.pv_capacity_mw
            FROM project_events e
            JOIN canonical_projects c ON c.id = e.canonical_id
            WHERE COALESCE(c.excluded, 0) = 0
            """
        ).fetchall()

        parents = enums.subcategory_parents()
        canonicals = conn.execute(
            f"""
            SELECT id, project_name, category, subcategory
            FROM canonical_projects
            WHERE category IN ({",".join("?" * len(parents))})
              AND COALESCE(excluded, 0) = 0
              AND subcategory IS NULL
            """,
            parents,
        ).fetchall() if parents else []
    finally:
        conn.close()

    event_plan: list[dict] = []
    misses: list[dict] = []
    # canonical → 事件文本（仅供光伏形态子类复用，避免二次查询）
    texts_by_canonical: dict[str, list[str]] = {}

    for r in events:
        details = r["capacity_details"]

        texts_by_canonical.setdefault(r["canonical_id"], []).extend(
            t for t in (details, r["project_summary"], r["additional_remarks"]) if t
        )
        change: dict[str, Any] = {"event_id": r["event_id"]}

        # 步骤 1：严格储能 fill-if-null（只有 confirmed 语境才返回容量）
        if r["storage_power_mw"] is None or r["storage_energy_mwh"] is None:
            combined = "；".join(
                t for t in (details, r["project_summary"], r["additional_remarks"]) if t
            )
            facts = extract_energy_facts(combined)
            pwr, eng = facts.storage_power_mw, facts.storage_energy_mwh
            if r["storage_power_mw"] is None and pwr is not None and "storage_power_mw" not in withheld.get(r["event_id"], set()):
                change["storage_power_mw"] = pwr
            if r["storage_energy_mwh"] is None and eng is not None and "storage_energy_mwh" not in withheld.get(r["event_id"], set()):
                change["storage_energy_mwh"] = eng
            if (pwr is None and eng is None and r["category"] == _ES_CATEGORY and (details or "").strip()
                  and r["storage_power_mw"] is None and r["storage_energy_mwh"] is None):
                misses.append({
                    "event_id": r["event_id"], "canonical_id": r["canonical_id"],
                    "category": r["category"], "reason": "es_no_storage_signal",
                    "capacity_details": (details or "")[:200],
                    "summary": (r["project_summary"] or "")[:200],
                })

        # 步骤 1b：光伏子项 fill-if-null（仅 HB）
        if r["category"] == _HB_CATEGORY and r["pv_capacity_mw"] is None and "pv_capacity_mw" not in withheld.get(r["event_id"], set()):
            pv = extract_pv_capacity(details)
            if pv is not None:
                change["pv_capacity_mw"] = pv
            elif mentions_pv(details):
                misses.append({
                    "event_id": r["event_id"], "canonical_id": r["canonical_id"],
                    "category": r["category"], "reason": "hb_no_pv_capacity",
                    "capacity_details": (details or "")[:200],
                    "summary": (r["project_summary"] or "")[:200],
                })

        if len(change) > 1:
            event_plan.append(change)

    # 步骤 2：子类判定（只对 subcategory IS NULL 的 canonical，命中才写）
    subcat_plan: list[dict] = []
    for c in canonicals:
        texts = [c["project_name"] or ""] + texts_by_canonical.get(c["id"], [])
        if c["category"] == _PV_CATEGORY:
            sub = classify_pv(texts)
        else:
            sub = None  # 综合能源组合标签已迁入 shadow taxonomy，不再写 subcategory=光储
        if sub is not None:
            if sub not in enums.subcategories(c["category"]):
                raise ValueError(f"非法子类 {sub!r}（父类 {c['category']}，合法值 {enums.subcategories(c['category'])}）")
            subcat_plan.append({"canonical_id": c["id"], "subcategory": sub})

    return event_plan, subcat_plan, misses


def _summarize(event_plan: list[dict], subcat_plan: list[dict]) -> dict[str, int]:
    """按列统计将被填充的行数。"""
    counts = {col: 0 for col in _EVENT_COLUMNS}
    for change in event_plan:
        for col in change:
            if col != "event_id":
                counts[col] += 1
    counts["subcategory"] = len(subcat_plan)
    return counts


# ══════════════════════════════════════════════════════════════════
# 备份（WAL 安全，抄 txmf_backfill.py）
# ══════════════════════════════════════════════════════════════════

def _backup_db(db_path: Path) -> Path:
    """WAL 安全备份（sqlite3 backup API）+ 三重断言（存在/非0字节/canonical 行数一致）。"""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dst = db_path.with_name(f"{db_path.name}.bak.{timestamp}")

    src_conn = sqlite3.connect(str(db_path))
    dst_conn = sqlite3.connect(str(dst))
    try:
        src_conn.backup(dst_conn)
    finally:
        src_conn.close()
        dst_conn.close()

    if not dst.exists():
        raise RuntimeError(f"备份文件不存在（断言1 失败）：{dst}")
    if dst.stat().st_size == 0:
        raise RuntimeError(f"备份文件为 0 字节（断言2 失败）：{dst}")
    with sqlite3.connect(str(db_path)) as s, sqlite3.connect(str(dst)) as d:
        (src_count,) = s.execute("SELECT COUNT(*) FROM canonical_projects").fetchone()
        (dst_count,) = d.execute("SELECT COUNT(*) FROM canonical_projects").fetchone()
    if src_count != dst_count:
        raise RuntimeError(
            f"备份 canonical_projects 行数不一致（断言3 失败）：源={src_count}，备份={dst_count}"
        )

    logger.info("备份完成：%s（canonical_projects=%d 行）", dst, src_count)
    return dst


# ══════════════════════════════════════════════════════════════════
# 落库（单事务 executemany）
# ══════════════════════════════════════════════════════════════════

def _apply_plan(db_path: str, event_plan: list[dict], subcat_plan: list[dict]) -> None:
    """单事务落库：事件级按列组合分组 executemany + 子类 executemany（规则3）。"""
    if not event_plan and not subcat_plan:
        return

    groups: dict[tuple, list[dict]] = {}
    for change in event_plan:
        cols = tuple(c for c in change if c != "event_id")
        groups.setdefault(cols, []).append(change)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        with conn:
            for cols, changes in groups.items():
                set_clause = ", ".join(f"{c}=?" for c in cols)
                sql = f"UPDATE project_events SET {set_clause} WHERE id=?"
                params = [tuple(ch[c] for c in cols) + (ch["event_id"],) for ch in changes]
                conn.executemany(sql, params)
            record_repairs(conn, event_plan, "storage_subcat_backfill")
            if subcat_plan:
                # 双保险：只更新仍为 NULL 的行（manual 值绝不覆盖）
                conn.executemany(
                    "UPDATE canonical_projects SET subcategory=? WHERE id=? AND subcategory IS NULL",
                    [(ch["subcategory"], ch["canonical_id"]) for ch in subcat_plan],
                )
        logger.info("已落库：事件级 %d 条（%d 个列组合）+ 子类 %d 条",
                    len(event_plan), len(groups), len(subcat_plan))
    finally:
        conn.close()


def _write_misses_csv(misses: list[dict], out_csv: str) -> None:
    """写 misses CSV（utf-8-sig，Excel 中文兼容）；空 misses 也写表头方便脚本消费。"""
    fieldnames = ["event_id", "canonical_id", "category", "reason", "capacity_details", "summary"]
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(misses)
    logger.info("misses 清单已写 %s（%d 行）", out_csv, len(misses))


# ══════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════

def run(db_path: str, apply: bool = False, out_csv: Optional[str] = None) -> dict:
    """执行储能/光伏子项/子类回填主流程：入口校验 → 扫描 → (apply: 备份+落库+幂等自检) → misses CSV。

    返回摘要统计 dict：{"would_change": {列: 行数}, "misses": misses条数, "applied": bool}。
    """
    try:
        RunParams(db_path=db_path, apply=apply)
    except ValidationError as e:
        raise ValueError(str(e)) from e

    _validate_db(db_path)

    event_plan, subcat_plan, misses = _scan(db_path)
    summary = _summarize(event_plan, subcat_plan)

    if apply:
        if event_plan or subcat_plan:
            _backup_db(Path(db_path))
            _apply_plan(db_path, event_plan, subcat_plan)

        # 幂等自检：apply 后重算 would_change 必须全为 0
        re_events, re_subcat, _ = _scan(db_path)
        resummary = _summarize(re_events, re_subcat)
        if any(v != 0 for v in resummary.values()):
            raise RuntimeError(f"幂等自检失败：apply 后仍有待变更 {resummary}")
        logger.info("幂等自检通过：apply 后 would_change 全为 0")

    if out_csv:
        _write_misses_csv(misses, out_csv)

    logger.info("储能/子类回填%s：would_change=%s，misses=%d",
                "已落库" if apply else "dry-run", summary, len(misses))

    return {"would_change": summary, "misses": len(misses), "applied": apply}


# ══════════════════════════════════════════════════════════════════
# CLI 入口
# ══════════════════════════════════════════════════════════════════

def _setup_logging(db_path: str) -> None:
    """初始化 logging：console + 落文件（CLAUDE.md 规则2）。"""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_path = Path(db_path).resolve().parent / f"storage_subcat_backfill_{ts}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()],
        force=True,
    )
    logger.info("日志文件：%s", log_path)


def main() -> None:
    """CLI 主入口：解析参数、跑 dry-run 或 apply，落 misses CSV。"""
    parser = argparse.ArgumentParser(description="储能/光伏子项/子类存量回填")
    parser.add_argument("--db", default=_DEFAULT_DB, help="目标数据库路径")
    parser.add_argument("--apply", action="store_true", help="落库（默认 dry-run）")
    parser.add_argument("--out", default=None, help="misses CSV 输出路径")
    args = parser.parse_args()

    _setup_logging(args.db)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_csv = args.out or str(_SUPPORTING_DOCS / f"storage_subcat_backfill_misses_{ts}.csv")

    try:
        report = run(args.db, apply=args.apply, out_csv=out_csv)
    except (RuntimeError, ValueError) as e:
        logger.error("storage_subcat_backfill 失败：%s", e)
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    print(f">>> would_change={report['would_change']}，misses={report['misses']}，"
          f"{'已落库' if report['applied'] else 'dry-run，未写库'}，misses CSV: {out_csv}")


if __name__ == "__main__":
    main()
