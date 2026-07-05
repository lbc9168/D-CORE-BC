"""Codex 季度分拣 verdict 回收 CLI：校验 + 分级信任 + 15% 抽检。

用法：
    uv run python -m codex_review.ingest_verdicts --task {T1,T2,T3} --quarter 2026Q3 [选项]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import random
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError, field_validator

from codex_review.ledger import (
    is_settled,
    load_ledger,
    record,
    save_ledger,
    t2_key,
    t3_key,
)
from codex_review.schemas import (
    T1QueueFile,
    T1Verdict,
    T1VerdictsFile,
    T2QueueFile,
    T2Verdict,
    T2VerdictsFile,
    T3QueueFile,
    T3Verdict,
    T3VerdictsFile,
    _T3_REQUIRE_GROUP,
)

# ──────────────────────────────────────────────
# 常量与默认路径
# ──────────────────────────────────────────────

_MODULE_DIR = Path(__file__).resolve().parent
_WORKTREE_ROOT = _MODULE_DIR.parent
_LOGS_DIR = _MODULE_DIR / "logs"
_DEFAULT_RUNS_DIR = _MODULE_DIR / "runs"
_DEFAULT_LEDGER = _MODULE_DIR / "ledger.json"

_QUARTER_RE = re.compile(r"^20\d{2}Q[1-4]$")

logger = logging.getLogger(__name__)

# 不改数据（自动收）的分级规则
_T1_AUTO_ACCEPT = {False}  # same_project=False
_T2_AUTO_ACCEPT = {"keep"}
_T3_AUTO_ACCEPT = {"not_chinese", "not_company"}
_T3_SKIP = {"uncertain"}


# ──────────────────────────────────────────────
# 入口参数校验模型（项目规则 4）
# ──────────────────────────────────────────────


class IngestParams(BaseModel):
    """ingest_verdicts 的入口参数，含格式与路径存在性校验。"""

    task: str
    quarter: str
    verdicts_path: Path
    queue_path: Path
    ledger_path: Path
    sample_rate: float
    runs_dir: Path

    @field_validator("quarter")
    @classmethod
    def _check_quarter(cls, v: str) -> str:
        """校验 quarter 格式为 20xxQ[1-4]。"""
        if not _QUARTER_RE.match(v):
            raise ValueError(f"quarter 格式非法，期望 20xxQ[1-4]，收到 {v!r}")
        return v

    @field_validator("verdicts_path", "queue_path")
    @classmethod
    def _check_exists(cls, v: Path) -> Path:
        """校验路径存在。"""
        if not v.exists():
            raise ValueError(f"文件不存在：{v}")
        return v

    @field_validator("sample_rate")
    @classmethod
    def _check_rate(cls, v: float) -> float:
        """校验 sample_rate 在 [0, 1] 范围内。"""
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"sample_rate 必须在 [0,1]，收到 {v}")
        return v


# ──────────────────────────────────────────────
# 结果数据类
# ──────────────────────────────────────────────


@dataclass
class IngestResult:
    """ingest_verdicts 运行结果的汇总，供测试与报告使用。"""

    total: int = 0
    valid: int = 0
    rejected: list[dict] = field(default_factory=list)
    auto_accepted: list[dict] = field(default_factory=list)
    gated: list[dict] = field(default_factory=list)
    skipped_uncertain: list[Any] = field(default_factory=list)
    skipped_already_settled: int = 0


# ──────────────────────────────────────────────
# 日志初始化
# ──────────────────────────────────────────────


def _setup_logging(task: str, logs_dir: Path) -> None:
    """初始化 logging：同时输出 console 和带时间戳的日志文件。"""
    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"ingest_{task}_{timestamp}.log"

    fmt = "%(asctime)s [%(name)s] %(levelname)s %(message)s"
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers, force=True)
    logger.info("日志文件：%s", log_file)


# ──────────────────────────────────────────────
# verdict digest 计算
# ──────────────────────────────────────────────


def _digest(verdict_dict: dict) -> str:
    """计算 verdict canonical JSON 的 sha1 摘要（用于台账 verdict_digest 字段）。"""
    canonical = json.dumps(verdict_dict, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


# ──────────────────────────────────────────────
# 信封校验（致命错误 → SystemExit）
# ──────────────────────────────────────────────


def _load_and_validate_envelope(
    verdicts_path: Path,
    task: str,
    quarter: str,
) -> tuple[str, list[dict]]:
    """加载 verdicts JSON，校验信封 task/quarter，返回 (task, raw_verdicts_list)。

    信封不匹配时 logger.error 后 sys.exit(1)（致命错误）。
    """
    try:
        raw = json.loads(verdicts_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.error("verdicts 文件 JSON 解析失败：%s", e)
        sys.exit(1)

    file_task = raw.get("task")
    file_quarter = raw.get("quarter")

    if file_task != task:
        logger.error(
            "信封 task 与 CLI 不符：文件=%r，CLI=%r，终止", file_task, task
        )
        sys.exit(1)

    if file_quarter != quarter:
        logger.error(
            "信封 quarter 与 CLI 不符：文件=%r，CLI=%r，终止", file_quarter, quarter
        )
        sys.exit(1)

    verdicts_raw: list[dict] = raw.get("verdicts", [])
    return task, verdicts_raw


# ──────────────────────────────────────────────
# 队列 id 集合提取
# ──────────────────────────────────────────────


def _load_queue_ids(queue_path: Path, task: str) -> dict[str, dict]:
    """加载队列文件，返回以"被引用 id"为键、条目 dict 为值的映射。

    T1: pair_id → item dict
    T2: project_id → item dict（注意 T2 key 需要 trigger，存整条以便后续取）
    T3: raw_name → item dict
    """
    raw = json.loads(queue_path.read_text(encoding="utf-8"))
    items = raw.get("items", [])
    mapping: dict[str, dict] = {}
    for item in items:
        if task == "T1":
            k = item.get("pair_id", "")
        elif task == "T2":
            k = item.get("project_id", "")
        else:  # T3
            k = item.get("raw_name", "")
        if k:
            mapping[k] = item
    return mapping


# ──────────────────────────────────────────────
# 单条 verdict 校验（逐条，非致命）
# ──────────────────────────────────────────────


def _get_verdict_id(raw: dict, task: str) -> str:
    """从 raw verdict dict 提取其"身份 id"（T1=pair_id, T2=project_id, T3=raw_name）。"""
    if task == "T1":
        return raw.get("pair_id", "")
    if task == "T2":
        return raw.get("project_id", "")
    return raw.get("raw_name", "")


def _validate_one(
    raw: dict,
    task: str,
    queue_map: dict[str, dict],
    seen_ids: set[str],
) -> tuple[Any | None, str | None]:
    """校验单条 verdict，返回 (parsed_verdict, error_reason) 二元组。

    返回 (None, reason) 表示拒收；返回 (verdict, None) 表示通过。
    """
    verdict_id = _get_verdict_id(raw, task)

    # 步骤 2：Pydantic schema 校验
    try:
        if task == "T1":
            parsed: Any = T1Verdict.model_validate(raw)
        elif task == "T2":
            parsed = T2Verdict.model_validate(raw)
        else:
            parsed = T3Verdict.model_validate(raw)
    except ValidationError as e:
        logger.error("verdict schema 校验失败，id=%r，错误=%s", verdict_id, e)
        return None, f"schema_error: {e.error_count()} 项"

    # 步骤 3：id 必须在队列
    if verdict_id not in queue_map:
        logger.error("verdict id 不在队列，id=%r，task=%s", verdict_id, task)
        return None, "not_in_queue"

    # 步骤 4：重复 id
    if verdict_id in seen_ids:
        logger.error("verdict id 重复出现，后续拒收，id=%r", verdict_id)
        return None, "duplicate"

    # 步骤 5：T1 survivor_id 属 pair（仅 same_project=True 时已由 Pydantic 强制有值）
    if task == "T1" and parsed.same_project:
        queue_item = queue_map[verdict_id]
        cid_a = queue_item.get("a", {}).get("canonical_id", "")
        cid_b = queue_item.get("b", {}).get("canonical_id", "")
        if parsed.survivor_id not in {cid_a, cid_b}:
            logger.error(
                "T1 survivor_id 不属于 pair，pair_id=%r，survivor=%r，a=%r，b=%r",
                verdict_id,
                parsed.survivor_id,
                cid_a,
                cid_b,
            )
            return None, f"survivor_not_in_pair: survivor={parsed.survivor_id!r}"

    # 步骤 6：T3 归属类 source_urls 必须以 http:// 或 https:// 开头
    if task == "T3" and parsed.classification in _T3_REQUIRE_GROUP:
        for url in parsed.source_urls:
            if not (url.startswith("http://") or url.startswith("https://")):
                logger.error(
                    "T3 source_url 非 http/https，raw_name=%r，url=%r", verdict_id, url
                )
                return None, "invalid_source_url"

    return parsed, None


# ──────────────────────────────────────────────
# 分级信任判断
# ──────────────────────────────────────────────


def _classify_verdict(parsed: Any, task: str) -> str:
    """将已通过校验的 verdict 分为 auto_accept / skip_uncertain / gated 三类。"""
    if task == "T1":
        return "auto_accept" if not parsed.same_project else "gated"
    if task == "T2":
        return "auto_accept" if parsed.action in _T2_AUTO_ACCEPT else "gated"
    # T3
    if parsed.classification in _T3_SKIP:
        return "skip_uncertain"
    if parsed.classification in _T3_AUTO_ACCEPT:
        return "auto_accept"
    return "gated"


# ──────────────────────────────────────────────
# 台账 key 计算（T1 直接用 pair_id）
# ──────────────────────────────────────────────


def _ledger_key(parsed: Any, task: str, queue_item: dict) -> str:
    """计算该 verdict 在台账中的幂等 key。"""
    if task == "T1":
        # pair_id 本身即为台账 key（导出时已是 t1_key）
        return parsed.pair_id
    if task == "T2":
        trigger = queue_item.get("trigger", "fixed_list")
        return t2_key(parsed.project_id, trigger)
    return t3_key(parsed.raw_name)


# ──────────────────────────────────────────────
# apply_plan 条目构建
# ──────────────────────────────────────────────


def _build_plan_entry(parsed: Any, task: str, queue_item: dict) -> dict:
    """构建 apply_plan 条目：verdict 全文 + 队列侧关键上下文。"""
    entry: dict = {"verdict": parsed.model_dump()}
    if task == "T1":
        entry["context"] = {
            "a_canonical_id": queue_item.get("a", {}).get("canonical_id"),
            "a_project_name": queue_item.get("a", {}).get("project_name"),
            "b_canonical_id": queue_item.get("b", {}).get("canonical_id"),
            "b_project_name": queue_item.get("b", {}).get("project_name"),
        }
    elif task == "T2":
        entry["context"] = {
            "project_name": queue_item.get("project", {}).get("project_name"),
            "trigger": queue_item.get("trigger"),
        }
    else:  # T3
        entry["context"] = {
            "freq": queue_item.get("freq"),
        }
    return entry


# ──────────────────────────────────────────────
# 确定性抽样
# ──────────────────────────────────────────────


def _sample_gated(gated_ids: list[str], task: str, quarter: str, rate: float) -> list[str]:
    """从 gated id 列表中确定性抽样，数量=ceil(n*rate)，n>0 时至少 1。"""
    n = len(gated_ids)
    if n == 0:
        return []
    k = max(1, math.ceil(n * rate))
    rng = random.Random(f"{task}-{quarter}")
    # 先排序保证确定性（Random 对 list 顺序敏感）
    sorted_ids = sorted(gated_ids)
    return rng.sample(sorted_ids, min(k, n))


# ──────────────────────────────────────────────
# 报告生成
# ──────────────────────────────────────────────


def _build_report(
    result: IngestResult,
    task: str,
    quarter: str,
    apply: bool,
    manual_check_path: Path | None,
    apply_plan_path: Path | None,
) -> str:
    """生成 markdown 格式的 ingest 报告文本。"""
    lines: list[str] = []
    lines.append(f"# Ingest 报告：{task} / {quarter}\n")
    lines.append(f"运行模式：{'--apply' if apply else 'dry-run'}\n")

    lines.append("\n## 统计")
    lines.append("| 项目 | 数量 |")
    lines.append("| --- | --- |")
    lines.append(f"| 总条数 | {result.total} |")
    lines.append(f"| 有效条数 | {result.valid} |")
    lines.append(f"| 拒收条数 | {len(result.rejected)} |")
    lines.append(f"| 自动收条数 | {len(result.auto_accepted)} |")
    lines.append(f"| 改数据（gated）条数 | {len(result.gated)} |")
    lines.append(f"| uncertain 跳过条数 | {len(result.skipped_uncertain)} |")
    lines.append(f"| 已定案跳过条数（幂等）| {result.skipped_already_settled} |")

    if result.rejected:
        lines.append("\n## 拒收明细")
        lines.append("| id | 原因 | 原始 JSON 片段 |")
        lines.append("| --- | --- | --- |")
        for r in result.rejected:
            raw_snip = json.dumps(r.get("raw", {}), ensure_ascii=False)[:120]
            lines.append(f"| {r['id']} | {r['reason']} | `{raw_snip}` |")

    if result.auto_accepted:
        lines.append("\n## 自动收明细")
        lines.append("| id | 结论 |")
        lines.append("| --- | --- |")
        for a in result.auto_accepted:
            lines.append(f"| {a['id']} | {a['summary']} |")

    if result.gated:
        lines.append("\n## 改数据（gated）明细")
        lines.append("| id | 动作 |")
        lines.append("| --- | --- |")
        for g in result.gated:
            lines.append(f"| {g['id']} | {g['action']} |")

    if result.skipped_uncertain:
        lines.append("\n## uncertain 跳过（下季度自然重新入队）")
        for s in result.skipped_uncertain:
            lines.append(f"- {s}")

    if apply and apply_plan_path and len(result.gated) > 0:
        lines.append("\n## 抽检清单")
        lines.append(f"- apply_plan 文件：`{apply_plan_path}`")
        if manual_check_path:
            lines.append(f"- manual_check CSV：`{manual_check_path}`（{len(result.gated)} 条 gated 中抽检部分）")

    lines.append("\n## 下一步")
    if not apply:
        lines.append("- dry-run 完成，请检查以上统计，确认后加 `--apply` 重跑以落台账与 apply 清单。")
    else:
        lines.append("- apply 清单已生成（apply_plan_<task>.json）。")
        lines.append("- DB/registry 写入由 apply_actions 执行（用户确认抽检后）。")
        lines.append(f"- 请先复核 `manual_check_{task}.csv`，再跑 apply_actions。")

    return "\n".join(lines) + "\n"


# ──────────────────────────────────────────────
# 占位函数（下一任务接入）
# ──────────────────────────────────────────────


def _execute_apply_plan(task: str, plan_path: Path, **kwargs: Any) -> None:
    """占位：apply_actions 将在后续任务中接入，本 CLI 不暴露触发参数。"""
    raise NotImplementedError("apply_actions 于后续任务接入")


# ──────────────────────────────────────────────
# 核心 ingest 逻辑
# ──────────────────────────────────────────────


def run_ingest(
    task: str,
    quarter: str,
    verdicts_path: Path,
    queue_path: Path,
    ledger_path: Path,
    *,
    apply: bool = False,
    sample_rate: float = 0.15,
    runs_dir: Path | None = None,
) -> IngestResult:
    """执行 verdict 回收主流程：校验 + 分级信任 + 15% 抽检，返回 IngestResult。"""
    if runs_dir is None:
        runs_dir = _DEFAULT_RUNS_DIR

    # 入口参数校验
    try:
        params = IngestParams(
            task=task,
            quarter=quarter,
            verdicts_path=verdicts_path,
            queue_path=queue_path,
            ledger_path=ledger_path,
            sample_rate=sample_rate,
            runs_dir=runs_dir,
        )
    except ValidationError as e:
        logger.error("入口参数校验失败：%s", e)
        sys.exit(1)

    quarter_dir = runs_dir / quarter
    quarter_dir.mkdir(parents=True, exist_ok=True)

    # 信封校验（致命错误退出）
    _, verdicts_raw = _load_and_validate_envelope(verdicts_path, task, quarter)

    # 加载队列（用于 id 存在性校验）
    queue_map = _load_queue_ids(queue_path, task)

    # 加载台账（apply 时写，dry-run 时不写）
    ledger = load_ledger(ledger_path)

    result = IngestResult(total=len(verdicts_raw))
    seen_ids: set[str] = set()
    gated_plan_entries: list[dict] = []
    gated_ids_ordered: list[str] = []  # 保留插入顺序用于 plan；抽样前再 sort

    for raw in verdicts_raw:
        verdict_id = _get_verdict_id(raw, task)

        parsed, err_reason = _validate_one(raw, task, queue_map, seen_ids)
        if err_reason is not None:
            result.rejected.append({"id": verdict_id, "reason": err_reason, "raw": raw})
            logger.info("拒收：id=%r，reason=%s", verdict_id, err_reason)
            continue

        seen_ids.add(verdict_id)
        result.valid += 1

        # 台账幂等检查（apply 时才实际跳过）
        lkey = _ledger_key(parsed, task, queue_map[verdict_id])
        if apply and is_settled(ledger, lkey):
            result.skipped_already_settled += 1
            logger.info("台账已定案，幂等跳过：id=%r", verdict_id)
            continue

        tier = _classify_verdict(parsed, task)

        if tier == "skip_uncertain":
            result.skipped_uncertain.append(verdict_id)
            logger.info("uncertain 跳过：id=%r", verdict_id)

        elif tier == "auto_accept":
            summary = _auto_accept_summary(parsed, task)
            result.auto_accepted.append({"id": verdict_id, "summary": summary})
            if apply:
                record(
                    ledger,
                    lkey,
                    task=task,
                    quarter=quarter,
                    item_id=verdict_id,
                    status="accepted_no_change",
                    verdict_digest=_digest(raw),
                )
                logger.info("自动收：id=%r，summary=%s", verdict_id, summary)

        else:  # gated
            action = _gated_action(parsed, task)
            result.gated.append({"id": verdict_id, "action": action})
            if apply:
                entry = _build_plan_entry(parsed, task, queue_map[verdict_id])
                gated_plan_entries.append(entry)
                gated_ids_ordered.append(verdict_id)
                logger.info("gated（apply_plan）：id=%r，action=%s", verdict_id, action)

    # apply 模式：写台账 + apply_plan + manual_check
    apply_plan_path: Path | None = None
    manual_check_path: Path | None = None

    if apply:
        save_ledger(ledger_path, ledger)
        logger.info("台账已保存：%s", ledger_path)

        if gated_plan_entries:
            apply_plan_path = quarter_dir / f"apply_plan_{task}.json"
            apply_plan_path.write_text(
                json.dumps(gated_plan_entries, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("apply_plan 已写出：%s（%d 条）", apply_plan_path, len(gated_plan_entries))

            sampled_ids = _sample_gated(gated_ids_ordered, task, quarter, sample_rate)
            if sampled_ids:
                manual_check_path = quarter_dir / f"manual_check_{task}.csv"
                with open(manual_check_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=["id", "action"])
                    writer.writeheader()
                    for sid in sampled_ids:
                        gated_entry = next(
                            (g for g in result.gated if g["id"] == sid), None
                        )
                        writer.writerow({
                            "id": sid,
                            "action": gated_entry["action"] if gated_entry else "",
                        })
                logger.info("manual_check 已写出：%s（%d 条）", manual_check_path, len(sampled_ids))

    print(
        f"apply_actions 于后续任务接入——apply 清单已生成，"
        f"DB/registry 写入由 apply_actions 执行（用户确认抽检后）"
        if apply and gated_plan_entries
        else f"[{task}/{quarter}] ingest 完成"
    )

    # 报告（覆盖式，两种模式都产）
    report_path = quarter_dir / f"ingest_report_{task}.md"
    report_text = _build_report(result, task, quarter, apply, manual_check_path, apply_plan_path)
    report_path.write_text(report_text, encoding="utf-8")
    logger.info("报告已写出：%s", report_path)

    return result


def _auto_accept_summary(parsed: Any, task: str) -> str:
    """生成自动收条目的一句话摘要。"""
    if task == "T1":
        return f"same_project=False，confidence={parsed.confidence}"
    if task == "T2":
        return f"action=keep，confidence={parsed.confidence if hasattr(parsed, 'confidence') else 'n/a'}"
    return f"classification={parsed.classification}，confidence={parsed.confidence}"


def _gated_action(parsed: Any, task: str) -> str:
    """从已通过校验的 verdict 提取动作描述。"""
    if task == "T1":
        return f"merge→survivor={parsed.survivor_id}"
    if task == "T2":
        return parsed.action
    return parsed.classification


# ──────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    """构建 argparse 解析器。"""
    p = argparse.ArgumentParser(
        description="Codex 季度分拣 verdict 回收 CLI",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--task", choices=["T1", "T2", "T3"], required=True, help="任务类型")
    p.add_argument("--quarter", required=True, help="季度，格式 20xxQ[1-4]，例如 2026Q3")
    p.add_argument(
        "--verdicts",
        default=None,
        help="verdicts JSON 路径（默认 codex_review/runs/<quarter>/verdicts_<task>.json）",
    )
    p.add_argument(
        "--queue",
        default=None,
        help="队列 JSON 路径（默认 codex_review/runs/<quarter>/queue_<task>.json）",
    )
    p.add_argument("--ledger", default=str(_DEFAULT_LEDGER), help="台账 JSON 路径")
    p.add_argument("--sample-rate", type=float, default=0.15, help="改数据类抽检比例（0~1）")
    p.add_argument("--apply", action="store_true", help="实际落台账与 apply 清单（默认 dry-run）")
    p.add_argument("--out-dir", default=str(_DEFAULT_RUNS_DIR), help="输出根目录（runs/ 父目录）")
    return p


def main() -> None:
    """CLI 主入口：解析参数并调用 run_ingest。"""
    parser = _build_parser()
    args = parser.parse_args()

    _setup_logging(args.task, _LOGS_DIR)

    quarter = args.quarter
    task = args.task
    runs_dir = Path(args.out_dir)
    quarter_dir = runs_dir / quarter

    verdicts_path = (
        Path(args.verdicts)
        if args.verdicts
        else quarter_dir / f"verdicts_{task}.json"
    )
    queue_path = (
        Path(args.queue)
        if args.queue
        else quarter_dir / f"queue_{task}.json"
    )
    ledger_path = Path(args.ledger)

    try:
        run_ingest(
            task=task,
            quarter=quarter,
            verdicts_path=verdicts_path,
            queue_path=queue_path,
            ledger_path=ledger_path,
            apply=args.apply,
            sample_rate=args.sample_rate,
            runs_dir=runs_dir,
        )
    except (ValueError, FileNotFoundError) as e:
        logger.error("ingest 失败：%s", e)
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
