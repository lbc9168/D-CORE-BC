"""replay_c2_would_change.py — 一次性：把 C2 dry-run 报告的 would_change 确定性回放到 extracted_projects。

为什么需要：C2 全量复核（2026-06-03，报告 review/reports/20260603_005742.json）已 dry-run 跑完并花掉
critic LLM 钱。真跑落库若直接去掉 --dry-run 重跑 run_review，会重复花钱 + LLM 非确定（可能这次判得不同）
+ 队列重复入队（queue.add_item 无去重）。本脚本直接重放报告里 critic 已判定的高置信改动（auto_corrected
字段纠正 + auto_excluded 排除），完全不调用 LLM、确定性、可重复执行（SET 到固定值，重跑无副作用）。

⚠️ 一次性脚本：只用于 C2 这次过渡。日常增量数据的"应用复核结论"由 review.run_review（非 --dry-run）
在流水线内联完成（update_field/exclude_field 写库），不用本脚本。

SQL 与 review/run_review.py 的 _make_update_field / _make_exclude_field 保持一致，保证回放结果
等同于一次真实的非 dry-run 运行（capacity_mw 的 *_raw 列见下方 NOTE）。

用法（在主工作区，真实 projects.db 所在处；worktree 的 projects.db 是空壳）：
    uv run python scripts/oneoff/replay_c2_would_change.py --report review/reports/20260603_005742.json --dry-run
    uv run python scripts/oneoff/replay_c2_would_change.py --report review/reports/20260603_005742.json
        # 真跑：执行前自动 .bak 备份 projects.db
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import shutil
import sqlite3
import sys
from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator

# ── 仓库根上溯（含 bri_enums.py），复用数值解析单一可信源 ──────────────────────
_root = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
import bri_parse  # noqa: E402

logger = logging.getLogger(__name__)

# 字段白名单：field → 数据库列名字面量（与 run_review._UPDATABLE 一致；SQL 只用 dict value，防注入）
_UPDATABLE: dict[str, str] = {
    "category": "category",
    "capacity_mw": "capacity_mw",
    "involvement_type": "involvement_type",
    "project_name": "project_name",
}


class ReplayArgs(BaseModel):
    """入口参数校验：报告与 projects_db 路径必须存在。"""
    report: str
    projects_db: str
    dry_run: bool = False

    @field_validator("report", "projects_db")
    @classmethod
    def _must_exist(cls, v: str) -> str:
        """路径必须指向已存在的文件。"""
        if not pathlib.Path(v).exists():
            raise ValueError(f"路径不存在: {v}")
        return v


def load_would_change(report_path: str) -> list[dict[str, Any]]:
    """读复核报告 JSON，校验是 dry-run 报告且含 would_change，返回 would_change 列表。"""
    report = json.loads(pathlib.Path(report_path).read_text(encoding="utf-8"))
    if not report.get("dry_run"):
        raise ValueError(f"报告非 dry-run（dry_run={report.get('dry_run')}），无 would_change 可回放: {report_path}")
    wc = report.get("would_change")
    if not wc:
        raise ValueError(f"报告缺少 would_change 或为空: {report_path}")
    return wc


def replay(projects_db: str, would_change: list[dict[str, Any]], *, dry_run: bool = False) -> dict[str, int]:
    """把 would_change 回放到 extracted_projects（事务 + 按操作分组 executemany）。

    分两类：
      - field='excluded' → SET excluded=1, exclude_source='review', exclude_reason=<reason>
      - field ∈ _UPDATABLE → SET <列>=<new_value>（capacity_mw 同步写 capacity_mw_raw）
    白名单外的 field 跳过并记 warning。dry_run=True 时只统计不写库。
    返回 {excluded, <字段名>..., skipped, missing_id}。
    """
    # ── 先按操作类型分组，并校验 id 存在 ──────────────────────────────────────
    counts: dict[str, int] = {"excluded": 0, "skipped": 0, "missing_id": 0}
    exclude_params: list[tuple] = []          # (reason, id)
    cap_params: list[tuple] = []              # (capacity_mw, capacity_mw_raw, id)
    field_params: dict[str, list[tuple]] = {} # 列名 → [(new_value, id)]

    conn = sqlite3.connect(str(projects_db))
    try:
        existing_ids = {r[0] for r in conn.execute("SELECT id FROM extracted_projects")}

        for entry in would_change:
            rid = entry.get("id")
            field = entry.get("field")
            if rid not in existing_ids:
                counts["missing_id"] += 1
                logger.warning("would_change 行 id=%s 在 extracted_projects 中不存在，跳过", rid)
                continue

            if field == "excluded":
                exclude_params.append((entry.get("reason") or "", rid))
                counts["excluded"] += 1
            elif field in _UPDATABLE:
                col = _UPDATABLE[field]
                val = entry.get("new_value")
                if col == "capacity_mw":
                    # NOTE: dry-run 的 would_change 只存了解析后的数值（原始建议串未保留），
                    # 故 capacity_mw_raw 回写 str(数值)。真实非 dry-run 跑会存 LLM 原串——
                    # 仅影响这 10 条的 *_raw 原文留存，数值列一致，可接受。
                    num = bri_parse.parse_number(val)
                    cap_params.append((num, str(val), rid))
                else:
                    field_params.setdefault(col, []).append((val, rid))
                counts[col] = counts.get(col, 0) + 1
            else:
                counts["skipped"] += 1
                logger.warning("would_change 行 id=%s 字段 %s 不在白名单，跳过", rid, field)

        if dry_run:
            logger.info("[DRY_RUN] 将回放：%s", {k: v for k, v in counts.items() if v})
            conn.close()
            return counts

        # ── 单事务批量写（CLAUDE.md 规则3：>50 用 executemany）────────────────
        with conn:
            if exclude_params:
                conn.executemany(
                    "UPDATE extracted_projects SET excluded=1, exclude_source='review', "
                    "exclude_reason=? WHERE id=?",
                    exclude_params,
                )
            if cap_params:
                conn.executemany(
                    "UPDATE extracted_projects SET capacity_mw=?, capacity_mw_raw=? WHERE id=?",
                    cap_params,
                )
            for col, params in field_params.items():
                # col 取自 _UPDATABLE 的字面量值，非运行时字符串 → f-string 安全
                conn.executemany(
                    f"UPDATE extracted_projects SET {col}=? WHERE id=?",
                    params,
                )
        logger.info("回放完成：%s", {k: v for k, v in counts.items() if v})
    finally:
        conn.close()
    return counts


def main() -> None:
    """CLI：校验参数 → 加载 would_change →（真跑前 .bak）→ 回放 → 打印摘要。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    parser = argparse.ArgumentParser(description="C2 dry-run would_change 确定性回放（一次性）")
    parser.add_argument("--report", required=True, help="复核报告 JSON 路径（含 would_change）")
    parser.add_argument(
        "--projects-db",
        default=str(_root / "s01b_project_extractor" / "data" / "projects.db"),
        help="目标 projects.db（默认 s01b 主库；worktree 为空壳，须在主工作区跑）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计将回放多少条，不写库")
    args = parser.parse_args()

    try:
        validated = ReplayArgs(report=args.report, projects_db=args.projects_db, dry_run=args.dry_run)
    except Exception as e:  # pydantic ValidationError 等：入口即拦，打印后退出
        print(f"参数校验失败:\n{e}")
        sys.exit(1)

    would_change = load_would_change(validated.report)
    print(f"加载 would_change: {len(would_change)} 条")

    if not validated.dry_run:
        bak = f"{validated.projects_db}.bak_replay_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(validated.projects_db, bak)
        print(f"已备份 projects.db → {bak}")

    counts = replay(validated.projects_db, would_change, dry_run=validated.dry_run)

    print("\n=== 回放摘要 ===")
    print(f"  dry_run     : {validated.dry_run}")
    print(f"  excluded    : {counts.get('excluded', 0)}")
    for col in ("involvement_type", "capacity_mw", "category", "project_name"):
        if counts.get(col):
            print(f"  {col:12}: {counts[col]}")
    print(f"  skipped(白名单外): {counts.get('skipped', 0)}")
    print(f"  missing_id(库中无此行): {counts.get('missing_id', 0)}")


if __name__ == "__main__":
    main()
