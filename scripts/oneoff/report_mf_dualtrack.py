"""report_mf_dualtrack.py — 阶段3任务3.3：MF（新能源制造业）双轨对照报告，只读、不迁移。

背景：canonical_projects 里有 category='新能源制造业' 的项目，non_project_records 里另有
record_type='新能源制造业' 的独立记录——两边互不打通。本脚本只读真库，生成对照报告
（双轨基本盘 + 同名重叠 + 边界案落点扫描），供用户拍板迁移口径；本脚本零写库。

用法：
    uv run --no-sync python scripts/oneoff/report_mf_dualtrack.py
    uv run --no-sync python scripts/oneoff/report_mf_dualtrack.py --db <path> --threshold 85
"""
from __future__ import annotations

import argparse
import csv
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, field_validator
from rapidfuzz import fuzz

# ──────────────────────────────────────────────
# 常量与默认路径
# ──────────────────────────────────────────────

_MODULE_DIR = Path(__file__).resolve().parent
_WORKTREE_ROOT = _MODULE_DIR.parent.parent
_LOGS_DIR = _MODULE_DIR / "logs"

_DEFAULT_DB = Path(
    r"E:\Projects\BRI_RE_Database_v2\s02a_duplicate_merger\data\db\bri_re_database.db"
)
_DEFAULT_REPORT_MD = _WORKTREE_ROOT / "docs" / "analysis" / "mf_dualtrack_report_2026-07.md"
_DEFAULT_SUPPORTING_DIR = _WORKTREE_ROOT / "supporting_docs"

_MF_CATEGORY = "新能源制造业"

# 阶段2 T2 硬收录边界关键词（逐字取自 codex_review/runs/2026Q3/guide_T2.md 「不应收录」节，
# 不重新裁决——只用于标出"若迁移将触发排除"的样例）
_T2_BOUNDARY_KEYWORDS = ["锂矿", "铁矿", "电解铝", "蓝氨", "蓝氢"]

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 入口参数校验（项目规则 4）
# ──────────────────────────────────────────────


class ReportParams(BaseModel):
    """report_mf_dualtrack 的入口参数，含路径存在性与阈值范围校验。"""

    db: Path
    threshold: int
    report_md: Path
    supporting_dir: Path

    @field_validator("db")
    @classmethod
    def _check_db(cls, v: Path) -> Path:
        """校验 db 路径存在。"""
        if not v.exists():
            raise ValueError(f"db 路径不存在：{v}")
        return v

    @field_validator("threshold")
    @classmethod
    def _check_threshold(cls, v: int) -> int:
        """校验相似度阈值在 0-100 之间。"""
        if not (0 <= v <= 100):
            raise ValueError(f"threshold 必须在 0-100 之间，收到 {v}")
        return v


# ──────────────────────────────────────────────
# 数据加载（只读）
# ──────────────────────────────────────────────


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    """以只读 URI 模式打开真库连接，杜绝任何写路径。"""
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _load_canonical_mf(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """加载 canonical_projects 中 category=新能源制造业 的全部行（含已排除）。"""
    cur = conn.execute(
        "SELECT id, project_name, country, country_iso, excluded, excluded_reason "
        "FROM canonical_projects WHERE category=?",
        (_MF_CATEGORY,),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _load_npr_mf(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """加载 non_project_records 中 record_type=新能源制造业 的全部行。"""
    cur = conn.execute(
        "SELECT id, project_name, country, country_iso, implementing_entity, "
        "implementing_entity_en, publish_date, source_link, capacity_mw_raw, "
        "capacity_details, project_stage "
        "FROM non_project_records WHERE record_type=?",
        (_MF_CATEGORY,),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ──────────────────────────────────────────────
# 分析：字段完备度
# ──────────────────────────────────────────────


def _field_completeness(rows: list[dict[str, Any]], fields: list[str]) -> dict[str, int]:
    """统计给定字段在 rows 中非空（非 None 且 strip 后非空串）的行数。"""
    out: dict[str, int] = {}
    for f in fields:
        out[f] = sum(1 for r in rows if r.get(f) is not None and str(r[f]).strip() != "")
    return out


# ──────────────────────────────────────────────
# 分析：同名重叠（精确 + rapidfuzz 相似度，同国优先）
# ──────────────────────────────────────────────


def _find_overlaps(
    npr_rows: list[dict[str, Any]],
    canonical_rows: list[dict[str, Any]],
    threshold: int,
) -> list[dict[str, Any]]:
    """对每条 npr 记录，在 canonical MF 中找精确名匹配或 rapidfuzz 相似度>=threshold 的候选（同国优先）。"""
    matches: list[dict[str, Any]] = []
    for npr in npr_rows:
        npr_name = (npr.get("project_name") or "").strip()
        npr_iso = npr.get("country_iso") or ""
        if not npr_name:
            continue
        best: dict[str, Any] | None = None
        for cp in canonical_rows:
            cp_name = (cp.get("project_name") or "").strip()
            if not cp_name:
                continue
            same_country = cp.get("country_iso") == npr_iso
            if cp_name == npr_name:
                score = 100.0
                match_type = "exact"
            else:
                score = fuzz.ratio(npr_name, cp_name)
                match_type = "fuzzy"
                if score < threshold:
                    continue
            candidate = {
                "npr_id": npr["id"],
                "npr_name": npr_name,
                "npr_country_iso": npr_iso,
                "canonical_id": cp["id"],
                "canonical_name": cp_name,
                "canonical_country_iso": cp.get("country_iso"),
                "canonical_excluded": cp.get("excluded"),
                "canonical_excluded_reason": cp.get("excluded_reason"),
                "match_type": match_type,
                "score": round(score, 1),
                "same_country": same_country,
            }
            # 排序优先级：同国 > 分数高。用 tuple 比较取最优候选。
            key = (same_country, score)
            if best is None or key > (best["same_country"], best["score"]):
                best = candidate
        if best is not None:
            matches.append(best)
    return matches


# ──────────────────────────────────────────────
# 分析：边界案关键词扫描（引用阶段2 T2 规则，不重新裁决）
# ──────────────────────────────────────────────


def _scan_boundary_keywords(npr_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """扫描 57 条 npr 记录的 project_name+capacity_details，命中 T2 边界关键词的标记为候选排除样例。"""
    hits: list[dict[str, Any]] = []
    for npr in npr_rows:
        haystack = f"{npr.get('project_name') or ''} {npr.get('capacity_details') or ''}"
        hit_kws = [kw for kw in _T2_BOUNDARY_KEYWORDS if kw in haystack]
        if hit_kws:
            hits.append({**npr, "hit_keywords": hit_kws})
    return hits


# ──────────────────────────────────────────────
# 输出：重叠明细 CSV
# ──────────────────────────────────────────────


def _write_overlap_csv(matches: list[dict[str, Any]], out_path: Path) -> None:
    """把重叠匹配明细写入 CSV（utf-8-sig，供 Excel 打开）。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "npr_id", "npr_name", "npr_country_iso",
        "canonical_id", "canonical_name", "canonical_country_iso",
        "canonical_excluded", "canonical_excluded_reason",
        "match_type", "score", "same_country",
    ]
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for m in matches:
            writer.writerow(m)
    logger.info("重叠明细 CSV 已写入：%s（%d 行）", out_path, len(matches))


# ──────────────────────────────────────────────
# 输出：MD 报告
# ──────────────────────────────────────────────


def _render_report(
    *,
    canonical_rows: list[dict[str, Any]],
    npr_rows: list[dict[str, Any]],
    completeness: dict[str, int],
    matches: list[dict[str, Any]],
    boundary_hits: list[dict[str, Any]],
    overlap_csv_path: Path,
    threshold: int,
    db_path: Path,
) -> str:
    """把各分析结果拼装为 Markdown 报告正文。"""
    total_mf = len(canonical_rows)
    excluded_rows = [r for r in canonical_rows if r["excluded"] == 1]
    active_rows = [r for r in canonical_rows if r["excluded"] == 0]
    n_excluded = len(excluded_rows)
    n_active = len(active_rows)

    reason_groups: dict[str, int] = {}
    for r in excluded_rows:
        reason = r.get("excluded_reason") or "(空)"
        reason_groups[reason] = reason_groups.get(reason, 0) + 1
    # T2 单列，其余按数量降序
    t2_key = "codex_T2_exclude:2026Q3"
    t2_count = reason_groups.pop(t2_key, 0)
    other_reasons = sorted(reason_groups.items(), key=lambda kv: -kv[1])

    n_npr = len(npr_rows)

    n_exact = sum(1 for m in matches if m["match_type"] == "exact")
    n_fuzzy = sum(1 for m in matches if m["match_type"] == "fuzzy")
    n_matches = len(matches)
    n_unmatched = n_npr - n_matches

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = []
    lines.append("# MF（新能源制造业）双轨对照报告")
    lines.append("")
    lines.append(f"生成时间：{now}　脚本：`scripts/oneoff/report_mf_dualtrack.py`　"
                  f"真库：`{db_path}`（只读连接，本报告零写库）")
    lines.append("")
    lines.append("## 摘要")
    lines.append("")
    lines.append(
        f"- canonical MF 总数 **{total_mf}**（未排除 **{n_active}** / 已排除 **{n_excluded}**）；"
        f"non_project_records 新能源制造业记录 **{n_npr}** 条"
    )
    lines.append(
        f"- 同名重叠（精确+rapidfuzz≥{threshold}，同国优先）：**{n_matches}** 对"
        f"（精确 {n_exact} / 模糊 {n_fuzzy}），未匹配 {n_unmatched} 条"
    )
    lines.append(
        f"- 若 57 条整体迁移，命中阶段2 T2 硬收录边界关键词（锂矿/铁矿/电解铝/蓝氨/蓝氢）的有 "
        f"**{len(boundary_hits)}** 条，将立刻触发排除"
    )
    lines.append("")

    # ── 1. 双轨基本盘 ──
    lines.append("## 1. 双轨基本盘")
    lines.append("")
    lines.append("### 1.1 canonical_projects（category=新能源制造业）")
    lines.append("")
    lines.append(f"| 口径 | 数量 |")
    lines.append(f"|---|---|")
    lines.append(f"| 总数 | {total_mf} |")
    lines.append(f"| 未排除（excluded=0） | {n_active} |")
    lines.append(f"| 已排除（excluded=1） | {n_excluded} |")
    lines.append(f"| 其中 codex_T2_exclude:2026Q3 | {t2_count} |")
    lines.append("")
    if other_reasons:
        lines.append("已排除的其余 reason 分组（非 T2）：")
        lines.append("")
        lines.append("| excluded_reason | 数量 |")
        lines.append("|---|---|")
        for reason, cnt in other_reasons:
            lines.append(f"| {reason} | {cnt} |")
        lines.append("")

    lines.append("### 1.2 non_project_records（record_type=新能源制造业）字段完备度")
    lines.append("")
    lines.append(f"共 {n_npr} 条。字段非空计数：")
    lines.append("")
    lines.append("| 字段 | 非空行数 | 占比 |")
    lines.append("|---|---|---|")
    field_labels = {
        "country": "国家（country）",
        "country_iso": "国家 ISO（country_iso）",
        "implementing_entity": "实施主体（implementing_entity）",
        "publish_date": "发布日期（publish_date）",
        "source_link": "信源（source_link）",
    }
    for field, label in field_labels.items():
        n = completeness.get(field, 0)
        pct = f"{n / n_npr * 100:.0f}%" if n_npr else "0%"
        lines.append(f"| {label} | {n}/{n_npr} | {pct} |")
    lines.append("")

    # ── 2. 同名重叠 ──
    lines.append("## 2. 同名重叠")
    lines.append("")
    lines.append(
        f"匹配方法：精确名匹配（score=100）+ rapidfuzz `fuzz.ratio` 相似度 ≥ {threshold}"
        f"（同国 country_iso 优先于分数排序，每条 npr 记录最多取 1 个最佳候选）。"
    )
    lines.append("")
    lines.append(f"- 重叠对数：**{n_matches}**（精确 {n_exact} / 模糊 {n_fuzzy}）")
    lines.append(f"- 明细 CSV（不提交 git）：`{overlap_csv_path}`")
    lines.append(
        f"- 与计划旧数「37」的差异：旧数来自 2026-07-06 之前的口径（当时 canonical MF 未排除数为 178，"
        f"计划文档 `docs/rules_and_plans/plan_data_quality_2026-07.md` §阶段3 记录\"178/37\"）；"
        f"此后阶段2 T2 复核已将 MF 中 {t2_count} 条判定 exclude（`codex_T2_exclude:2026Q3`），"
        f"canonical MF 未排除数从 178 降至 {n_active}，故本次按当前库重算得到 {n_matches} 对，"
        f"与旧数口径不同（统计口径、库状态均已变化，非本脚本误差）。"
    )
    lines.append("")

    lines.append("### 抽样对照（前 5 对，按分数降序）")
    lines.append("")
    sample = sorted(matches, key=lambda m: -m["score"])[:5]
    for i, m in enumerate(sample, 1):
        lines.append(f"**样例 {i}**（{m['match_type']}，score={m['score']}，"
                      f"同国={'是' if m['same_country'] else '否'}）")
        lines.append(f"- npr[{m['npr_id']}] `{m['npr_name']}`（{m['npr_country_iso']}）")
        lines.append(
            f"- canonical `{m['canonical_id']}` `{m['canonical_name']}`（{m['canonical_country_iso']}，"
            f"excluded={m['canonical_excluded']}"
            + (f"，reason={m['canonical_excluded_reason']}" if m["canonical_excluded_reason"] else "")
            + "）"
        )
        lines.append("")

    # ── 3. 边界案落点 ──
    lines.append("## 3. 边界案落点（引用阶段2 T2 规则，不重新裁决）")
    lines.append("")
    lines.append(
        "T2 硬收录边界规则原文（`codex_review/runs/2026Q3/guide_T2.md`）：\n"
        "> **不应收录**：… 非能源资产：矿山开采（锂矿/铁矿等）、电解铝冶炼、蓝氨/蓝氢（天然气制氨，非可再生能源）。"
    )
    lines.append("")
    lines.append(
        f"对 57 条 non_project_records 的 project_name + capacity_details 做关键词扫描"
        f"（锂矿/铁矿/电解铝/蓝氨/蓝氢），命中 **{len(boundary_hits)}** 条——"
        f"若这些记录被迁移为 canonical，按 T2 规则将立刻触发 exclude。"
    )
    lines.append("")
    if boundary_hits:
        lines.append("| npr id | project_name | 命中关键词 |")
        lines.append("|---|---|---|")
        for h in boundary_hits:
            lines.append(f"| {h['id']} | {h['project_name']} | {', '.join(h['hit_keywords'])} |")
    else:
        lines.append("（无命中）")
    lines.append("")

    # ── 4. 迁移口径选项 ──
    lines.append("## 4. 迁移口径选项")
    lines.append("")
    lines.append("### A. 维持双轨不迁移")
    lines.append(
        f"- 影响：0 新增/合并 canonical；0 工作量；MF 展示继续只用 canonical（{n_active} 条未排除）"
        f"，non_project_records 仅存档不进前端。"
    )
    lines.append(
        f"- 去重风险：无（不动结构）。代价：{n_npr} 条一手信息（含 {n_matches} 条与既有项目重叠、"
        f"{n_npr - n_matches} 条独有）继续不可见于任何展示面。"
    )
    lines.append("")
    lines.append("### B. 仅同名重叠部分补挂信源")
    lines.append(
        f"- 影响：0 新增 canonical；把 {n_matches} 条重叠记录的 source_link/publish_date/实体信息"
        f"补挂到对应既有 canonical MF 项目（作为该项目的补充信源/事件备注），不新建 id。"
    )
    lines.append(
        f"- 工作量：中等——需人工/半自动逐对确认 {n_matches} 对匹配是否语义上确为同一项目"
        f"（rapidfuzz 只是候选，非确定性判断，尤其 {n_fuzzy} 条模糊匹配需复核）；"
        f"其中命中边界关键词的 {sum(1 for h in boundary_hits if h['id'] in {m['npr_id'] for m in matches})} 条"
        f"重叠记录对应的 canonical 项目本身需检查是否也该被 T2 规则排除。"
    )
    lines.append(f"- 去重风险：低（不新建 id，只加信源，不会产生新的重复项目）。")
    lines.append(f"- {n_npr - n_matches} 条未匹配的记录仍不处理，维持存档状态。")
    lines.append("")
    lines.append("### C. 全量迁移（57 条升格 canonical）")
    lines.append(
        f"- 路径：57 条中 {n_matches} 条与既有 canonical 重叠，若不去重直接建 id 会产生重复项目——"
        f"需先套用 s02a 的模糊匹配去重逻辑（或人工去重），仅对 {n_npr - n_matches} 条真正独有的记录"
        f"新建 canonical id（{_infer_new_iso_examples(npr_rows, matches)}）；"
        f"重叠的 {n_matches} 条应走方案 B 的补挂路径而非新建。"
    )
    lines.append(
        f"- 边界案拦截：全量 57 条中命中 T2 关键词 **{len(boundary_hits)}** 条，若走全量迁移，"
        f"这些记录进 canonical 后应立即比照 T2 规则标记 excluded（不应作为可见项目升格）。"
    )
    lines.append(
        f"- 工作量：较大——id 分配需走 s02a 编号规则（`{{ISO3}}-MF-{{NNNN}}`，需查当前各国 MF 序号"
        f"最大值续号），且需人工复核每条记录是否满足\"具名中企参与+可识别单体项目\"（T2 keep 门槛），"
        f"不能全信 non_project_records 原始标注。"
    )
    lines.append(f"- 去重风险：高（若跳过去重步骤直接迁移全部 57 条，会与既有 {n_matches} 条重叠项目产生重复）。")
    lines.append("")
    lines.append("### 推荐")
    lines.append(
        f"**推荐方案 B**（仅同名重叠部分补挂信源，未匹配的暂维持归档）：\n"
        f"- 理由：{n_matches} 条重叠已有 canonical 归宿，补挂信源零去重风险、工作量可控，"
        f"能立刻提升既有 MF 项目的信源完整度；\n"
        f"- {n_npr - n_matches} 条独有记录不建议在本任务内草率全量迁移——"
        f"其中命中 T2 边界关键词的 {len(boundary_hits)} 条大概率会被排除，"
        f"其余独有记录是否满足\"可识别单体项目\"门槛需要逐条核实（不是数据结构问题，是内容判断问题），"
        f"建议留给阶段2 T3（registry/去重复核）或后续人工 campaign 消化，而非本次批量升格；\n"
        f"- 方案 A（完全不迁移）过于保守，浪费了 {n_matches} 条已确认重叠的可补充信源；"
        f"方案 C（全量迁移）在未逐条核实独有记录质量前风险最高，且会把本该走 T2 判定的边界案"
        f"（{len(boundary_hits)} 条）暴露为需要额外一轮排除处理的负担。"
    )
    lines.append("")

    # ── 5. 附录：SQL ──
    lines.append("## 附录：关键数字复算 SQL")
    lines.append("")
    lines.append("```sql")
    lines.append("-- canonical MF 总数 / 未排除 / 已排除")
    lines.append("SELECT COUNT(*) FROM canonical_projects WHERE category='新能源制造业';")
    lines.append("SELECT COUNT(*) FROM canonical_projects WHERE category='新能源制造业' AND excluded=0;")
    lines.append("SELECT COUNT(*) FROM canonical_projects WHERE category='新能源制造业' AND excluded=1;")
    lines.append("")
    lines.append("-- 已排除 reason 分组")
    lines.append(
        "SELECT excluded_reason, COUNT(*) FROM canonical_projects "
        "WHERE category='新能源制造业' AND excluded=1 GROUP BY excluded_reason ORDER BY COUNT(*) DESC;"
    )
    lines.append("")
    lines.append("-- non_project_records 新能源制造业总数与字段完备度")
    lines.append("SELECT COUNT(*) FROM non_project_records WHERE record_type='新能源制造业';")
    lines.append(
        "SELECT COUNT(*) FROM non_project_records WHERE record_type='新能源制造业' "
        "AND implementing_entity IS NOT NULL AND TRIM(implementing_entity)<>'';"
    )
    lines.append("")
    lines.append("-- 边界关键词扫描（示例：锂矿）")
    lines.append(
        "SELECT id, project_name FROM non_project_records WHERE record_type='新能源制造业' "
        "AND (project_name LIKE '%锂矿%' OR capacity_details LIKE '%锂矿%');"
    )
    lines.append("```")
    lines.append("")
    lines.append(
        "同名重叠对数为脚本计算结果（精确名匹配 SQL 可直接对拍，模糊匹配需跑本脚本"
        "`scripts/oneoff/report_mf_dualtrack.py` 复现 rapidfuzz 计算）：\n"
        "```sql\n"
        "-- 精确名匹配（不含模糊）\n"
        "SELECT n.id AS npr_id, n.project_name, c.id AS canonical_id\n"
        "FROM non_project_records n JOIN canonical_projects c\n"
        "  ON c.project_name = n.project_name AND c.category='新能源制造业'\n"
        "WHERE n.record_type='新能源制造业';\n"
        "```"
    )
    lines.append("")

    return "\n".join(lines)


def _infer_new_iso_examples(
    npr_rows: list[dict[str, Any]], matches: list[dict[str, Any]]
) -> str:
    """为方案 C 的说明文字生成一句"独有记录分布在哪些国家"的简短提示。"""
    matched_ids = {m["npr_id"] for m in matches}
    unmatched = [r for r in npr_rows if r["id"] not in matched_ids]
    isos = sorted({r.get("country_iso") or "?" for r in unmatched})
    return f"涉及国家 {', '.join(isos[:8])}{'…' if len(isos) > 8 else ''} 共 {len(unmatched)} 条"


# ──────────────────────────────────────────────
# 日志初始化
# ──────────────────────────────────────────────


def _setup_logging() -> None:
    """初始化 logging：同时输出 console 和带时间戳的日志文件（项目规则 2）。"""
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = _LOGS_DIR / f"report_mf_dualtrack_{timestamp}.log"
    fmt = "%(asctime)s [%(name)s] %(levelname)s %(message)s"
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers, force=True)
    logger.info("日志文件：%s", log_file)


# ──────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────


def main() -> None:
    """CLI 入口：只读加载双轨数据 → 分析完备度/重叠/边界案 → 落 MD 报告 + CSV。"""
    _setup_logging()
    parser = argparse.ArgumentParser(description="MF 双轨对照报告（只读，报告先行）")
    parser.add_argument("--db", default=str(_DEFAULT_DB), help="真库路径（只读打开）")
    parser.add_argument("--threshold", type=int, default=85, help="rapidfuzz 相似度阈值（0-100）")
    parser.add_argument("--report-md", default=str(_DEFAULT_REPORT_MD), help="MD 报告输出路径")
    parser.add_argument(
        "--supporting-dir", default=str(_DEFAULT_SUPPORTING_DIR), help="重叠 CSV 输出目录"
    )
    args = parser.parse_args()

    try:
        params = ReportParams(
            db=Path(args.db),
            threshold=args.threshold,
            report_md=Path(args.report_md),
            supporting_dir=Path(args.supporting_dir),
        )
    except Exception as e:  # pydantic ValidationError：入口即拦，打印后退出
        logger.error("参数校验失败: %s", e)
        sys.exit(1)

    conn = _connect_ro(params.db)
    try:
        canonical_rows = _load_canonical_mf(conn)
        npr_rows = _load_npr_mf(conn)
    finally:
        conn.close()

    logger.info("canonical MF 总数=%d，non_project_records MF=%d", len(canonical_rows), len(npr_rows))

    completeness = _field_completeness(
        npr_rows, ["country", "country_iso", "implementing_entity", "publish_date", "source_link"]
    )
    matches = _find_overlaps(npr_rows, canonical_rows, params.threshold)
    boundary_hits = _scan_boundary_keywords(npr_rows)

    logger.info(
        "重叠对数=%d（精确=%d/模糊=%d），边界案命中=%d",
        len(matches),
        sum(1 for m in matches if m["match_type"] == "exact"),
        sum(1 for m in matches if m["match_type"] == "fuzzy"),
        len(boundary_hits),
    )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    overlap_csv_path = params.supporting_dir / f"mf_dualtrack_overlap_{timestamp}.csv"
    _write_overlap_csv(matches, overlap_csv_path)

    report_text = _render_report(
        canonical_rows=canonical_rows,
        npr_rows=npr_rows,
        completeness=completeness,
        matches=matches,
        boundary_hits=boundary_hits,
        overlap_csv_path=overlap_csv_path,
        threshold=params.threshold,
        db_path=params.db,
    )
    params.report_md.parent.mkdir(parents=True, exist_ok=True)
    params.report_md.write_text(report_text, encoding="utf-8")
    logger.info("MD 报告已写入：%s", params.report_md)


if __name__ == "__main__":
    main()
