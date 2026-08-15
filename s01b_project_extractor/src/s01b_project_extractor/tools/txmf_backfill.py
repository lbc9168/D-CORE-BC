"""TX/MF 专用指标存量回填 CLI：用 txmf_spec_parser 确定性解析回填 3.1 加的 5 个稀疏列。

范围：未排除（COALESCE(excluded,0)=0）TX（输变电建设）/ MF（新能源制造业）canonical
的全部 project_events。写入策略 fill-if-null——只填目标列为 NULL 的，已有非空值绝不覆盖。

安全范式抄 codex_review/apply_actions.py：dry-run 默认 + mode=ro 只读连接；--apply 时
sqlite3 backup API WAL 安全备份 + 三重断言 + busy_timeout=30s + 单事务 executemany +
幂等自检（apply 后重算 would_change 必须=0）。

用法：uv run --no-sync python -m s01b_project_extractor.tools.txmf_backfill [--db PATH] [--apply] [--out CSV]
"""
from __future__ import annotations

import argparse
import csv
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ValidationError, field_validator

from s01b_project_extractor.txmf_spec_parser import (
    parse_mf_atomic,
    parse_mf_event,
    parse_tx_atomic,
    parse_tx_event,
)

import bri_enums as enums

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_DB = str(_PROJECT_ROOT / "s02a_duplicate_merger" / "data" / "db" / "bri_re_database.db")
_SUPPORTING_DOCS = _PROJECT_ROOT / "supporting_docs"

_TX_CATEGORY = "输变电建设"
_MF_CATEGORY = "新能源制造业"
_REQUIRED_COLUMNS = (
    "voltage_kv", "line_length_km", "transmission_capacity_mw",
    "production_capacity_value", "production_capacity_unit",
)
_RAW_COLUMNS = (
    "voltage_kv_raw", "line_length_km_raw", "transmission_capacity_mw_raw",
    "production_capacity_raw",
)
_TX_COLUMNS = ("voltage_kv", "line_length_km", "transmission_capacity_mw")


# ══════════════════════════════════════════════════════════════════
# 入口参数校验（CLAUDE.md 规则4：外部入口用 Pydantic 校验，非法立即抛出）
# ══════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════
# 入口验证（CLAUDE.md 规则8：验证上一阶段输出，无效则抛带描述异常）
# ══════════════════════════════════════════════════════════════════

def _validate_db(db_path: str) -> None:
    """校验库文件存在、project_events 含 5 列、canonical_projects 非空；否则抛 RuntimeError。"""
    if not Path(db_path).exists():
        raise RuntimeError(f"数据库文件不存在：{db_path}")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(project_events)")}
        missing = [c for c in _REQUIRED_COLUMNS if c not in cols]
        if missing:
            raise RuntimeError(
                f"project_events 缺列 {missing}（需先跑 scripts/migrate_add_txmf_columns.py --apply）"
            )
        (n,) = conn.execute("SELECT COUNT(*) FROM canonical_projects").fetchone()
        if n == 0:
            raise RuntimeError("canonical_projects 为空，上一阶段输出无效")
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# 扫描：构建变更计划（只读，不写库）
# ══════════════════════════════════════════════════════════════════

def _scan_events(db_path: str) -> tuple[list[dict], list[dict]]:
    """只读扫描未排除 TX/MF 事件，解析后与现有值比对；返回 (变更计划, misses)。

    变更计划每条：{event_id, column: value, ...}（仅含实际会写的列，已有非空值不入内）。
    misses 每条：{event_id, canonical_id, category, reason, capacity_mw_raw,
                  capacity_details, summary_excerpt}。
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(project_events)")}
        raw_select = ", ".join(
            f"e.{column}" if column in existing else f"NULL AS {column}"
            for column in _RAW_COLUMNS
        )
        rows = conn.execute(
            f"""
            SELECT e.id AS event_id, e.canonical_id, c.category,
                   e.capacity_details, e.project_summary, e.capacity_mw_raw,
                   {raw_select},
                   e.voltage_kv, e.line_length_km, e.transmission_capacity_mw,
                   e.production_capacity_value, e.production_capacity_unit
            FROM project_events e
            JOIN canonical_projects c ON c.id = e.canonical_id
            WHERE c.category IN (?, ?) AND COALESCE(c.excluded, 0) = 0
            """,
            (_TX_CATEGORY, _MF_CATEGORY),
        ).fetchall()
    finally:
        conn.close()

    plan: list[dict] = []
    misses: list[dict] = []

    for r in rows:
        has_text = bool((r["capacity_details"] or "").strip() or (r["project_summary"] or "").strip())

        if r["category"] == _TX_CATEGORY:
            has_atomic = any((r[col] or "").strip() for col in (
                "voltage_kv_raw", "line_length_km_raw", "transmission_capacity_mw_raw"
            ))
            parsed = parse_tx_atomic(
                r["voltage_kv_raw"], r["line_length_km_raw"],
                r["transmission_capacity_mw_raw"],
            ) if has_atomic else parse_tx_event(
                r["capacity_details"], r["project_summary"], r["capacity_mw_raw"]
            )
            change: dict[str, Any] = {"event_id": r["event_id"]}
            for col in _TX_COLUMNS:
                if r[col] is None and parsed[col] is not None:
                    change[col] = parsed[col]
            if len(change) > 1:
                plan.append(change)
            if parsed["miss"] and has_text:
                misses.append({
                    "event_id": r["event_id"], "canonical_id": r["canonical_id"],
                    "category": r["category"], "reason": "no_signal",
                    "capacity_mw_raw": r["capacity_mw_raw"],
                    "capacity_details": (r["capacity_details"] or "")[:200],
                    "summary": (r["project_summary"] or "")[:200],
                })
        else:  # MF
            parsed = parse_mf_atomic(r["production_capacity_raw"]) if (
                r["production_capacity_raw"] or ""
            ).strip() else parse_mf_event(
                r["capacity_details"], r["project_summary"], r["capacity_mw_raw"]
            )
            change = {"event_id": r["event_id"]}
            # 成对填：value/unit 只要目标列现有值均为 NULL 且解析出新值就一起写
            if (r["production_capacity_value"] is None and r["production_capacity_unit"] is None
                    and (parsed["production_capacity_value"] is not None
                         or parsed["production_capacity_unit"] is not None)):
                change["production_capacity_value"] = parsed["production_capacity_value"]
                change["production_capacity_unit"] = parsed["production_capacity_unit"]
            if len(change) > 1:
                plan.append(change)
            if parsed["miss"] and has_text:
                misses.append({
                    "event_id": r["event_id"], "canonical_id": r["canonical_id"],
                    "category": r["category"], "reason": parsed["miss_reason"],
                    "capacity_mw_raw": r["capacity_mw_raw"],
                    "capacity_details": (r["capacity_details"] or "")[:200],
                    "summary": (r["project_summary"] or "")[:200],
                })

    return plan, misses


def _summarize_plan(plan: list[dict]) -> dict[str, int]:
    """按列名统计变更计划中将被填充的行数。"""
    counts = {col: 0 for col in _REQUIRED_COLUMNS}
    for change in plan:
        for col in change:
            if col != "event_id":
                counts[col] += 1
    return counts


# ══════════════════════════════════════════════════════════════════
# 备份（WAL 安全，抄 codex_review/apply_actions.py / migrate_add_txmf_columns.py）
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

def _validate_mf_units(plan: list[dict]) -> None:
    """写库前 MF production_capacity_unit 必须过 bri_enums 枚举校验，非法即抛错（规则6）。"""
    valid = set(enums.mf_production_units())
    for change in plan:
        unit = change.get("production_capacity_unit")
        if unit is not None and unit not in valid:
            raise ValueError(f"非法 production_capacity_unit={unit!r}（合法值：{valid}）")


def _apply_plan(db_path: str, plan: list[dict]) -> None:
    """单事务 executemany 落库；每条 change 按其实际含有的列动态生成 UPDATE。"""
    if not plan:
        return

    # 按变更的列集合分组，同结构的合并为一条 executemany（规则3：批量写库用事务+executemany）
    groups: dict[tuple, list[dict]] = {}
    for change in plan:
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
        logger.info("已落库 %d 条变更（%d 个列组合）", len(plan), len(groups))
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# misses CSV
# ══════════════════════════════════════════════════════════════════

def _write_misses_csv(misses: list[dict], out_csv: str) -> None:
    """写 misses CSV（utf-8-sig，Excel 中文兼容）；空 misses 也写表头方便脚本消费。"""
    fieldnames = ["event_id", "canonical_id", "category", "reason",
                  "capacity_mw_raw", "capacity_details", "summary"]
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(misses)
    logger.info("misses 清单已写 %s（%d 行）", out_csv, len(misses))


# ══════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════

def run(db_path: str, apply: bool = False, out_csv: Optional[str] = None) -> dict:
    """执行 TX/MF 回填主流程：入口校验 → 扫描 → (apply: 备份+落库+幂等自检) → misses CSV。

    返回摘要统计 dict：{"would_change": {列: 行数}, "misses": misses条数, "applied": bool}。
    """
    try:
        RunParams(db_path=db_path, apply=apply)
    except ValidationError as e:
        raise ValueError(str(e)) from e

    _validate_db(db_path)

    plan, misses = _scan_events(db_path)
    summary = _summarize_plan(plan)

    if apply:
        _validate_mf_units(plan)
        if plan:
            _backup_db(Path(db_path))
            _apply_plan(db_path, plan)

        # 幂等自检：apply 后重算 would_change 必须全为 0
        replan, _ = _scan_events(db_path)
        resummary = _summarize_plan(replan)
        if any(v != 0 for v in resummary.values()):
            raise RuntimeError(f"幂等自检失败：apply 后仍有待变更 {resummary}")
        logger.info("幂等自检通过：apply 后 would_change 全为 0")

    if out_csv:
        _write_misses_csv(misses, out_csv)

    logger.info("TX/MF 回填%s：would_change=%s，misses=%d",
                "已落库" if apply else "dry-run", summary, len(misses))

    return {"would_change": summary, "misses": len(misses), "applied": apply}


# ══════════════════════════════════════════════════════════════════
# CLI 入口
# ══════════════════════════════════════════════════════════════════

def _setup_logging(db_path: str) -> None:
    """初始化 logging：console + 落文件（CLAUDE.md 规则2）。"""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_path = Path(db_path).resolve().parent / f"txmf_backfill_{ts}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()],
        force=True,
    )
    logger.info("日志文件：%s", log_path)


def main() -> None:
    """CLI 主入口：解析参数、跑 dry-run 或 apply，落 misses CSV。"""
    parser = argparse.ArgumentParser(description="TX/MF 专用指标存量回填")
    parser.add_argument("--db", default=_DEFAULT_DB, help="目标数据库路径")
    parser.add_argument("--apply", action="store_true", help="落库（默认 dry-run）")
    parser.add_argument("--out", default=None, help="misses CSV 输出路径")
    args = parser.parse_args()

    _setup_logging(args.db)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_csv = args.out or str(_SUPPORTING_DOCS / f"txmf_backfill_misses_{ts}.csv")

    try:
        report = run(args.db, apply=args.apply, out_csv=out_csv)
    except (RuntimeError, ValueError) as e:
        logger.error("txmf_backfill 失败：%s", e)
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    print(f">>> would_change={report['would_change']}，misses={report['misses']}，"
          f"{'已落库' if report['applied'] else 'dry-run，未写库'}，misses CSV: {out_csv}")


if __name__ == "__main__":
    main()
