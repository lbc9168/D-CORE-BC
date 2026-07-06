"""Codex 季度分拣 apply 执行层：T1 merge / T2 exclude / T3 registry 补录产物。

用法：
    uv run python -m codex_review.apply_actions --task {T1,T2,T3} --quarter 2026Q3 [--db PATH] [--plan PATH] [--ledger PATH] [--apply]
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError, field_validator

from codex_review.export_queue import _DEFAULT_DB
from codex_review.ledger import load_ledger, record, save_ledger, t2_key

# ──────────────────────────────────────────────
# 常量与默认路径
# ──────────────────────────────────────────────

_MODULE_DIR = Path(__file__).resolve().parent
_LOGS_DIR = _MODULE_DIR / "logs"
_DEFAULT_RUNS_DIR = _MODULE_DIR / "runs"
_DEFAULT_LEDGER = _MODULE_DIR / "ledger.json"

_QUARTER_RE = __import__("re").compile(r"^20\d{2}Q[1-4]$")

logger = logging.getLogger(__name__)

# T3 分类中需写进 new_groups 的（其余写进 additions_to_existing_groups）
_T3_NEW_GROUP_CLASSIFICATIONS = frozenset(["new_group", "subsidiary_of_new"])
_T3_EXISTING_GROUP_CLASSIFICATIONS = frozenset(
    ["alias_of_existing", "subsidiary_of_existing"]
)


# ──────────────────────────────────────────────
# 入口参数校验（项目规则 4）
# ──────────────────────────────────────────────


class ApplyParams(BaseModel):
    """apply_actions 的入口参数，含格式与路径存在性校验。"""

    task: str
    quarter: str
    db_path: Path
    plan_path: Path
    ledger_path: Path
    runs_dir: Path

    @field_validator("quarter")
    @classmethod
    def _check_quarter(cls, v: str) -> str:
        """校验 quarter 格式为 20xxQ[1-4]。"""
        if not _QUARTER_RE.match(v):
            raise ValueError(f"quarter 格式非法，期望 20xxQ[1-4]，收到 {v!r}")
        return v

    @field_validator("task")
    @classmethod
    def _check_task(cls, v: str) -> str:
        """校验 task 为 T1/T2/T3 之一。"""
        if v not in ("T1", "T2", "T3"):
            raise ValueError(f"task 必须为 T1/T2/T3，收到 {v!r}")
        return v

    @field_validator("plan_path")
    @classmethod
    def _check_plan_exists(cls, v: Path) -> Path:
        """校验 plan_path 存在（不存在提示先跑 ingest --apply）。"""
        if not v.exists():
            raise ValueError(
                f"apply_plan 文件不存在：{v}。请先运行 ingest_verdicts --apply 生成该文件。"
            )
        return v

    @field_validator("db_path")
    @classmethod
    def _check_db_exists(cls, v: Path) -> Path:
        """校验数据库文件存在。"""
        if not v.exists():
            raise ValueError(f"数据库文件不存在：{v}")
        return v


# ──────────────────────────────────────────────
# 日志初始化
# ──────────────────────────────────────────────


def _setup_logging(task: str, logs_dir: Path) -> None:
    """初始化 logging：同时输出 console 和带时间戳的日志文件。"""
    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"apply_{task}_{timestamp}.log"

    fmt = "%(asctime)s [%(name)s] %(levelname)s %(message)s"
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers, force=True)
    logger.info("日志文件：%s", log_file)


def _connect_check(db_path: Path, apply: bool) -> sqlite3.Connection:
    """干运行/重放检查用只读连接（mode=ro 防意外写入），apply 用普通读写连接。"""
    if not apply:
        return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    return sqlite3.connect(str(db_path))


# ──────────────────────────────────────────────
# 备份（复刻 s02b/db_backup.py 的 WAL 安全口径；该包不在根 venv 故内联）
# ──────────────────────────────────────────────


def _backup_db(db_path: Path) -> str:
    """备份数据库文件，返回备份路径字符串；三重断言防止 0 字节备份事故。"""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dst = Path(f"{db_path}.bak.{timestamp}")

    # WAL 安全备份（sqlite3 原生 backup API）
    src_conn = sqlite3.connect(str(db_path))
    dst_conn = sqlite3.connect(str(dst))
    try:
        src_conn.backup(dst_conn)
    finally:
        src_conn.close()
        dst_conn.close()

    # 三重断言
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
    return str(dst)


# ──────────────────────────────────────────────
# 外键 + 前缀一致性自检
# ──────────────────────────────────────────────


def _integrity_check(db_path: Path) -> None:
    """外键一致性与事件 id 前缀一致性自检；失败抛 RuntimeError。"""
    with sqlite3.connect(str(db_path)) as conn:
        # 外键检查1：chinese_entities.event_id 必须都在 project_events
        (orphan_entities,) = conn.execute(
            "SELECT COUNT(*) FROM chinese_entities WHERE event_id NOT IN (SELECT id FROM project_events)"
        ).fetchone()
        if orphan_entities != 0:
            raise RuntimeError(
                f"外键自检失败：{orphan_entities} 条 chinese_entities.event_id 不在 project_events"
            )

        # 外键检查2：project_events.canonical_id 必须都在 canonical_projects
        (orphan_events,) = conn.execute(
            "SELECT COUNT(*) FROM project_events WHERE canonical_id NOT IN (SELECT id FROM canonical_projects)"
        ).fetchone()
        if orphan_events != 0:
            raise RuntimeError(
                f"外键自检失败：{orphan_events} 条 project_events.canonical_id 不在 canonical_projects"
            )

        # 前缀一致性：project_events.id 去掉末尾 -NN 应等于 canonical_id
        bad_prefix = conn.execute(
            """
            SELECT id, canonical_id FROM project_events
            WHERE substr(id, 1, length(id) - 3) != canonical_id
            """
        ).fetchall()
        if bad_prefix:
            bad_str = ", ".join(f"{r[0]}->{r[1]}" for r in bad_prefix[:5])
            raise RuntimeError(f"事件 id 前缀不一致：{bad_str}")

    logger.info("外键自检通过")


# ──────────────────────────────────────────────
# 重放检查（幂等验证第三项）
# ──────────────────────────────────────────────


def _replay_check(
    task: str,
    plan_path: Path,
    db_path: Path,
    ledger_path: Path,
    quarter: str,
) -> None:
    """重放检查：对同一 plan 再算一遍待执行集，断言 done==0（全部命中幂等/冲突跳过）；失败抛 RuntimeError。"""
    # 复用与执行前相同的计算路径（apply=False），不写库
    if task == "T1":
        replay_report = apply_t1(
            plan_path=plan_path,
            db_path=db_path,
            ledger_path=ledger_path,
            quarter=quarter,
            apply=False,
        )
    elif task == "T2":
        replay_report = apply_t2(
            plan_path=plan_path,
            db_path=db_path,
            ledger_path=ledger_path,
            quarter=quarter,
            apply=False,
        )
    else:
        # T3 不动库，重放无意义，视为通过
        logger.info("重放检查：T3 不动库，跳过重放")
        return

    pending = replay_report.get("done", 0)
    if pending != 0:
        raise RuntimeError(
            f"重放检查失败：apply 完成后仍有 {pending} 条待执行动作（应为 0），"
            f"说明本次 apply 可能不完整或存在一致性问题，请人工复查"
        )
    logger.info("重放检查通过：apply 后 pending=0")


# ──────────────────────────────────────────────
# T1 merge
# ──────────────────────────────────────────────


def apply_t1(
    *,
    plan_path: Path,
    db_path: Path,
    ledger_path: Path,
    quarter: str,
    apply: bool,
) -> dict[str, Any]:
    """执行 T1 merge：事件迁挂+重编 id+外键同步+loser 标记；返回统计 dict。"""
    entries: list[dict] = json.loads(plan_path.read_text(encoding="utf-8"))

    report: dict[str, Any] = {
        "done": 0,
        "skipped_idempotent": 0,
        "conflict_skipped": 0,
        "error": 0,
        "details": [],
    }

    if not entries:
        return report

    # ── 冲突预检：找出在多条 merge 里出现多次的 canonical_id ──
    id_to_pairs: dict[str, list[str]] = {}  # canonical_id → [pair_id, ...]
    for entry in entries:
        verdict = entry["verdict"]
        if not verdict.get("same_project"):
            continue
        pair_id: str = verdict["pair_id"]
        context = entry["context"]
        for cid in [context.get("a_canonical_id"), context.get("b_canonical_id")]:
            if cid:
                id_to_pairs.setdefault(cid, []).append(pair_id)

    # 有同一 canonical_id 出现在多对里的 pair_id 集合 → 全部 conflict_skipped
    conflict_pair_ids: set[str] = set()
    for cid, pairs in id_to_pairs.items():
        if len(pairs) > 1:
            conflict_pair_ids.update(pairs)

    if conflict_pair_ids:
        logger.warning("冲突预检发现 %d 对涉及链式/星型 merge，全部 conflict_skipped：%s",
                       len(conflict_pair_ids), conflict_pair_ids)

    ledger = load_ledger(ledger_path)

    # 收集所有 SQL 变更，统一在单事务内执行（规则 3）
    sql_batch: list[tuple[str, tuple]] = []  # [(sql, params), ...]
    ledger_records: list[dict] = []

    for entry in entries:
        verdict = entry["verdict"]
        if not verdict.get("same_project"):
            continue  # same_project=False 已在 ingest 自动收

        pair_id = verdict["pair_id"]
        survivor_id: str = verdict["survivor_id"]
        context = entry["context"]
        a_id = context.get("a_canonical_id", "")
        b_id = context.get("b_canonical_id", "")

        # survivor_id 必须属于 pair 双方
        if survivor_id not in {a_id, b_id}:
            logger.error(
                "survivor_id 不属于 pair 双方，pair_id=%r，survivor=%r，a=%r，b=%r",
                pair_id, survivor_id, a_id, b_id,
            )
            report["conflict_skipped"] += 1
            report["details"].append({"pair_id": pair_id, "result": "conflict_skipped", "reason": "survivor_not_in_pair"})
            continue

        if pair_id in conflict_pair_ids:
            report["conflict_skipped"] += 1
            report["details"].append({"pair_id": pair_id, "result": "conflict_skipped", "reason": "chain_conflict"})
            continue

        loser_id = b_id if survivor_id == a_id else a_id

        # 幂等检查：loser 已是 merged_into:<S>
        with _connect_check(db_path, apply) as check_conn:
            row = check_conn.execute(
                "SELECT excluded, excluded_reason FROM canonical_projects WHERE id=?",
                (loser_id,),
            ).fetchone()
        if row is None:
            logger.error("loser 不存在于 canonical_projects，id=%r", loser_id)
            report["error"] += 1
            report["details"].append({"pair_id": pair_id, "result": "error", "reason": f"loser_not_found:{loser_id}"})
            continue

        excl, excl_reason = row
        if excl == 1 and excl_reason and excl_reason.startswith("merged_into:"):
            report["skipped_idempotent"] += 1
            report["details"].append({"pair_id": pair_id, "result": "skipped_idempotent"})
            logger.info("幂等跳过（loser 已 merged）：pair_id=%r", pair_id)
            continue

        # survivor 是否已被 merge 进别人
        with _connect_check(db_path, apply) as check_conn:
            s_row = check_conn.execute(
                "SELECT excluded, excluded_reason FROM canonical_projects WHERE id=?",
                (survivor_id,),
            ).fetchone()
        if s_row and s_row[0] == 1 and s_row[1] and s_row[1].startswith("merged_into:"):
            logger.error(
                "survivor 已被合并进其他项目，survivor_id=%r，reason=%r，pair_id=%r",
                survivor_id, s_row[1], pair_id,
            )
            report["conflict_skipped"] += 1
            report["details"].append({"pair_id": pair_id, "result": "conflict_skipped", "reason": "survivor_already_merged"})
            continue

        if not apply:
            # dry-run：只计算，不写
            report["done"] += 1
            report["details"].append({"pair_id": pair_id, "result": "would_merge"})
            continue

        # ── 计算 survivor 现有最大 NN ──
        with sqlite3.connect(str(db_path)) as check_conn:
            loser_events = check_conn.execute(
                "SELECT id FROM project_events WHERE canonical_id=? ORDER BY id", (loser_id,)
            ).fetchall()
            s_events = check_conn.execute(
                "SELECT id FROM project_events WHERE canonical_id=? ORDER BY id", (survivor_id,)
            ).fetchall()

        max_nn = 0
        for (eid,) in s_events:
            nn_str = eid.rsplit("-", 1)[-1]
            if nn_str.isdigit():
                max_nn = max(max_nn, int(nn_str))

        if max_nn + len(loser_events) > 99:
            raise RuntimeError(
                f"NN 超 99（两位编号库约定），survivor={survivor_id}，"
                f"现有={max_nn}，loser 事件数={len(loser_events)}，需人工处理"
            )

        # ── 构造 loser 事件的 id 重映射 ──
        remap: list[tuple[str, str]] = []  # [(old_id, new_id), ...]
        for i, (old_id,) in enumerate(loser_events, start=max_nn + 1):
            new_id = f"{survivor_id}-{i:02d}"
            remap.append((old_id, new_id))

        # ── 将本次操作加入 sql_batch（单事务） ──
        for old_id, new_id in remap:
            # 更新 project_events
            sql_batch.append(
                ("UPDATE project_events SET id=?, canonical_id=? WHERE id=?",
                 (new_id, survivor_id, old_id))
            )
            # 同步 chinese_entities
            sql_batch.append(
                ("UPDATE chinese_entities SET event_id=? WHERE event_id=?",
                 (new_id, old_id))
            )

        # loser → excluded
        sql_batch.append(
            ("UPDATE canonical_projects SET excluded=1, excluded_reason=? WHERE id=?",
             (f"merged_into:{survivor_id}", loser_id))
        )

        # 台账记录（apply 时写）
        ledger_records.append({
            "key": pair_id,
            "task": "T1",
            "quarter": quarter,
            "item_id": pair_id,
            "status": "applied",
            "verdict_digest": "",
        })

        report["done"] += 1
        report["details"].append({
            "pair_id": pair_id,
            "result": "done",
            "survivor": survivor_id,
            "loser": loser_id,
            "events_moved": len(remap),
        })
        logger.info(
            "T1 merge 计划：pair_id=%r，survivor=%r，loser=%r，事件数=%d",
            pair_id, survivor_id, loser_id, len(remap),
        )

    # ── 单事务执行全部 SQL（规则 3） ──
    if apply and sql_batch:
        with sqlite3.connect(str(db_path)) as conn:
            for sql, params in sql_batch:
                conn.execute(sql, params)
            conn.commit()
        logger.info("T1 单事务提交，共 %d 条 SQL", len(sql_batch))

        # 写台账
        for lr in ledger_records:
            record(
                ledger,
                lr["key"],
                task=lr["task"],
                quarter=lr["quarter"],
                item_id=lr["item_id"],
                status=lr["status"],  # type: ignore[arg-type]
                verdict_digest=lr["verdict_digest"],
            )
        save_ledger(ledger_path, ledger)

    return report


# ──────────────────────────────────────────────
# T2 exclude / downgrade
# ──────────────────────────────────────────────


def apply_t2(
    *,
    plan_path: Path,
    db_path: Path,
    ledger_path: Path,
    quarter: str,
    apply: bool,
) -> dict[str, Any]:
    """执行 T2 exclude/downgrade：置排除标记；返回统计 dict。"""
    entries: list[dict] = json.loads(plan_path.read_text(encoding="utf-8"))

    report: dict[str, Any] = {
        "done": 0,
        "skipped_idempotent": 0,
        "error": 0,
        "details": [],
    }

    ledger = load_ledger(ledger_path)
    sql_batch: list[tuple[str, tuple]] = []
    ledger_records: list[dict] = []

    for entry in entries:
        verdict = entry["verdict"]
        project_id: str = verdict["project_id"]
        action: str = verdict["action"]
        trigger: str = (entry.get("context", {}).get("trigger") or "fixed_list")

        lkey = t2_key(project_id, trigger)

        # 幂等检查
        with _connect_check(db_path, apply) as check_conn:
            row = check_conn.execute(
                "SELECT excluded FROM canonical_projects WHERE id=?", (project_id,)
            ).fetchone()

        if row is None:
            logger.error("T2 project_id 不在库，id=%r", project_id)
            report["error"] += 1
            report["details"].append({"project_id": project_id, "result": "error", "reason": "not_found"})
            continue

        if row[0] == 1:
            report["skipped_idempotent"] += 1
            report["details"].append({"project_id": project_id, "result": "skipped_idempotent"})
            logger.info("T2 幂等跳过（已 excluded）：project_id=%r", project_id)
            continue

        if not apply:
            report["done"] += 1
            report["details"].append({"project_id": project_id, "result": "would_exclude", "action": action})
            continue

        # 确定 excluded_reason
        if action == "exclude":
            reason = f"codex_T2_exclude:{quarter}"
        else:  # downgrade_non_project
            reason = f"codex_T2_non_project:{quarter}"

        sql_batch.append(
            ("UPDATE canonical_projects SET excluded=1, excluded_reason=? WHERE id=?",
             (reason, project_id))
        )

        ledger_records.append({
            "key": lkey,
            "task": "T2",
            "quarter": quarter,
            "item_id": project_id,
            "status": "applied",
            "verdict_digest": "",
        })

        report["done"] += 1
        report["details"].append({"project_id": project_id, "result": "done", "action": action})
        logger.info("T2 计划：project_id=%r，action=%r", project_id, action)

    # 单事务执行
    if apply and sql_batch:
        with sqlite3.connect(str(db_path)) as conn:
            for sql, params in sql_batch:
                conn.execute(sql, params)
            conn.commit()
        logger.info("T2 单事务提交，共 %d 条 SQL", len(sql_batch))

        for lr in ledger_records:
            record(
                ledger,
                lr["key"],
                task=lr["task"],
                quarter=lr["quarter"],
                item_id=lr["item_id"],
                status=lr["status"],  # type: ignore[arg-type]
                verdict_digest=lr["verdict_digest"],
            )
        save_ledger(ledger_path, ledger)

    return report


# ──────────────────────────────────────────────
# T3 registry 补录（不动库，不动台账，不写 draft JSON）
# 幂等说明：T3 不占台账——export 端的 registry 覆盖过滤保证已入 registry 的名字不再入队，天然幂等。
# ──────────────────────────────────────────────


def apply_t3(
    *,
    plan_path: Path,
    db_path: Path,
    ledger_path: Path,
    quarter: str,
    apply: bool,
    runs_dir: Path | None = None,
) -> dict[str, Any]:
    """生成 registry_additions_<quarter>.json（不动库，不写 draft，不占台账）；返回统计 dict。"""
    if runs_dir is None:
        runs_dir = _DEFAULT_RUNS_DIR

    entries: list[dict] = json.loads(plan_path.read_text(encoding="utf-8"))

    report: dict[str, Any] = {"done": 0, "dry_run_only": 0, "details": []}

    # 格式对齐 company_hierarchy_draft_2026-07-05.json 的条目形状
    new_groups: list[dict] = []
    additions_to_existing: list[dict] = []

    for entry in entries:
        verdict = entry["verdict"]
        raw_name: str = verdict["raw_name"]
        classification: str = verdict["classification"]
        canonical_group_cn: str | None = verdict.get("canonical_group_cn")
        legal_full_name_cn: str | None = verdict.get("legal_full_name_cn")
        is_central_soe: bool | None = verdict.get("is_central_soe")
        source_urls: list[str] = verdict.get("source_urls", [])
        notes: str = verdict.get("notes", "")
        parent_cn: str | None = verdict.get("parent_cn")

        if classification in _T3_NEW_GROUP_CLASSIFICATIONS:
            # 新集团或新集团的子公司 → new_groups
            new_entry: dict = {
                "canonical_name_cn": canonical_group_cn or raw_name,
                "is_central_soe": is_central_soe,
                "provenance": f"codex_T3_{quarter}",
                "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "aliases": [],
                "subsidiaries": [],
                "source_urls": source_urls,
                "notes": notes,
            }
            if classification == "subsidiary_of_new" and parent_cn:
                new_entry["parent_cn"] = parent_cn
            if legal_full_name_cn:
                new_entry["legal_full_name_cn"] = legal_full_name_cn
            new_groups.append(new_entry)

        elif classification in _T3_EXISTING_GROUP_CLASSIFICATIONS:
            # alias 或已有集团的子公司 → additions_to_existing_groups
            add_entry: dict = {
                "canonical_name_cn": canonical_group_cn,
                "addition_type": classification,
                "raw_name": raw_name,
                "legal_full_name_cn": legal_full_name_cn,
                "is_central_soe": is_central_soe,
                "source_urls": source_urls,
                "notes": notes,
            }
            additions_to_existing.append(add_entry)

        else:
            # not_chinese / not_company / uncertain — 不入 additions
            logger.info("T3 跳过（classification=%r）：raw_name=%r", classification, raw_name)
            continue

        report["done"] += 1
        report["details"].append({"raw_name": raw_name, "result": "done" if apply else "would_add"})
        logger.info("T3 条目：raw_name=%r，classification=%r", raw_name, classification)

    if not apply:
        # dry-run：不落文件
        report["dry_run_only"] = report["done"]
        report["done"] = 0
        return report

    # 落 additions 文件
    quarter_dir = runs_dir / quarter
    quarter_dir.mkdir(parents=True, exist_ok=True)
    additions_path = quarter_dir / f"registry_additions_{quarter}.json"

    additions_data: dict = {
        "generated_on": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "quarter": quarter,
        "notes": [
            "人工三步：①把 additions 合并进 company_hierarchy_draft_*.json（用户在编辑，不代劳）",
            "②重跑 uv run supporting_docs/build_hierarchy_v2.py",
            "③在 s01b 跑 entity_reparse_maindb --apply（主库 gated）",
        ],
        "new_groups": new_groups,
        "additions_to_existing_groups": additions_to_existing,
    }
    additions_path.write_text(
        json.dumps(additions_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("T3 additions 文件已写出：%s（new_groups=%d，additions_to_existing=%d）",
                additions_path, len(new_groups), len(additions_to_existing))

    return report


# ──────────────────────────────────────────────
# apply 报告生成
# ──────────────────────────────────────────────


def _write_apply_report(
    task: str,
    quarter: str,
    apply: bool,
    bak_path: str | None,
    report: dict[str, Any],
    runs_dir: Path,
    integrity_ok: bool = True,
    integrity_error: str = "",
    replay_ok: bool = True,
    replay_error: str = "",
) -> Path:
    """生成 apply_report_<task>.md（覆盖式），返回报告路径。"""
    quarter_dir = runs_dir / quarter
    quarter_dir.mkdir(parents=True, exist_ok=True)
    report_path = quarter_dir / f"apply_report_{task}.md"

    mode = "--apply（已执行）" if apply else "dry-run（拟执行）"
    lines: list[str] = []
    lines.append(f"# Apply 报告：{task} / {quarter}\n")
    lines.append(f"运行模式：{mode}\n")

    if bak_path:
        lines.append(f"备份路径：`{bak_path}`\n")

    lines.append("\n## 统计")
    for k, v in report.items():
        if k != "details" and isinstance(v, int):
            lines.append(f"- {k}: {v}")

    if report.get("details"):
        lines.append("\n## 明细")
        lines.append("| id | 结果 | 备注 |")
        lines.append("| --- | --- | --- |")
        for d in report["details"]:
            did = d.get("pair_id") or d.get("project_id") or d.get("raw_name", "")
            result = d.get("result", "")
            reason = d.get("reason") or d.get("action") or ""
            lines.append(f"| {did} | {result} | {reason} |")

    lines.append("\n## 幂等自检")
    if not apply:
        lines.append("dry-run 模式，未执行自检。")
    else:
        lines.append(f"外键+前缀一致性：{'通过 ✓' if integrity_ok else f'**失败** ✗：{integrity_error}'}")
        lines.append(f"重放检查（pending=0）：{'通过 ✓' if replay_ok else f'**失败** ✗：{replay_error}'}")

    if task == "T3":
        lines.append("\n## T3 人工三步提示")
        lines.append("1. 把 `registry_additions_{quarter}.json` 合并进 `company_hierarchy_draft_*.json`（用户在编辑，不代劳）")
        lines.append(f"2. 重跑 `uv run supporting_docs/build_hierarchy_v2.py`")
        lines.append(f"3. 在 s01b 跑 `entity_reparse_maindb --apply`（主库 gated）")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("报告已写出：%s", report_path)
    return report_path


# ──────────────────────────────────────────────
# 主入口（供外部调用 + ingest_verdicts 接缝）
# ──────────────────────────────────────────────


def run_apply(
    task: str,
    quarter: str,
    db_path: Path,
    plan_path: Path,
    ledger_path: Path,
    *,
    apply: bool = False,
    runs_dir: Path | None = None,
) -> dict[str, Any]:
    """执行 apply_actions 主流程；返回统计 report dict。"""
    if runs_dir is None:
        runs_dir = _DEFAULT_RUNS_DIR

    # 入口参数校验（规则 4）
    try:
        ApplyParams(
            task=task,
            quarter=quarter,
            db_path=db_path,
            plan_path=plan_path,
            ledger_path=ledger_path,
            runs_dir=runs_dir,
        )
    except ValidationError as e:
        logger.error("入口参数校验失败：%s", e)
        raise ValueError(str(e)) from e

    bak_path: str | None = None

    # --apply 时动库任务先备份
    if apply and task in ("T1", "T2"):
        bak_path = _backup_db(db_path)

    # 执行各任务
    if task == "T1":
        report = apply_t1(
            plan_path=plan_path,
            db_path=db_path,
            ledger_path=ledger_path,
            quarter=quarter,
            apply=apply,
        )
    elif task == "T2":
        report = apply_t2(
            plan_path=plan_path,
            db_path=db_path,
            ledger_path=ledger_path,
            quarter=quarter,
            apply=apply,
        )
    else:  # T3
        report = apply_t3(
            plan_path=plan_path,
            db_path=db_path,
            ledger_path=ledger_path,
            quarter=quarter,
            apply=apply,
            runs_dir=runs_dir,
        )

    # 幂等自检（--apply 后）：外键+前缀一致性
    integrity_ok = True
    integrity_error = ""
    if apply and task in ("T1", "T2"):
        try:
            _integrity_check(db_path)
        except RuntimeError as e:
            integrity_ok = False
            integrity_error = str(e)
            logger.error("幂等自检失败：%s", e)

    # 重放检查（--apply 后）：对同一 plan 重算待执行集应为空
    replay_ok = True
    replay_error = ""
    if apply:
        try:
            _replay_check(
                task=task,
                plan_path=plan_path,
                db_path=db_path,
                ledger_path=ledger_path,
                quarter=quarter,
            )
        except RuntimeError as e:
            replay_ok = False
            replay_error = str(e)
            logger.error("重放检查失败：%s", e)

    _write_apply_report(
        task=task,
        quarter=quarter,
        apply=apply,
        bak_path=bak_path,
        report=report,
        runs_dir=runs_dir,
        integrity_ok=integrity_ok,
        integrity_error=integrity_error,
        replay_ok=replay_ok,
        replay_error=replay_error,
    )

    if apply and (not integrity_ok or not replay_ok):
        sys.exit(1)

    return report


# ──────────────────────────────────────────────
# ingest_verdicts 占位接缝
# ──────────────────────────────────────────────


def execute_apply_plan(task: str, plan_path: Path, **kwargs: Any) -> None:
    """供 ingest_verdicts._execute_apply_plan 调用的接缝（接管原 NotImplementedError 占位）。"""
    quarter: str = kwargs.get("quarter", "")
    db_path: Path = kwargs.get("db_path", Path(_DEFAULT_DB))
    ledger_path: Path = kwargs.get("ledger_path", _DEFAULT_LEDGER)
    apply_flag: bool = kwargs.get("apply", True)
    runs_dir: Path | None = kwargs.get("runs_dir", None)

    run_apply(
        task=task,
        quarter=quarter,
        db_path=db_path,
        plan_path=plan_path,
        ledger_path=ledger_path,
        apply=apply_flag,
        runs_dir=runs_dir,
    )


# ──────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    """构建 argparse 解析器。"""
    p = argparse.ArgumentParser(
        description="Codex 季度分拣 apply 执行层",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--task", choices=["T1", "T2", "T3"], required=True)
    p.add_argument("--quarter", required=True, help="季度，格式 20xxQ[1-4]")
    p.add_argument("--db", default=str(_DEFAULT_DB), help="主库路径")
    p.add_argument("--plan", default=None, help="apply_plan JSON 路径（默认 runs/<quarter>/apply_plan_<task>.json）")
    p.add_argument("--ledger", default=str(_DEFAULT_LEDGER), help="台账 JSON 路径")
    p.add_argument("--out-dir", default=str(_DEFAULT_RUNS_DIR), help="输出根目录（runs/ 父目录）")
    p.add_argument("--apply", action="store_true", help="实际执行（默认 dry-run）")
    return p


def main() -> None:
    """CLI 主入口：解析参数并调用 run_apply。"""
    parser = _build_parser()
    args = parser.parse_args()

    _setup_logging(args.task, _LOGS_DIR)

    runs_dir = Path(args.out_dir)
    quarter_dir = runs_dir / args.quarter
    plan_path = (
        Path(args.plan)
        if args.plan
        else quarter_dir / f"apply_plan_{args.task}.json"
    )

    try:
        run_apply(
            task=args.task,
            quarter=args.quarter,
            db_path=Path(args.db),
            plan_path=plan_path,
            ledger_path=Path(args.ledger),
            apply=args.apply,
            runs_dir=runs_dir,
        )
    except (ValueError, FileNotFoundError) as e:
        logger.error("apply_actions 失败：%s", e)
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
