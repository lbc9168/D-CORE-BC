"""apply_mf_dualtrack_b.py — 双轨方案B落库：把8对同名重叠的 non_project_records 信源补挂到既有 canonical MF 事件。

背景：docs/analysis/mf_dualtrack_report_2026-07.md 方案B已由用户拍板——8条
non_project_records（record_type=新能源制造业）与既有 canonical MF 项目同名重叠，
把这些记录的信源/发布日期/实体信息补挂到对应 canonical 的最新事件，不新建任何
canonical、不建 chinese_entities 行、不动 excluded（报告方案B明示"只加信源"）。

机制（5步，逐对执行）：
1. 目标事件 = 该 canonical 的最新事件（事件 id 尾号 NN 最大者）
2. source_links 补挂：npr.source_link 若不在目标事件 source_links 中则追加，已存在跳过
3. additional_remarks 追加一行标记（原值为空/NULL 则直接设值）
4. npr 侧标记：non_project_records.merged_into_canonical 置为对应 canonical id（幂等键）
5. 不建 chinese_entities 行、不建新事件、不动 excluded

配对来源：现算自 report_mf_dualtrack.py 的 _find_overlaps（非手抄），并与本文件内嵌的
期望8对清单断言一致——多一对/少一对/任何 id 不匹配都立即中止，防止上游报告与本次
落库之间数据漂移导致误操作。

用法：
    uv run --no-sync python scripts/oneoff/apply_mf_dualtrack_b.py                # dry-run（默认）
    uv run --no-sync python scripts/oneoff/apply_mf_dualtrack_b.py --apply        # 真跑
    uv run --no-sync python scripts/oneoff/apply_mf_dualtrack_b.py --db <path> --apply
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

from pydantic import BaseModel, field_validator

# ── import 同目录 report_mf_dualtrack.py 的匹配函数（现算配对，不手抄） ──────────
_MODULE_DIR = Path(__file__).resolve().parent
if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))
from report_mf_dualtrack import (  # noqa: E402
    _connect_ro,
    _find_overlaps,
    _load_canonical_mf,
    _load_npr_mf,
)

_WORKTREE_ROOT = _MODULE_DIR.parent.parent
_LOGS_DIR = _MODULE_DIR / "logs"
_DEFAULT_DB = Path(
    r"E:\Projects\BRI_RE_Database_v2\s02a_duplicate_merger\data\db\bri_re_database.db"
)
_DEFAULT_SUPPORTING_DIR = _WORKTREE_ROOT / "supporting_docs"
_MATCH_THRESHOLD = 85  # 与报告一致（docs/analysis/mf_dualtrack_report_2026-07.md 用 85）

_MARK_DATE = "2026-07-07"

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 期望8对清单（逐字取自 docs/analysis/mf_dualtrack_report_2026-07.md §2「全部配对逐对对照」）
# 现算结果必须与此清单一致（同一组 (npr_id, canonical_id) 集合），否则立即中止。
# ──────────────────────────────────────────────

_EXPECTED_PAIRS: frozenset[tuple[int, str]] = frozenset(
    {
        (6, "EGY-MF-0001"),
        (31, "EGY-MF-0001"),
        (566, "SAU-MF-0006"),
        (756, "BRA-MF-0003"),
        (800, "MYS-MF-0011"),
        (1009, "MYS-MF-0010"),
        (126, "GBR-MF-0001"),
        (694, "MYS-MF-0016"),
    }
)


# ──────────────────────────────────────────────
# 入口参数校验（项目规则4）
# ──────────────────────────────────────────────


class ApplyParams(BaseModel):
    """apply_mf_dualtrack_b 的入口参数，含路径存在性校验。"""

    db: Path
    apply: bool
    out: Path

    @field_validator("db")
    @classmethod
    def _check_db(cls, v: Path) -> Path:
        """校验 db 路径存在。"""
        if not v.exists():
            raise ValueError(f"db 路径不存在：{v}")
        return v


# ──────────────────────────────────────────────
# 日志初始化
# ──────────────────────────────────────────────


def _setup_logging() -> None:
    """初始化 logging：同时输出 console 和带时间戳的日志文件（项目规则2）。"""
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = _LOGS_DIR / f"apply_mf_dualtrack_b_{timestamp}.log"
    fmt = "%(asctime)s [%(name)s] %(levelname)s %(message)s"
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers, force=True)
    logger.info("日志文件：%s", log_file)


# ──────────────────────────────────────────────
# 备份（复刻 codex_review/apply_actions.py 的 WAL 安全口径）
# ──────────────────────────────────────────────


def _backup_db(db_path: Path) -> str:
    """备份数据库文件，返回备份路径字符串；三重断言防止0字节备份事故。"""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dst = Path(f"{db_path}.bak.{timestamp}")

    src_conn = sqlite3.connect(str(db_path))
    dst_conn = sqlite3.connect(str(dst))
    try:
        src_conn.backup(dst_conn)
    finally:
        src_conn.close()
        dst_conn.close()

    if not dst.exists():
        raise RuntimeError(f"备份文件不存在（断言1失败）：{dst}")
    if dst.stat().st_size == 0:
        raise RuntimeError(f"备份文件为0字节（断言2失败）：{dst}")
    with sqlite3.connect(str(db_path)) as s, sqlite3.connect(str(dst)) as d:
        (src_count,) = s.execute("SELECT COUNT(*) FROM canonical_projects").fetchone()
        (dst_count,) = d.execute("SELECT COUNT(*) FROM canonical_projects").fetchone()
    if src_count != dst_count:
        raise RuntimeError(
            f"备份 canonical_projects 行数不一致（断言3失败）：源={src_count}，备份={dst_count}"
        )

    logger.info("备份完成：%s（canonical_projects=%d 行）", dst, src_count)
    return str(dst)


# ──────────────────────────────────────────────
# 配对：现算 + 断言一致
# ──────────────────────────────────────────────


def _compute_and_assert_pairs(
    conn: sqlite3.Connection,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """现算8对重叠（复用 report_mf_dualtrack._find_overlaps），与内嵌期望清单断言一致。

    返回 (matches, canonical_rows, npr_rows)；任何不一致立即抛 RuntimeError 中止。
    """
    canonical_rows = _load_canonical_mf(conn)
    npr_rows = _load_npr_mf(conn)
    matches = _find_overlaps(npr_rows, canonical_rows, _MATCH_THRESHOLD)

    computed_pairs = frozenset((m["npr_id"], m["canonical_id"]) for m in matches)
    if computed_pairs != _EXPECTED_PAIRS:
        missing = _EXPECTED_PAIRS - computed_pairs
        extra = computed_pairs - _EXPECTED_PAIRS
        raise RuntimeError(
            "现算配对与期望8对清单不一致，立即中止！"
            f"缺失={sorted(missing)}，多出={sorted(extra)}"
        )
    logger.info("现算配对与期望8对清单一致：%d 对", len(matches))
    return matches, canonical_rows, npr_rows


def _target_event(conn: sqlite3.Connection, canonical_id: str) -> dict[str, Any]:
    """取该 canonical 的最新事件（事件 id 尾号 NN 最大者），确定性规则。

    在 Python 侧按 id 末段（"-" 分隔后最后一段）转 int 排序，不依赖固定宽度 substr，
    对未来若出现 3 位数尾号（NN>=100）也不会静默取错。
    """
    cur = conn.execute(
        "SELECT id, source_links, additional_remarks FROM project_events WHERE canonical_id=?",
        (canonical_id,),
    )
    rows = cur.fetchall()
    if not rows:
        raise RuntimeError(f"canonical {canonical_id} 无任何 project_events 行，无法确定目标事件")
    best = max(rows, key=lambda r: int(r[0].rsplit("-", 1)[-1]))
    return {"id": best[0], "source_links": best[1], "additional_remarks": best[2]}


# ──────────────────────────────────────────────
# source_links 合并（JSON 数组字符串格式，见 project_events.source_links 实际取值）
# ──────────────────────────────────────────────


def _merge_source_links(existing_raw: str | None, new_link: str) -> tuple[str, bool]:
    """把 new_link 追加进 existing_raw（JSON数组字符串）；已存在则跳过。返回(新值, 是否变化)。"""
    if existing_raw:
        try:
            links: list[str] = json.loads(existing_raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"source_links 非法JSON，无法解析：{existing_raw!r}") from e
    else:
        links = []

    if new_link in links:
        return existing_raw or "[]", False

    links.append(new_link)
    return json.dumps(links, ensure_ascii=False), True


def _build_remark_line(npr: dict[str, Any]) -> str:
    """按简报格式拼装 additional_remarks 追加行。"""
    return (
        f"[双轨方案B {_MARK_DATE}] 补充信源自 non_project_records rowid={npr['id']}: "
        f"{npr['project_name']} | publish={npr.get('publish_date')} | "
        f"实体={npr.get('implementing_entity')}"
    )


def _merge_remarks(existing: str | None, new_line: str) -> str:
    """追加一行到 additional_remarks；NULL/空则直接设值，否则换行追加。"""
    if existing:
        return f"{existing}\n{new_line}"
    return new_line


# ──────────────────────────────────────────────
# 主处理：逐对计算（dry-run 与 apply 共用同一计算路径，纯读不写）
# ──────────────────────────────────────────────


def _process_pairs(
    conn: sqlite3.Connection,
    matches: list[dict[str, Any]],
    npr_rows: list[dict[str, Any]],
    canonical_rows: list[dict[str, Any]],
    *,
    has_merged_column: bool,
) -> dict[str, Any]:
    """逐对计算变更（只读 SELECT，不写库）：source_links 追加/跳过、remarks 追加、npr 标记幂等跳过。

    has_merged_column=False 时（merged_into_canonical 列尚未创建，见首次 dry-run 场景），
    视全部 npr 为未标记——跳过对该列的 SELECT，避免在列不存在时报错。

    ⚠️ 同一目标事件可能被多对命中（如 EGY-MF-0001-01 同时是 npr[6] 与 npr[31] 的目标）：
    用 _event_overlay 维护"本次运行中已计划好的最新事件状态"，后续对同一事件的追加
    在此基础上叠加，而非每次都从数据库原始值重新算起（否则后一对会覆盖前一对的追加）。

    返回 report dict，含 pairs（逐对对照+计划变更）与统计。
    """
    npr_by_id = {r["id"]: r for r in npr_rows}
    canonical_by_id = {r["id"]: r for r in canonical_rows}
    # event_id -> (source_links原始JSON字符串, additional_remarks) 的运行时叠加状态
    _event_overlay: dict[str, tuple[str | None, str | None]] = {}

    report: dict[str, Any] = {
        "pairs": [],
        "n_links_added": 0,
        "n_links_skipped_existing": 0,
        "n_remarks_added": 0,
        "n_npr_marked": 0,
        "n_npr_skipped_idempotent": 0,
    }

    for m in sorted(matches, key=lambda x: x["npr_id"]):
        npr_id = m["npr_id"]
        canonical_id = m["canonical_id"]
        npr = npr_by_id[npr_id]
        canonical = canonical_by_id[canonical_id]

        # 幂等检查：merged_into_canonical 非空则整对跳过
        already_marked_value: str | None = None
        if has_merged_column:
            cur = conn.execute(
                "SELECT merged_into_canonical FROM non_project_records WHERE id=?", (npr_id,)
            )
            row = cur.fetchone()
            already_marked_value = row[0] if row else None

        target_event = _target_event(conn, canonical_id)
        event_id = target_event["id"]

        pair_detail: dict[str, Any] = {
            "npr_id": npr_id,
            "npr_name": npr["project_name"],
            "npr_capacity_details": npr.get("capacity_details"),
            "canonical_id": canonical_id,
            "canonical_name": canonical["project_name"],
            "target_event_id": event_id,
        }

        if already_marked_value:
            pair_detail["result"] = "skipped_idempotent"
            pair_detail["merged_into_canonical_existing"] = already_marked_value
            report["n_npr_skipped_idempotent"] += 1
            report["pairs"].append(pair_detail)
            logger.info(
                "幂等跳过：npr[%d] 已标记 merged_into_canonical=%r", npr_id, already_marked_value
            )
            continue

        # 取叠加状态（若本次运行中已有更早的对写过该事件，用叠加值而非数据库原始值）
        base_links, base_remarks = _event_overlay.get(
            event_id, (target_event["source_links"], target_event["additional_remarks"])
        )

        new_link = npr.get("source_link") or ""
        new_links_json, links_changed = _merge_source_links(base_links, new_link)
        remark_line = _build_remark_line(npr)
        new_remarks = _merge_remarks(base_remarks, remark_line)

        _event_overlay[event_id] = (new_links_json, new_remarks)

        pair_detail["result"] = "would_apply"
        pair_detail["new_source_link"] = new_link
        pair_detail["link_already_present"] = not links_changed
        pair_detail["remark_line"] = remark_line
        # 内部字段：apply 阶段写库用（报告渲染时忽略下划线前缀字段）
        pair_detail["_sql_target_event_id"] = event_id
        pair_detail["_sql_new_source_links"] = new_links_json
        pair_detail["_sql_new_remarks"] = new_remarks
        pair_detail["_sql_canonical_id"] = canonical_id

        if links_changed:
            report["n_links_added"] += 1
        else:
            report["n_links_skipped_existing"] += 1
        report["n_remarks_added"] += 1
        report["n_npr_marked"] += 1

        report["pairs"].append(pair_detail)

    return report


def _apply_pairs(conn: sqlite3.Connection, process_report: dict[str, Any]) -> None:
    """把 _process_pairs 算出的变更在单事务内写库（规则3：批量写库用事务包裹）。"""
    sql_batch: list[tuple[str, tuple]] = []
    for p in process_report["pairs"]:
        if p["result"] != "would_apply":
            continue
        sql_batch.append(
            (
                "UPDATE project_events SET source_links=?, additional_remarks=? WHERE id=?",
                (p["_sql_new_source_links"], p["_sql_new_remarks"], p["_sql_target_event_id"]),
            )
        )
        sql_batch.append(
            (
                "UPDATE non_project_records SET merged_into_canonical=? WHERE id=?",
                (p["_sql_canonical_id"], p["npr_id"]),
            )
        )

    if not sql_batch:
        logger.info("无待执行变更（全部幂等跳过）")
        return

    for sql, params in sql_batch:
        conn.execute(sql, params)
    conn.commit()
    logger.info("单事务提交，共 %d 条 SQL", len(sql_batch))


# ──────────────────────────────────────────────
# ALTER TABLE：幂等加列
# ──────────────────────────────────────────────


def _has_merged_column(conn: sqlite3.Connection) -> bool:
    """探测 non_project_records 是否已有 merged_into_canonical 列（PRAGMA 只读，dry-run 安全）。"""
    cur = conn.execute("PRAGMA table_info(non_project_records)")
    return any(r[1] == "merged_into_canonical" for r in cur.fetchall())


def _ensure_merged_column(conn: sqlite3.Connection) -> None:
    """ALTER TABLE 幂等加 merged_into_canonical 列；列已存在的 OperationalError 是已知无害边界。"""
    try:
        conn.execute("ALTER TABLE non_project_records ADD COLUMN merged_into_canonical TEXT")
        conn.commit()
        logger.info("已新增列 non_project_records.merged_into_canonical")
    except sqlite3.OperationalError as e:
        # 已知无害边界：列已存在（重跑幂等），静默跳过。
        if "duplicate column name" in str(e).lower():
            logger.info("列 merged_into_canonical 已存在，跳过 ALTER TABLE")
        else:
            raise


# ──────────────────────────────────────────────
# 一致性自检：零行数变化
# ──────────────────────────────────────────────


def _row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """统计三张表行数，供 apply 前后比对（本任务零行数变化）。"""
    counts = {}
    for table in ("canonical_projects", "project_events", "chinese_entities"):
        (n,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        counts[table] = n
    return counts


# ──────────────────────────────────────────────
# 报告渲染
# ──────────────────────────────────────────────


def _render_report(
    *,
    apply_mode: bool,
    db_path: Path,
    bak_path: str | None,
    process_report: dict[str, Any],
    counts_before: dict[str, int],
    counts_after: dict[str, int] | None,
    replay_pending: int | None,
) -> str:
    """把 dry-run/apply 结果拼装为 Markdown 报告正文（逐对原文对照 + 落库统计）。"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    mode = "--apply（已执行）" if apply_mode else "dry-run（拟执行）"

    lines: list[str] = []
    lines.append("# 双轨方案B 执行报告：8对重叠补挂信源")
    lines.append("")
    lines.append(f"生成时间：{now}　运行模式：{mode}　真库：`{db_path}`")
    lines.append("")
    if bak_path:
        lines.append(f"备份路径：`{bak_path}`")
        lines.append("")

    lines.append("## 统计")
    lines.append("")
    lines.append(f"- 补挂/幂等跳过对数：{len(process_report['pairs'])}")
    lines.append(f"- source_links 新增链接数：{process_report['n_links_added']}")
    lines.append(f"- source_links 已存在跳过数：{process_report['n_links_skipped_existing']}")
    lines.append(f"- additional_remarks 追加行数：{process_report['n_remarks_added']}")
    lines.append(f"- npr 侧新标记 merged_into_canonical 行数：{process_report['n_npr_marked']}")
    lines.append(f"- npr 侧幂等跳过行数：{process_report['n_npr_skipped_idempotent']}")
    lines.append("")

    lines.append("## 逐对原文对照（人工/审查目验）")
    lines.append("")
    for p in process_report["pairs"]:
        lines.append(f"### npr[{p['npr_id']}] → {p['canonical_id']}")
        lines.append("")
        lines.append(f"- npr 项目名：`{p['npr_name']}`")
        if p.get("npr_capacity_details"):
            lines.append(f"- npr capacity_details：{p['npr_capacity_details']}")
        lines.append(f"- canonical 项目名：`{p['canonical_name']}`")
        lines.append(f"- 目标事件：`{p['target_event_id']}`")
        lines.append(f"- 结果：{p['result']}")
        if p["result"] == "would_apply":
            lines.append(f"- 新增 source_link：{p['new_source_link']}"
                          f"（{'已存在，未新增' if p['link_already_present'] else '本次新增'}）")
            lines.append(f"- 追加 remark：{p['remark_line']}")
        elif p["result"] == "skipped_idempotent":
            lines.append(f"- 已标记：merged_into_canonical={p.get('merged_into_canonical_existing')}")
        lines.append("")

    lines.append("## 行数一致性自检（本任务零行数变化）")
    lines.append("")
    lines.append("| 表 | apply前 | apply后 |")
    lines.append("|---|---|---|")
    for table in ("canonical_projects", "project_events", "chinese_entities"):
        after_val = counts_after[table] if counts_after else "-"
        lines.append(f"| {table} | {counts_before[table]} | {after_val} |")
    lines.append("")

    if replay_pending is not None:
        lines.append("## 重放自检（apply 后再跑一遍 dry-run 应报0待处理）")
        lines.append("")
        status = "通过 ✓" if replay_pending == 0 else f"**失败** ✗（仍有 {replay_pending} 对待处理）"
        lines.append(f"重放 pending={replay_pending}：{status}")
        lines.append("")

    return "\n".join(lines)


# ──────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────


def run(params: ApplyParams) -> dict[str, Any]:
    """主流程：现算配对断言 → 逐对计算(dry-run) → apply 时备份+加列+写库+重放自检 → 返回结果。"""
    ro_conn = _connect_ro(params.db)
    try:
        matches, canonical_rows, npr_rows = _compute_and_assert_pairs(ro_conn)
        counts_before = _row_counts(ro_conn)
        has_column = _has_merged_column(ro_conn)
        process_report = _process_pairs(
            ro_conn, matches, npr_rows, canonical_rows, has_merged_column=has_column
        )
    finally:
        ro_conn.close()

    bak_path: str | None = None
    counts_after: dict[str, int] | None = None
    replay_pending: int | None = None

    if params.apply:
        bak_path = _backup_db(params.db)
        conn = sqlite3.connect(str(params.db), timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            _ensure_merged_column(conn)
            # 加列后重新计算一遍（此时 has_merged_column 恒为 True），保证写库用的
            # process_report 与加列后的库状态一致（加列前 dry-run 算的 report 不含该列判断）。
            process_report = _process_pairs(
                conn, matches, npr_rows, canonical_rows, has_merged_column=True
            )
            _apply_pairs(conn, process_report)
            counts_after = _row_counts(conn)
        finally:
            conn.close()

        # 重放自检：apply 后再跑一遍只读计算，应全部 skipped_idempotent（pending=0）
        ro_conn2 = _connect_ro(params.db)
        try:
            matches2, canonical_rows2, npr_rows2 = _compute_and_assert_pairs(ro_conn2)
            replay_report = _process_pairs(
                ro_conn2, matches2, npr_rows2, canonical_rows2, has_merged_column=True
            )
            replay_pending = replay_report["n_npr_marked"]
        finally:
            ro_conn2.close()

        if counts_before != counts_after:
            raise RuntimeError(
                f"行数一致性自检失败（本任务应零行数变化）：before={counts_before}, after={counts_after}"
            )
        if replay_pending != 0:
            raise RuntimeError(f"重放自检失败：apply 后仍有 {replay_pending} 对待处理（应为0）")

    return {
        "apply_mode": params.apply,
        "bak_path": bak_path,
        "process_report": process_report,
        "counts_before": counts_before,
        "counts_after": counts_after,
        "replay_pending": replay_pending,
    }


# ──────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────


def main() -> None:
    """CLI 主入口：解析参数 → 校验 → run() → 落报告 MD。"""
    _setup_logging()
    parser = argparse.ArgumentParser(description="双轨方案B落库：8对重叠补挂信源")
    parser.add_argument("--db", default=str(_DEFAULT_DB), help="真库路径")
    parser.add_argument("--apply", action="store_true", help="实际执行（默认 dry-run）")
    parser.add_argument("--out", default=None, help="报告 MD 输出路径（默认 supporting_docs 带时戳）")
    args = parser.parse_args()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    default_out = _DEFAULT_SUPPORTING_DIR / f"mf_dualtrack_b_apply_report_{timestamp}.md"
    out_path = Path(args.out) if args.out else default_out

    try:
        params = ApplyParams(db=Path(args.db), apply=args.apply, out=out_path)
    except ValueError as e:
        logger.error("参数校验失败: %s", e)
        sys.exit(1)

    try:
        result = run(params)
    except RuntimeError as e:
        logger.error("执行失败: %s", e)
        sys.exit(1)

    report_text = _render_report(
        apply_mode=result["apply_mode"],
        db_path=params.db,
        bak_path=result["bak_path"],
        process_report=result["process_report"],
        counts_before=result["counts_before"],
        counts_after=result["counts_after"],
        replay_pending=result["replay_pending"],
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report_text, encoding="utf-8")
    logger.info("报告已写入：%s", out_path)

    pr = result["process_report"]
    print(
        f"[{'APPLY' if result['apply_mode'] else 'DRY-RUN'}] "
        f"补挂/幂等对数={len(pr['pairs'])} "
        f"链接新增={pr['n_links_added']} "
        f"npr标记={pr['n_npr_marked']} "
        f"幂等跳过={pr['n_npr_skipped_idempotent']}"
    )


if __name__ == "__main__":
    main()
