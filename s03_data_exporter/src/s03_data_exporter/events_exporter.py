"""events_exporter.py — 从 s02a 三表导出"实体级"明细 CSV（一行 = 一事件 × 一家中国公司）。

供前端下载：同一项目每个 stage 拆条，每个事件下每家中国公司各一行；补全公司
中英文名/子公司中英文名/参与角色中英文/各自央企标志。口径对齐网页可视化：
主表项目走 projects_flat + 基线政策（passes_baseline）；TX/MF 专表项目走专用
基线（passes_txmf_baseline，同前端 txmf 域：仅排中港澳台 + 最后事件年 < 2022）；
事件只含 year>=2022。行序：发电+储能（主表）→ 输变电 → 制造业，组内按
canonical_id + event_id 升序——仅为人眼浏览优化，程序分析勿依赖行序。
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
from . import flat_builder
from .production_capacity import public_production_capacity

BASELINE_YEAR = 2022
_EXCLUDED_ISO3 = {"CHN", "HKG", "MAC", "TWN"}
_UNKNOWN_TEXTS = {"unknown", "未知", "未披露", "n/a", "na", "-", "—"}

_COLUMNS = [
    "canonical_id", "event_id",
    "project_name_en", "project_name_cn",
    "country_en", "country_iso", "province",
    "category_en", "category_cn", "subcategory_cn", "subcategory_en",
    "generation_sources", "storage_status", "technology_tags_cn", "technology_tags_en",
    "stage_cn", "stage_en", "stage_group_cn", "stage_group_en",
    "year", "month",
    "capacity_mw", "pv_capacity_mw", "storage_power_mw", "storage_energy_mwh",
    "voltage_kv", "line_length_km", "transmission_capacity_mw",
    "production_capacity_value", "production_capacity_unit",
    "total_investment_usd", "contract_amount_usd", "financing_amount_usd",
    "financing_model_en",
    "developer_cn", "developer_en",
    "entity_cn", "entity_en", "subsidiary_cn", "subsidiary_en",
    "involvement_type_cn", "involvement_type_en", "is_central_soe",
]

_ENTITY_COLS = (
    "entity_cn", "entity_en", "subsidiary_cn", "subsidiary_en",
    "involvement_type_cn", "involvement_type_en", "is_central_soe",
)


def _year(d: Optional[str]) -> str:
    """日期字符串 → 年（4 位数字）或空串。"""
    d = d or ""
    return d[:4] if len(d) >= 4 and d[:4].isdigit() else ""


def _month(d: Optional[str]) -> str:
    """日期字符串 → YYYY-MM 或空串。"""
    d = d or ""
    return d[:7] if len(d) >= 7 and d[4:5] == "-" else ""


def _is_unknown_text(value: Optional[str]) -> bool:
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
    # Public display/download gate only; unknown capacity does not invalidate ingestion.
    if not enums.is_no_capacity(category) and p.get("capacity_mw") is None:
        return False
    return True


def _is_txmf(category: str) -> bool:
    """类别是否为输变电(TX)/新能源制造业(MF)——按 enums code 判定，不硬编码中文。"""
    return enums.category_code(category or "") in ("TX", "MF")


def passes_txmf_baseline(row: dict) -> bool:
    """TX/MF 专表行（tx_flat/mf_flat）的基线：仅排中港澳台 + 终态事件年 < 2022。

    对齐前端 enrichTxMf；不套主表的 hidden/unknown-entity/capacity 检查（专表语义不适用）。
    ⚠️ 判定源必须是 tx_flat/mf_flat 的 event_year（终态事件年，含全部 stage），
    不能用 projects_flat 的 last_event_year——后者只统计可见 viz 阶段，会漏拟开发/Unknown 项目。
    """
    iso = (row.get("country_iso") or "").upper()
    if iso in _EXCLUDED_ISO3:
        return False
    if row.get("event_year") is None or row["event_year"] < BASELINE_YEAR:
        return False
    return True


def _eligible_ids(flat_db: "Optional[Path | str]") -> Optional[set]:
    """取通过基线政策的 project_id 集合；flat_db 为 None 返回 None（不做基线）。

    主表类别读 projects_flat 走 passes_baseline；TX/MF 读 tx_flat/mf_flat 走
    passes_txmf_baseline（并入下载 CSV，W30b；口径与前端 txmf 域逐条一致）。
    """
    if flat_db is None:
        return None
    con = sqlite3.connect(flat_db)
    try:
        con.row_factory = sqlite3.Row
        out = set()
        for r in con.execute("SELECT * FROM projects_flat"):
            p = dict(r)
            if _is_txmf(p.get("category") or ""):
                continue  # TX/MF 走下方专表判定
            if passes_baseline(p):
                out.add(p["project_id"])
        for tbl in ("tx_flat", "mf_flat"):
            try:
                for r in con.execute(f"SELECT project_id, country_iso, event_year FROM {tbl}"):
                    if passes_txmf_baseline(dict(r)):
                        out.add(r["project_id"])
            except sqlite3.OperationalError as exc:
                # 旧导出库无 tx_flat/mf_flat 表时跳过（TX/MF 不进 CSV），不视为致命
                print(f"[events_exporter] 警告：读取 {tbl} 失败（{exc}），CSV 将不含该表项目")
        return out
    finally:
        con.close()


def export_events_csv(
    src_db,
    out_paths: list,
    flat_db: "Optional[Path | str]" = None,
    iso3_to_en: Optional[dict] = None,
    province_map: Optional[dict] = None,
) -> int:
    """读 s02a 三表，按 事件×公司 一行写出实体级 CSV 到 out_paths（多个目录）。

    合格项目集：flat_db 提供时走 projects_flat + passes_baseline；否则仅排 excluded=1。
    事件只含 year>=2022；无中方公司的事件保留 1 行（实体列留空）。返回写出行数。
    """
    iso3_to_en = iso3_to_en or {}
    province_map = province_map or {}
    eligible = _eligible_ids(flat_db)

    con = sqlite3.connect(src_db)
    try:
        con.row_factory = sqlite3.Row
        projects = {r["id"]: dict(r) for r in con.execute("SELECT * FROM canonical_projects")}
        entities_by_event: dict[str, list[dict]] = {}
        for e in con.execute("SELECT * FROM chinese_entities"):
            if flat_builder.record_is_publishable(dict(e)):
                entities_by_event.setdefault(e["event_id"], []).append(dict(e))
        events = [dict(r) for r in con.execute("SELECT * FROM project_events")]
    finally:
        con.close()

    rows: list[dict] = []
    for ev in events:
        if not flat_builder.record_is_publishable(ev):
            continue
        proj = projects.get(ev["canonical_id"])
        if proj is None or proj.get("excluded", 0):
            continue
        if eligible is not None and ev["canonical_id"] not in eligible:
            continue
        d = ev.get("publish_date") or ev.get("investment_date") or ""
        yr = _year(d)
        if not yr or int(yr) < BASELINE_YEAR:
            continue

        stage = ev.get("project_stage") or ""
        grp = enums.viz_group(stage)
        grp_meta = enums.viz_groups().get(grp, {}) if grp else {}
        cap = ev.get("capacity_mw")

        # 子类只表达项目形态；综合能源旧“光储/其他”组合信息不再导出。
        _cat = proj.get("category") or ""
        _sub = flat_builder._export_subcategory(_cat, proj.get("subcategory"))
        _sources = flat_builder._json_string_list(ev.get("generation_sources_json"))
        _storage_status = str(ev.get("storage_status") or "not_mentioned").strip()
        if "storage_status" not in ev and (
            ev.get("storage_power_mw") is not None or ev.get("storage_energy_mwh") is not None
        ):
            _storage_status = "confirmed"
        _tags = flat_builder._json_string_list(proj.get("technology_tags_json"))

        def _num(col: str):
            """事件级数值列：None → 空串（CSV 口径）。"""
            v = ev.get(col)
            return v if v is not None else ""

        production_value, production_unit = public_production_capacity(
            ev.get("production_capacity_value"), ev.get("production_capacity_unit")
        )
        base = {
            "canonical_id": ev["canonical_id"],
            "event_id": ev["id"],
            "project_name_en": proj.get("project_name_en") or "",
            "project_name_cn": proj.get("project_name") or "",
            "country_en": iso3_to_en.get(proj.get("country_iso") or "", ""),
            "country_iso": proj.get("country_iso") or "",
            "province": province_map.get(ev["canonical_id"]) or "",
            "category_en": enums.category_en(_cat) or _cat,
            "category_cn": _cat,
            "subcategory_cn": _sub or "",
            "subcategory_en": (enums.subcategory_en(_cat, _sub) or "") if _sub else "",
            "generation_sources": " | ".join(_sources),
            "storage_status": _storage_status,
            "technology_tags_cn": " | ".join(_tags),
            "technology_tags_en": " | ".join(flat_builder._technology_tag_en(tag) for tag in _tags),
            "stage_cn": stage,
            "stage_en": enums.stage_en(stage) or stage,
            "stage_group_cn": grp_meta.get("zh", ""),
            "stage_group_en": grp_meta.get("en", ""),
            "year": yr,
            "month": _month(d),
            "capacity_mw": cap if cap is not None else "",
            "pv_capacity_mw": _num("pv_capacity_mw"),
            "storage_power_mw": _num("storage_power_mw") if _storage_status == "confirmed" else "",
            "storage_energy_mwh": _num("storage_energy_mwh") if _storage_status == "confirmed" else "",
            "voltage_kv": _num("voltage_kv"),
            "line_length_km": _num("line_length_km"),
            "transmission_capacity_mw": _num("transmission_capacity_mw"),
            "production_capacity_value": production_value if production_value is not None else "",
            "production_capacity_unit": production_unit or "",
            "total_investment_usd": _num("total_investment_usd"),
            "contract_amount_usd": _num("contract_amount_usd"),
            "financing_amount_usd": _num("financing_amount_usd"),
            "financing_model_en": enums.financing_en(ev.get("financing_model") or "") or (ev.get("financing_model") or ""),
            "developer_cn": ev.get("project_developer") or "",
            "developer_en": ev.get("project_developer_en") or "",
        }

        ents = entities_by_event.get(ev["id"], [])
        if not ents:
            rows.append({**base, **{c: "" for c in _ENTITY_COLS}})
            continue
        for e in ents:
            inv_type = e.get("involvement_type") or ""
            soe = e.get("is_central_soe")
            rows.append({
                **base,
                "entity_cn": e.get("implementing_entity") or "",
                "entity_en": e.get("implementing_entity_en") or "",
                "subsidiary_cn": e.get("subsidiary_name") or "",
                "subsidiary_en": e.get("subsidiary_name_en") or "",
                "involvement_type_cn": inv_type,
                "involvement_type_en": enums.involvement_en(inv_type) or inv_type,
                "is_central_soe": soe if soe is not None else "",
            })

    # 分组排：主表（发电+储能）→ 输变电 → 制造业，组内 canonical_id + event_id 升序（仅人眼优化）
    _group_rank = {"TX": 1, "MF": 2}

    def _sort_key(row: dict):
        code = enums.category_code(row["category_cn"] or "") or ""
        return (_group_rank.get(code, 0), row["canonical_id"], row["event_id"])

    rows.sort(key=_sort_key)

    for out in out_paths:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    return len(rows)
