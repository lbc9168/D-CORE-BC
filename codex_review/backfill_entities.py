"""T2 救回项目 chinese_entities 补录执行层：按判定 plan 为摘要点名中企的事件补实体行。

用法：
    uv run --no-sync python -m codex_review.backfill_entities --quarter 2026Q3 [--db PATH] [--plan PATH] [--apply]

流程（老规矩）：dry-run 产出 CSV 供复核 → --apply 时备份(.bak 三重断言) → 单事务 INSERT → 外键自检 + 重放检查(pending=0)。
en 列全部留空由 en_backfill 统一补；entity_en_quality 走表默认。
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError, field_validator

from codex_review.apply_actions import _backup_db, _connect_check, _integrity_check
from codex_review.export_queue import _DEFAULT_DB

# ──────────────────────────────────────────────
# 常量与默认路径
# ──────────────────────────────────────────────

_MODULE_DIR = Path(__file__).resolve().parent
_LOGS_DIR = _MODULE_DIR / "logs"
_DEFAULT_RUNS_DIR = _MODULE_DIR / "runs"

_QUARTER_RE = __import__("re").compile(r"^20\d{2}Q[1-4]$")

# 库内既有 involvement_type 口径（含 Unknown 兜底）
_ALLOWED_INVOLVEMENT = frozenset([
    "EPC总承包", "股权投资方", "设备供应商", "设计/施工服务方",
    "融资方", "投融资+EPC", "BOT/BOOT", "一体化方案提供商", "Unknown",
])

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 入口参数与 plan 条目校验（项目规则 4/6）
# ──────────────────────────────────────────────


class BackfillParams(BaseModel):
    """backfill_entities 的入口参数，含格式与路径存在性校验。"""

    quarter: str
    db_path: Path
    plan_path: Path
    runs_dir: Path

    @field_validator("quarter")
    @classmethod
    def _check_quarter(cls, v: str) -> str:
        """校验 quarter 格式为 20xxQ[1-4]。"""
        if not _QUARTER_RE.match(v):
            raise ValueError(f"quarter 格式非法，期望 20xxQ[1-4]，收到 {v!r}")
        return v

    @field_validator("db_path")
    @classmethod
    def _check_db_exists(cls, v: Path) -> Path:
        """校验数据库文件存在。"""
        if not v.exists():
            raise ValueError(f"数据库文件不存在：{v}")
        return v

    @field_validator("plan_path")
    @classmethod
    def _check_plan_exists(cls, v: Path) -> Path:
        """校验判定 plan 文件存在。"""
        if not v.exists():
            raise ValueError(f"补录 plan 文件不存在：{v}")
        return v


class BackfillEntry(BaseModel):
    """plan 中单条补录判定的结构校验。"""

    canonical_id: str
    event_id: str
    implementing_entity: str
    subsidiary_name: str = ""
    involvement_type: str
    is_central_soe: int | None
    evidence: str
    note: str = ""

    @field_validator("implementing_entity")
    @classmethod
    def _check_entity(cls, v: str) -> str:
        """implementing_entity 不得为空串。"""
        if not v.strip():
            raise ValueError("implementing_entity 为空")
        return v

    @field_validator("involvement_type")
    @classmethod
    def _check_involvement(cls, v: str) -> str:
        """involvement_type 必须在库内既有口径内。"""
        if v not in _ALLOWED_INVOLVEMENT:
            raise ValueError(f"involvement_type 不在既有口径：{v!r}")
        return v

    @field_validator("is_central_soe")
    @classmethod
    def _check_soe(cls, v: int | None) -> int | None:
        """is_central_soe 只允许 0/1/null。"""
        if v is not None and v not in (0, 1):
            raise ValueError(f"is_central_soe 只允许 0/1/null，收到 {v!r}")
        return v


# ──────────────────────────────────────────────
# 日志初始化
# ──────────────────────────────────────────────


def _setup_logging(logs_dir: Path) -> None:
    """初始化 logging：同时输出 console 和带时间戳的日志文件。"""
    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"backfill_entities_{timestamp}.log"

    fmt = "%(asctime)s [%(name)s] %(levelname)s %(message)s"
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers, force=True)
    logger.info("日志文件：%s", log_file)


# ──────────────────────────────────────────────
# 核心执行（dry-run 与 apply 共用同一计算路径）
# ──────────────────────────────────────────────


def run_backfill(
    *,
    quarter: str,
    db_path: Path,
    plan_path: Path,
    apply: bool,
    runs_dir: Path | None = None,
) -> dict[str, Any]:
    """执行补录：校验→逐条判定（插入/幂等跳过/冲突跳过）→apply 时备份+单事务写入+自检；返回统计 dict。"""
    if runs_dir is None:
        runs_dir = _DEFAULT_RUNS_DIR

    try:
        BackfillParams(
            quarter=quarter, db_path=db_path, plan_path=plan_path, runs_dir=runs_dir
        )
    except ValidationError as e:
        logger.error("入口参数校验失败：%s", e)
        raise ValueError(str(e)) from e

    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
    raw_entries = plan_data.get("entries", [])
    entries: list[BackfillEntry] = []
    for i, raw in enumerate(raw_entries):
        try:
            entries.append(BackfillEntry(**raw))
        except ValidationError as e:
            logger.error("plan 第 %d 条结构校验失败：%s；原始=%r", i, e, raw)
            raise ValueError(f"plan 第 {i} 条结构校验失败：{e}") from e

    report: dict[str, Any] = {
        "done": 0,
        "skipped_idempotent": 0,
        "conflict_skipped": 0,
        "error": 0,
        "details": [],
    }
    rows_to_insert: list[tuple] = []

    check_conn = _connect_check(db_path, apply=False)
    try:
        for entry in entries:
            detail: dict[str, Any] = {
                "canonical_id": entry.canonical_id,
                "event_id": entry.event_id,
                "implementing_entity": entry.implementing_entity,
            }

            # 事件存在且归属一致
            ev = check_conn.execute(
                "SELECT canonical_id, project_summary FROM project_events WHERE id=?",
                (entry.event_id,),
            ).fetchone()
            if ev is None:
                logger.error("事件不存在：%r", entry.event_id)
                report["error"] += 1
                detail.update(result="error", reason="event_not_found")
                report["details"].append(detail)
                continue
            ev_canonical, ev_summary = ev
            if ev_canonical != entry.canonical_id:
                logger.error(
                    "事件归属不一致：event=%r 属 %r，plan 写 %r",
                    entry.event_id, ev_canonical, entry.canonical_id,
                )
                report["error"] += 1
                detail.update(result="error", reason=f"canonical_mismatch:{ev_canonical}")
                report["details"].append(detail)
                continue
            if not (ev_summary or "").strip():
                logger.error("事件摘要为空，无摘要证据可依：%r", entry.event_id)
                report["error"] += 1
                detail.update(result="error", reason="empty_summary")
                report["details"].append(detail)
                continue

            # 项目已 excluded → 冲突跳过（救回项目应为未排除态）
            (excluded,) = check_conn.execute(
                "SELECT excluded FROM canonical_projects WHERE id=?",
                (entry.canonical_id,),
            ).fetchone()
            if excluded == 1:
                logger.warning("项目已 excluded，跳过补录：%r", entry.canonical_id)
                report["conflict_skipped"] += 1
                detail.update(result="conflict_skipped", reason="project_excluded")
                report["details"].append(detail)
                continue

            # 幂等检查：同事件+同集团名已有行则跳过
            (dup,) = check_conn.execute(
                "SELECT COUNT(*) FROM chinese_entities WHERE event_id=? AND implementing_entity=?",
                (entry.event_id, entry.implementing_entity),
            ).fetchone()
            if dup > 0:
                report["skipped_idempotent"] += 1
                detail.update(result="skipped_idempotent")
                report["details"].append(detail)
                logger.info(
                    "幂等跳过（已有同名实体行）：event=%r entity=%r",
                    entry.event_id, entry.implementing_entity,
                )
                continue

            rows_to_insert.append(
                (
                    entry.event_id,
                    entry.implementing_entity,
                    entry.subsidiary_name,
                    entry.involvement_type,
                    entry.is_central_soe,
                    "",  # subcontract_amount_raw：对齐库内既有空串口径
                )
            )
            report["done"] += 1
            detail.update(result="done" if apply else "would_insert")
            report["details"].append(detail)
    finally:
        check_conn.close()

    # ── apply：备份 → 单事务批量写入（规则 3） → 自检 ──
    bak_path: str | None = None
    if apply and rows_to_insert:
        bak_path = _backup_db(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            conn.executemany(
                """INSERT INTO chinese_entities
                   (event_id, implementing_entity, subsidiary_name,
                    involvement_type, is_central_soe, subcontract_amount_raw)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                rows_to_insert,
            )
            conn.commit()
        logger.info("单事务提交，共插入 %d 行 chinese_entities", len(rows_to_insert))

        # 外键自检（复用 apply_actions 口径）
        _integrity_check(db_path)

        # 重放检查：同一 plan 重算，待插入集应为 0（全部命中幂等）
        replay = run_backfill(
            quarter=quarter, db_path=db_path, plan_path=plan_path,
            apply=False, runs_dir=runs_dir,
        )
        if replay["done"] != 0:
            raise RuntimeError(
                f"重放检查失败：apply 后仍有 {replay['done']} 条待插入（应为 0），请人工复查"
            )
        logger.info("重放检查通过：apply 后 pending=0")

    report["bak_path"] = bak_path

    # ── 输出 CSV（dry-run 与 apply 都写，供复核/留档） ──
    _write_csv(quarter, entries, report, runs_dir)
    _write_report(quarter, apply, bak_path, report, runs_dir)
    return report


# ──────────────────────────────────────────────
# CSV 与报告输出
# ──────────────────────────────────────────────


def _write_csv(
    quarter: str,
    entries: list[BackfillEntry],
    report: dict[str, Any],
    runs_dir: Path,
) -> Path:
    """写补录清单 CSV（utf-8-sig 带 BOM，Excel 直开不乱码），返回路径。"""
    quarter_dir = runs_dir / quarter
    quarter_dir.mkdir(parents=True, exist_ok=True)
    csv_path = quarter_dir / f"entity_backfill_{quarter}.csv"

    status_by_key: dict[tuple[str, str], str] = {
        (d["event_id"], d["implementing_entity"]): d["result"]
        for d in report["details"]
    }
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "canonical_id", "event_id", "implementing_entity", "subsidiary_name",
            "involvement_type", "is_central_soe", "status", "evidence", "note",
        ])
        for e in entries:
            w.writerow([
                e.canonical_id, e.event_id, e.implementing_entity, e.subsidiary_name,
                e.involvement_type,
                "" if e.is_central_soe is None else e.is_central_soe,
                status_by_key.get((e.event_id, e.implementing_entity), ""),
                e.evidence, e.note,
            ])
    logger.info("补录清单 CSV 已写出：%s（%d 行）", csv_path, len(entries))
    return csv_path


def _write_report(
    quarter: str,
    apply: bool,
    bak_path: str | None,
    report: dict[str, Any],
    runs_dir: Path,
) -> Path:
    """生成 apply_report_entity_backfill.md（覆盖式），返回报告路径。"""
    quarter_dir = runs_dir / quarter
    quarter_dir.mkdir(parents=True, exist_ok=True)
    report_path = quarter_dir / "apply_report_entity_backfill.md"

    mode = "--apply（已执行）" if apply else "dry-run（拟执行）"
    lines: list[str] = [f"# Apply 报告：entity_backfill / {quarter}\n", f"运行模式：{mode}\n"]
    if bak_path:
        lines.append(f"备份路径：`{bak_path}`\n")
    lines.append("\n## 统计")
    for k in ("done", "skipped_idempotent", "conflict_skipped", "error"):
        lines.append(f"- {k}: {report[k]}")
    lines.append("\n## 明细")
    lines.append("| canonical_id | event_id | 实体 | 结果 | 备注 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for d in report["details"]:
        lines.append(
            f"| {d['canonical_id']} | {d['event_id']} | {d['implementing_entity']} "
            f"| {d.get('result', '')} | {d.get('reason', '')} |"
        )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("报告已写出：%s", report_path)
    return report_path


# ──────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    """构建 argparse 解析器。"""
    p = argparse.ArgumentParser(
        description="T2 救回项目 chinese_entities 补录执行层",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--quarter", required=True, help="季度，格式 20xxQ[1-4]")
    p.add_argument("--db", default=str(_DEFAULT_DB), help="主库 SQLite 路径")
    p.add_argument("--plan", default=None,
                   help="补录 plan JSON 路径（默认 runs/<quarter>/entity_backfill_plan_<quarter>.json）")
    p.add_argument("--out-dir", default=str(_DEFAULT_RUNS_DIR), help="输出根目录（runs/ 父目录）")
    p.add_argument("--apply", action="store_true", help="实际写库（默认 dry-run）")
    return p


def main() -> None:
    """CLI 主入口：解析参数并调用 run_backfill。"""
    parser = _build_parser()
    args = parser.parse_args()

    _setup_logging(_LOGS_DIR)

    runs_dir = Path(args.out_dir)
    plan_path = (
        Path(args.plan)
        if args.plan
        else runs_dir / args.quarter / f"entity_backfill_plan_{args.quarter}.json"
    )

    try:
        report = run_backfill(
            quarter=args.quarter,
            db_path=Path(args.db),
            plan_path=plan_path,
            apply=args.apply,
            runs_dir=runs_dir,
        )
    except (ValueError, RuntimeError) as e:
        logger.error("backfill_entities 失败：%s", e)
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    if report["error"] > 0:
        logger.error("存在 %d 条 error 明细，请检查报告", report["error"])
        sys.exit(1)


if __name__ == "__main__":
    main()
