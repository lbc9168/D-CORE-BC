"""Codex 季度分拣薄编排器：循环调用既有 export/ingest/apply 函数 + 生成交接文件，不复制业务逻辑。

用法：
    uv run python -m codex_review.quarterly export --quarter 2026Q3
    uv run python -m codex_review.quarterly ingest --quarter 2026Q3 [--apply]
    uv run python -m codex_review.quarterly apply --quarter 2026Q3 [--apply]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, field_validator

from codex_review.apply_actions import _DEFAULT_RUNS_DIR as _APPLY_DEFAULT_RUNS_DIR
from codex_review.apply_actions import run_apply
from codex_review.export_queue import (
    _DEFAULT_AUDIT_CSV,
    _DEFAULT_DB,
    _DEFAULT_LEDGER,
    _DEFAULT_OUT_DIR,
    _DEFAULT_REGISTRY,
    export_t1,
    export_t2,
    export_t3,
)
from codex_review.ingest_verdicts import run_ingest

_MODULE_DIR = Path(__file__).resolve().parent
_WORKTREE_ROOT = _MODULE_DIR.parent
_SUPPORTING_DOCS_DIR = _WORKTREE_ROOT / "supporting_docs"
_LOGS_DIR = _MODULE_DIR / "logs"

_QUARTER_RE = re.compile(r"^20\d{2}Q[1-4]$")
_TASKS = ["T1", "T2", "T3"]

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 入口参数校验模型（项目规则 4）
# ──────────────────────────────────────────────


class QuarterlyExportParams(BaseModel):
    """quarterly export 的入口参数，含 quarter 格式校验。"""

    quarter: str

    @field_validator("quarter")
    @classmethod
    def _check_quarter(cls, v: str) -> str:
        """校验 quarter 格式为 20xxQ[1-4]。"""
        if not _QUARTER_RE.match(v):
            raise ValueError(f"quarter 格式非法，期望 20xxQ[1-4]，收到 {v!r}")
        return v


class QuarterlyIngestParams(BaseModel):
    """quarterly ingest 的入口参数，含 quarter 格式与 sample_rate 范围校验。"""

    quarter: str
    sample_rate: float

    @field_validator("quarter")
    @classmethod
    def _check_quarter(cls, v: str) -> str:
        """校验 quarter 格式为 20xxQ[1-4]。"""
        if not _QUARTER_RE.match(v):
            raise ValueError(f"quarter 格式非法，期望 20xxQ[1-4]，收到 {v!r}")
        return v

    @field_validator("sample_rate")
    @classmethod
    def _check_rate(cls, v: float) -> float:
        """校验 sample_rate 在 [0, 1] 范围内。"""
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"sample_rate 必须在 [0,1]，收到 {v}")
        return v


class QuarterlyApplyParams(BaseModel):
    """quarterly apply 的入口参数，含 quarter 格式校验。"""

    quarter: str

    @field_validator("quarter")
    @classmethod
    def _check_quarter(cls, v: str) -> str:
        """校验 quarter 格式为 20xxQ[1-4]。"""
        if not _QUARTER_RE.match(v):
            raise ValueError(f"quarter 格式非法，期望 20xxQ[1-4]，收到 {v!r}")
        return v


# ──────────────────────────────────────────────
# 日志初始化
# ──────────────────────────────────────────────


def _setup_logging(step: str) -> None:
    """初始化 logging：同时输出 console 和带时间戳的日志文件。"""
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = _LOGS_DIR / f"quarterly_{step}_{timestamp}.log"

    fmt = "%(asctime)s [%(name)s] %(levelname)s %(message)s"
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers, force=True)
    logger.info("日志文件：%s", log_file)


# ──────────────────────────────────────────────
# misses CSV 自动选最新
# ──────────────────────────────────────────────


def _find_latest_misses_csv(misses_csv_dir: Path) -> Path:
    """在目录下 glob entity_reparse_misses_*.csv，按 mtime 取最新；找不到则抛错。"""
    candidates = sorted(
        Path(misses_csv_dir).glob("entity_reparse_misses_*.csv"),
        key=lambda p: p.stat().st_mtime,
    )
    if not candidates:
        raise FileNotFoundError(
            f"目录下找不到 entity_reparse_misses_*.csv：{misses_csv_dir}"
        )
    latest = candidates[-1]
    logger.info("misses CSV 自动选中最新文件：%s", latest)
    return latest


# ──────────────────────────────────────────────
# export 子命令：依次调 T1/T2/T3 导出函数 + 生成 CODEX_README
# ──────────────────────────────────────────────


def run_export(
    *,
    quarter: str,
    db: Path,
    audit_csv: Path,
    extra_ids: list[str],
    misses_csv_dir: Path,
    registry: Path,
    out_dir: Path,
    ledger_path: Path,
) -> None:
    """依次导出 T1/T2/T3 队列，misses CSV 自动选最新，收尾生成 CODEX_README.md。"""
    params = QuarterlyExportParams(quarter=quarter)

    misses_csv = _find_latest_misses_csv(misses_csv_dir)

    export_t1(quarter=params.quarter, db=db, out_dir=out_dir, ledger_path=ledger_path)
    export_t2(
        quarter=params.quarter,
        db=db,
        audit_csv=audit_csv,
        extra_ids=extra_ids,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )
    export_t3(
        quarter=params.quarter,
        misses_csv=misses_csv,
        registry=registry,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )

    quarter_dir = Path(out_dir) / params.quarter
    counts = _read_queue_counts(quarter_dir)

    readme_path = generate_codex_readme(quarter_dir, params.quarter, counts)

    print(
        f"三队列汇总：T1={counts['T1']} 条，T2={counts['T2']} 条，T3={counts['T3']} 条"
    )
    print(f"交接文件已生成：{readme_path}")
    print(
        f"下一步：把 {quarter_dir}/ 交给 Codex：让它读 CODEX_README.md 照做；"
        f"完成后跑 quarterly ingest"
    )


def _read_queue_counts(quarter_dir: Path) -> dict[str, int]:
    """读取 quarter_dir 下三个 queue_T*.json 的条数，返回 {task: count} 字典。"""
    counts: dict[str, int] = {}
    for task in _TASKS:
        queue_path = quarter_dir / f"queue_{task}.json"
        if not queue_path.exists():
            counts[task] = 0
            continue
        data = json.loads(queue_path.read_text(encoding="utf-8"))
        counts[task] = len(data.get("items", []))
    return counts


# ──────────────────────────────────────────────
# CODEX_README 生成器（独立函数便于测试）
# ──────────────────────────────────────────────

_TASK_LABELS = {
    "T1": "T1 漏合并候选对",
    "T2": "T2 范围复核",
    "T3": "T3 实体归属补录",
}


def generate_codex_readme(quarter_dir: Path, quarter: str, counts: dict[str, int]) -> Path:
    """生成 <quarter_dir>/CODEX_README.md，内容基于实际扫描到的队列条数（不硬编码）。"""
    quarter_dir = Path(quarter_dir)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines: list[str] = []
    lines.append(f"# {quarter} 季度复核包")
    lines.append("")
    lines.append(f"生成时间：{generated_at}")
    lines.append("")
    lines.append(
        "**你的任务**：读取本文件夹里的 guide_T*.md 分拣规则，逐条判断 queue_T*.json "
        "里的条目，把结果写成 verdicts_T*.json。"
    )
    lines.append("")
    lines.append("## 文件清单")
    lines.append("")
    lines.append("| 任务 | 输入 | 规则 | 输出 | 条数 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for task in _TASKS:
        n = counts.get(task, 0)
        note = "（本轮无活，跳过）" if n == 0 else str(n)
        lines.append(
            f"| {_TASK_LABELS[task]} | queue_{task}.json | guide_{task}.md | "
            f"verdicts_{task}.json | {note} |"
        )
    lines.append("")
    lines.append("## 硬规则")
    lines.append("")
    lines.append("1. 先读对应 guide 再动手。")
    lines.append(
        '2. 输出信封 `{"task":"T?","quarter":"' + quarter + '","verdicts":[...]}`，'
        "字段以 guide 内 JSON 示例为准，**禁止任何额外字段**（严格校验，多余字段整条拒收）。"
    )
    lines.append("3. evidence_quote 必须逐字引用 item 上下文或来源原文。")
    lines.append("4. T3 归属类结论必须联网核实，且 source_urls 为 http(s) 链接。")
    lines.append("5. 只写三个 verdicts 文件，不改动本文件夹其他任何文件。")
    lines.append("")
    lines.append("## 分批说明")
    lines.append("")
    lines.append(
        "允许部分完成——verdicts 里只包含已判条目即可，未判条目下轮重新入队；"
        "量大（如 T1 上千对）建议分批追加，每批写完自行 `json.load` 校验可解析。"
    )
    lines.append("")
    lines.append("## 完成后")
    lines.append("")
    lines.append(
        f"告知操作者运行：`uv run --no-sync python -m codex_review.quarterly ingest "
        f"--quarter {quarter}`"
    )
    lines.append("")

    readme_path = quarter_dir / "CODEX_README.md"
    readme_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("CODEX_README 已生成：%s", readme_path)
    return readme_path


# ──────────────────────────────────────────────
# ingest 子命令：对存在 verdicts 的任务依次调 run_ingest
# ──────────────────────────────────────────────


def run_ingest_all(
    *,
    quarter: str,
    runs_dir: Path,
    ledger_path: Path,
    apply: bool,
    sample_rate: float,
) -> int:
    """对存在 verdicts_<task>.json 的任务依次调 run_ingest；缺文件的任务跳过并提示。

    返回值：全部成功=0；任一任务信封不符等致命错误→非零。
    """
    params = QuarterlyIngestParams(quarter=quarter, sample_rate=sample_rate)
    quarter_dir = Path(runs_dir) / params.quarter

    summary: dict[str, dict] = {}
    any_failed = False

    for task in _TASKS:
        verdicts_path = quarter_dir / f"verdicts_{task}.json"
        queue_path = quarter_dir / f"queue_{task}.json"
        if not verdicts_path.exists():
            print(f"[{task}] verdicts_{task}.json 不存在，跳过")
            logger.info("跳过 %s：verdicts 文件不存在（%s）", task, verdicts_path)
            continue

        try:
            result = run_ingest(
                task=task,
                quarter=params.quarter,
                verdicts_path=verdicts_path,
                queue_path=queue_path,
                ledger_path=ledger_path,
                apply=apply,
                sample_rate=params.sample_rate,
                runs_dir=runs_dir,
            )
        except SystemExit as e:
            any_failed = True
            logger.error("%s ingest 致命错误退出，code=%s", task, e.code)
            print(f"[{task}] FAILED：信封校验或其他致命错误，详见日志")
            continue
        except Exception as e:
            # 隔离边界：单任务的非预期异常（如队列 JSON 损坏抛 ValueError）不得
            # 连累其余任务——记录后继续下一任务，最终由 any_failed 触发非零退出。
            any_failed = True
            logger.error("%s ingest 未捕获异常：%s: %s", task, type(e).__name__, e)
            print(f"[{task}] FAILED：{type(e).__name__}: {e}")
            continue

        summary[task] = {
            "有效": result.valid,
            "自动收": len(result.auto_accepted),
            "gated": len(result.gated),
            "uncertain": len(result.skipped_uncertain),
        }

    _print_ingest_summary(summary)

    if apply:
        print("下一步：请复核 manual_check_*.csv 后跑 quarterly apply")
    else:
        print("下一步：确认统计无误后加 --apply 重跑")

    return 1 if any_failed else 0


def _print_ingest_summary(summary: dict[str, dict]) -> None:
    """打印三任务的 ingest 汇总表。"""
    print("\n== ingest 汇总 ==")
    for task in _TASKS:
        if task not in summary:
            continue
        s = summary[task]
        print(
            f"[{task}] 有效={s['有效']} 自动收={s['自动收']} "
            f"gated={s['gated']} uncertain={s['uncertain']}"
        )


# ──────────────────────────────────────────────
# apply 子命令：对存在 apply_plan 的任务依次调 run_apply
# ──────────────────────────────────────────────


def run_apply_all(
    *,
    quarter: str,
    runs_dir: Path,
    db: Path,
    ledger_path: Path,
    apply: bool,
) -> int:
    """对存在 apply_plan_<task>.json 的任务依次调 run_apply；缺文件的任务跳过。

    返回值：全部成功=0；任一任务失败→非零。
    """
    params = QuarterlyApplyParams(quarter=quarter)
    quarter_dir = Path(runs_dir) / params.quarter

    summary: dict[str, dict] = {}
    any_failed = False

    for task in _TASKS:
        plan_path = quarter_dir / f"apply_plan_{task}.json"
        if not plan_path.exists():
            print(f"[{task}] apply_plan_{task}.json 不存在，跳过")
            logger.info("跳过 %s：apply_plan 文件不存在（%s）", task, plan_path)
            continue

        try:
            report = run_apply(
                task=task,
                quarter=params.quarter,
                db_path=db,
                plan_path=plan_path,
                ledger_path=ledger_path,
                apply=apply,
                runs_dir=runs_dir,
            )
        except SystemExit as e:
            any_failed = True
            logger.error("%s apply 致命错误退出，code=%s", task, e.code)
            print(f"[{task}] FAILED：致命错误，详见日志")
            continue
        except Exception as e:
            # 隔离边界：单任务的非预期异常（如 _backup_db 三重断言在磁盘满/锁场景
            # 抛 RuntimeError）不得连累其余任务——记录后继续下一任务，最终由
            # any_failed 触发非零退出。
            any_failed = True
            logger.error("%s apply 未捕获异常：%s: %s", task, type(e).__name__, e)
            print(f"[{task}] FAILED：{type(e).__name__}: {e}")
            continue

        summary[task] = report

    _print_apply_summary(summary)

    if apply:
        print("下一步：T3 三步 / en_backfill→run_export→部署")
    else:
        print("下一步：确认统计无误后加 --apply 执行")

    return 1 if any_failed else 0


def _print_apply_summary(summary: dict[str, dict]) -> None:
    """打印三任务的 apply 汇总表（done/skipped/conflict/error）。"""
    print("\n== apply 汇总 ==")
    for task in _TASKS:
        if task not in summary:
            continue
        r = summary[task]
        print(
            f"[{task}] done={r.get('done', 0)} "
            f"skipped_idempotent={r.get('skipped_idempotent', 0)} "
            f"conflict_skipped={r.get('conflict_skipped', 0)} "
            f"error={r.get('error', 0)}"
        )


# ──────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    """构建 argparse 解析器（export/ingest/apply 三子命令）。"""
    p = argparse.ArgumentParser(
        description="Codex 季度分拣薄编排器",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_export = sub.add_parser("export", help="依次导出 T1/T2/T3 队列 + 生成 CODEX_README")
    p_export.add_argument("--quarter", required=True, help="季度，格式 20xxQ[1-4]")
    p_export.add_argument("--db", default=str(_DEFAULT_DB), help="主库 SQLite 路径")
    p_export.add_argument("--audit-csv", default=str(_DEFAULT_AUDIT_CSV), help="T2 审计清单 CSV")
    p_export.add_argument("--t2-extra-ids", default="", help="T2 额外追加的 project_id，逗号分隔")
    p_export.add_argument("--registry", default=str(_DEFAULT_REGISTRY), help="T3 集团层级 JSON")
    p_export.add_argument("--out-dir", default=str(_DEFAULT_OUT_DIR), help="输出根目录")
    p_export.add_argument("--ledger", default=str(_DEFAULT_LEDGER), help="台账 JSON 路径")

    p_ingest = sub.add_parser("ingest", help="对存在 verdicts 的任务依次回收校验")
    p_ingest.add_argument("--quarter", required=True, help="季度，格式 20xxQ[1-4]")
    p_ingest.add_argument("--apply", action="store_true", help="实际落台账与 apply 清单")
    p_ingest.add_argument("--sample-rate", type=float, default=0.15, help="改数据类抽检比例")
    p_ingest.add_argument("--ledger", default=str(_DEFAULT_LEDGER), help="台账 JSON 路径")
    p_ingest.add_argument("--out-dir", default=str(_DEFAULT_OUT_DIR), help="输出根目录（runs/ 父目录）")

    p_apply = sub.add_parser("apply", help="对存在 apply_plan 的任务依次落库")
    p_apply.add_argument("--quarter", required=True, help="季度，格式 20xxQ[1-4]")
    p_apply.add_argument("--apply", action="store_true", help="实际执行（默认 dry-run）")
    p_apply.add_argument("--db", default=str(_DEFAULT_DB), help="主库 SQLite 路径")
    p_apply.add_argument("--ledger", default=str(_DEFAULT_LEDGER), help="台账 JSON 路径")
    p_apply.add_argument("--out-dir", default=str(_APPLY_DEFAULT_RUNS_DIR), help="输出根目录（runs/ 父目录）")

    return p


def main() -> None:
    """CLI 主入口：解析参数并 dispatch 到 export/ingest/apply。"""
    parser = _build_parser()
    args = parser.parse_args()

    _setup_logging(args.cmd)

    if args.cmd == "export":
        extra_ids = (
            [x.strip() for x in args.t2_extra_ids.split(",") if x.strip()]
            if args.t2_extra_ids
            else []
        )
        try:
            run_export(
                quarter=args.quarter,
                db=Path(args.db),
                audit_csv=Path(args.audit_csv),
                extra_ids=extra_ids,
                misses_csv_dir=_SUPPORTING_DOCS_DIR,
                registry=Path(args.registry),
                out_dir=Path(args.out_dir),
                ledger_path=Path(args.ledger),
            )
        except (ValueError, FileNotFoundError) as e:
            logger.error("export 失败：%s", e)
            print(f"[ERROR] {e}", file=sys.stderr)
            sys.exit(1)

    elif args.cmd == "ingest":
        exit_code = run_ingest_all(
            quarter=args.quarter,
            runs_dir=Path(args.out_dir),
            ledger_path=Path(args.ledger),
            apply=args.apply,
            sample_rate=args.sample_rate,
        )
        sys.exit(exit_code)

    else:  # apply
        exit_code = run_apply_all(
            quarter=args.quarter,
            runs_dir=Path(args.out_dir),
            db=Path(args.db),
            ledger_path=Path(args.ledger),
            apply=args.apply,
        )
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
