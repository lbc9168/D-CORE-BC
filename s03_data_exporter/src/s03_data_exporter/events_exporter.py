"""events_exporter.py — 从 s02a 三表导出"按事件拆条"的明细 CSV（一行一个 project_event）。

供前端下载：同一项目的每个 stage 各一行，含 canonical_id + event_id + 细 stage + 4 大类映射列。
保留原始细 stage（stage_cn/stage_en），并加 4 大类映射列（stage_group_cn/stage_group_en）。
"""
import csv
import sqlite3
import sys
from pathlib import Path
from typing import Optional

# ── 枚举单一可信源：上溯找仓库根（含 bri_enums.py）──
_root = next(p for p in Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
import bri_enums as enums  # noqa: E402

BASELINE_YEAR = 2022
_EXCLUDED_ISO3 = {"CHN", "HKG", "MAC", "TWN"}
_UNKNOWN_TEXTS = {"unknown", "未知", "未披露", "n/a", "na", "-", "—"}


def _is_unknown_text(value) -> bool:
    """文本是否视作"未知"（空或占位符）；对齐前端 isUnknownText。"""
    if not value:
        return True
    return str(value).strip().lower() in _UNKNOWN_TEXTS


def passes_baseline(p: dict) -> bool:
    """projects_flat 行是否进入网页可视化口径（逐条对齐前端 shouldExcludeByPolicy）。

    ⚠️ 改这里须同步前端 src/contexts/DataContext.tsx 的 shouldExcludeByPolicy；
    TS/Python 无法共享代码，靠本文件测试钉住一致性。
    """
    iso = (p.get("country_iso") or "").upper()
    if iso in _EXCLUDED_ISO3:
        return False
    if p.get("first_event_year") is None or p.get("last_event_year") is None:
        return False
    if p["last_event_year"] < BASELINE_YEAR:
        return False
    category = p.get("category") or ""
    if enums.is_excluded_category(category) or enums.is_hidden_category(category):
        return False
    if _is_unknown_text(p.get("primary_entity")):
        return False
    if category and not enums.is_no_capacity(category) and p.get("capacity_mw") is None:
        return False
    return True


_COLUMNS = [
    "canonical_id", "event_id",
    "project_name_en", "project_name_cn",
    "country_en", "country_iso", "province",
    "category_en", "category_cn",
    "stage_cn", "stage_en", "stage_group_cn", "stage_group_en",
    "year", "month",
    "capacity_mw", "total_investment_usd", "financing_model_en",
    "entities", "is_central_soe", "verification_status",
]


def _year(d: Optional[str]) -> str:
    """日期字符串 → 年（4 位数字）或空串。"""
    d = d or ""
    return d[:4] if len(d) >= 4 and d[:4].isdigit() else ""


def _month(d: Optional[str]) -> str:
    """日期字符串 → YYYY-MM 或空串。"""
    d = d or ""
    return d[:7] if len(d) >= 7 and d[4:5] == "-" else ""


def export_events_csv(
    src_db,
    out_paths: list,
    iso3_to_en: Optional[dict] = None,
    province_map: Optional[dict] = None,
) -> int:
    """读 s02a 三表，按 project_event 一行写出 CSV 到 out_paths（多个目录）；排除 excluded 项目。返回行数。"""
    iso3_to_en = iso3_to_en or {}
    province_map = province_map or {}

    con = sqlite3.connect(src_db)
    try:
        con.row_factory = sqlite3.Row
        projects = {r["id"]: dict(r) for r in con.execute("SELECT * FROM canonical_projects")}
        entities_by_event: dict[str, list[dict]] = {}
        for e in con.execute("SELECT * FROM chinese_entities"):
            entities_by_event.setdefault(e["event_id"], []).append(dict(e))
        events = [dict(r) for r in con.execute("SELECT * FROM project_events")]
    finally:
        con.close()

    rows: list[dict] = []
    for ev in events:
        proj = projects.get(ev["canonical_id"])
        if proj is None or proj.get("excluded", 0):
            continue
        stage = ev.get("project_stage") or ""
        grp = enums.viz_group(stage)
        grp_meta = enums.viz_groups().get(grp, {}) if grp else {}
        d = ev.get("publish_date") or ev.get("investment_date") or ""
        ents = entities_by_event.get(ev["id"], [])
        cap = ev.get("capacity_mw")
        inv = ev.get("total_investment_usd")
        rows.append(
            {
                "canonical_id": ev["canonical_id"],
                "event_id": ev["id"],
                "project_name_en": proj.get("project_name_en") or "",
                "project_name_cn": proj.get("project_name") or "",
                "country_en": iso3_to_en.get(proj.get("country_iso") or "", ""),
                "country_iso": proj.get("country_iso") or "",
                "province": province_map.get(ev["canonical_id"]) or "",
                "category_en": enums.category_en(proj.get("category") or "") or (proj.get("category") or ""),
                "category_cn": proj.get("category") or "",
                "stage_cn": stage,
                "stage_en": enums.stage_en(stage) or stage,
                "stage_group_cn": grp_meta.get("zh", ""),
                "stage_group_en": grp_meta.get("en", ""),
                "year": _year(d),
                "month": _month(d),
                "capacity_mw": cap if cap is not None else "",
                "total_investment_usd": inv if inv is not None else "",
                "financing_model_en": enums.financing_en(ev.get("financing_model") or "") or (ev.get("financing_model") or ""),
                "entities": "; ".join(
                    e.get("implementing_entity") or "" for e in ents if e.get("implementing_entity")
                ),
                "is_central_soe": 1 if any(e.get("is_central_soe") == 1 for e in ents) else 0,
                "verification_status": ev.get("verification_status") or "",
            }
        )

    for out in out_paths:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    return len(rows)
