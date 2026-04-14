#!/usr/bin/env python3
"""
step2_match_ai.py
将 202210-202509-full-old.xlsx 中的信息补充到 projects.db。

匹配方式（三阶段）：
  1. 按 country_iso 精确预过滤
  2. rapidfuzz 加权模糊评分（project_name 40% + implementing_entity 30% + capacity 20% + location 10%）
  3. 得分 ≥ 90：自动匹配；55–90：Gemini 二次确认；< 55：跳过

填充规则：只在 DB 字段为 NULL / 空字符串 / "Unknown" 时填入，不覆盖已有值，不新增行。

用法：
  uv run --with pandas --with openpyxl --with rapidfuzz --with google-genai --with python-dotenv step2_match_ai.py
  uv run ... step2_match_ai.py --dry-run
  uv run ... step2_match_ai.py --skip-gemini   # 跳过 Gemini，仅执行自动匹配

涉及字段映射（File 2 列索引 → DB 列名）：
  col 15 Remarks (Official Website Link)      → official_website
  col 16 Remarks (Non-Official Source Link)   → non_official_website
  col 17 Total Project Investment (Million USD) → total_investment_usd
  col 18 Subcontract Amount (Million USD)     → subcontract_amount_usd
  col 19 Investment (or Financial Close) Date → investment_date
  col 21 Remarks (second)                     → additional_remarks
  col 22 Financing Model                      → financing_model
"""

import argparse
import csv
import json
import math
import os
import re
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai import types
from rapidfuzz import fuzz

# ── 路径 ─────────────────────────────────────────────────────────────────────
_HERE       = Path(__file__).resolve().parent           # external-tools/data-combination/
_REPO_ROOT  = _HERE.parents[1]                          # 项目根目录
_DB_PATH    = _REPO_ROOT / "s01b_project_extractor" / "data" / "projects.db"
_EXCEL_PATH = _HERE / "202210-202509-full-old.xlsx"
_LOG_DIR    = _HERE / "logs"

# ── 加载 .env（沿目录树向上查找，找到第一个即停止）────────────────────────────
for _p in [_HERE, *_HERE.parents]:
    _env_file = _p / ".env"
    if _env_file.exists():
        load_dotenv(_env_file)
        break

# ── 匹配阈值 ──────────────────────────────────────────────────────────────────
AUTO_MATCH_THRESHOLD = 90.0   # 综合得分 ≥ 此值：自动匹配
SKIP_THRESHOLD       = 55.0   # 综合得分 < 此值：直接跳过

# ── Excel 列索引（File 2 列头因编码问题乱码，统一使用位置索引）────────────────
#   匹配用
F2_NAME    = 0    # Project Name / 项目名称
F2_ISO     = 2    # Country ISO Alpha-3 Code
F2_LOC     = 3    # Location(State)
F2_CAP     = 5    # Capacity (MW)
F2_ENTITY  = 9    # Implementing Chinese Entity / 中方实施企业
#   填充用
F2_OFF     = 15   # Remarks (Official Website Link)
F2_NONOFF  = 16   # Remarks (Non-Official Source Link)
F2_INV     = 17   # Total Project Investment (Million USD)
F2_SUB     = 18   # Subcontract Amount (Million USD)
F2_DATE    = 19   # Investment (or Financial Close) Date
F2_REMARKS = 21   # Remarks（第二个，备注）
F2_FIN     = 22   # Financing Model / 融资模式

# ── 字段映射：Excel 列索引 → DB 列名 ─────────────────────────────────────────
FIELD_MAP: dict[int, str] = {
    F2_OFF    : "official_website",
    F2_NONOFF : "non_official_website",
    F2_INV    : "total_investment_usd",
    F2_SUB    : "subcontract_amount_usd",
    F2_DATE   : "investment_date",
    F2_REMARKS: "additional_remarks",
    F2_FIN    : "financing_model",
}

# ── 工具函数 ──────────────────────────────────────────────────────────────────
def is_empty(val) -> bool:
    """判断值是否视为"空"（NULL / 空字符串 / Unknown / NaN 等）。"""
    if val is None:
        return True
    if isinstance(val, float) and math.isnan(val):
        return True
    s = str(val).strip()
    return s == "" or s.lower() in {"unknown", "nan", "none", "n/a", "na", "-", "–"}


def safe_str(val) -> str | None:
    """将值转为字符串并 strip；若为空则返回 None。"""
    if is_empty(val):
        return None
    return str(val).strip()


def extract_mw(val) -> float | None:
    """从字符串中提取容量数值（MW）。"""
    s = safe_str(val)
    if s is None:
        return None
    m = re.search(r'[\d]+(?:\.\d+)?', s.replace(',', ''))
    return float(m.group()) if m else None


def capacity_score(excel_cap, db_cap) -> float:
    """返回 0–100 的容量相似度分。"""
    ec = extract_mw(excel_cap)
    dc = extract_mw(db_cap)
    if ec is None and dc is None:
        return 50.0   # 双方都无容量数据，中性分
    if ec is None or dc is None:
        return 25.0   # 单方有数据，扣分
    if ec == 0 and dc == 0:
        return 50.0
    max_v = max(ec, dc)
    ratio = min(ec, dc) / max_v
    return round(ratio * 100, 1)


def compute_score(row_vals: list, db_row: dict) -> float:
    """计算 Excel 行与 DB 记录的加权相似度（0–100）。"""
    # 项目名：与中文名和英文名分别比较，取较高分
    excel_name  = safe_str(row_vals[F2_NAME]) or ""
    db_name_cn  = db_row.get("project_name") or ""
    db_name_en  = db_row.get("project_name_en") or ""
    name_sc = max(
        fuzz.WRatio(excel_name, db_name_cn) if excel_name and db_name_cn else 0,
        fuzz.WRatio(excel_name, db_name_en) if excel_name and db_name_en else 0,
    )

    # 实施企业
    excel_entity = safe_str(row_vals[F2_ENTITY]) or ""
    db_entity    = db_row.get("implementing_entity") or ""
    entity_sc = fuzz.WRatio(excel_entity, db_entity) if excel_entity and db_entity else 30

    # 容量
    cap_sc = capacity_score(safe_str(row_vals[F2_CAP]), db_row.get("capacity_mw"))

    # 地点
    excel_loc = safe_str(row_vals[F2_LOC]) or ""
    db_loc    = db_row.get("location") or ""
    loc_sc = fuzz.WRatio(excel_loc, db_loc) if excel_loc and db_loc else 50

    total = (
        name_sc   * 0.40 +
        entity_sc * 0.30 +
        cap_sc    * 0.20 +
        loc_sc    * 0.10
    )
    return round(total, 2)


def gemini_confirm(
    row_vals: list,
    top3: list[tuple[float, dict]],
    client,
) -> dict:
    """
    调用 Gemini 判断 Excel 行是否与 top3 候选中某条 DB 记录描述同一项目。
    返回 {"match": bool, "db_id": int|None, "reason": str}。
    """
    excel_name   = safe_str(row_vals[F2_NAME])   or "N/A"
    excel_iso    = safe_str(row_vals[F2_ISO])    or "N/A"
    excel_loc    = safe_str(row_vals[F2_LOC])    or "N/A"
    excel_cap    = safe_str(row_vals[F2_CAP])    or "N/A"
    excel_entity = safe_str(row_vals[F2_ENTITY]) or "N/A"

    candidates_text = ""
    for rank, (score, c) in enumerate(top3, 1):
        candidates_text += (
            f"\n候选{rank} (相似度{score:.1f}分, DB_ID={c['id']}):\n"
            f"  项目名(中): {c.get('project_name') or 'N/A'}\n"
            f"  项目名(英): {c.get('project_name_en') or 'N/A'}\n"
            f"  国家: {c.get('country') or 'N/A'} ({c.get('country_iso') or 'N/A'})\n"
            f"  地点: {c.get('location') or 'N/A'}\n"
            f"  容量: {c.get('capacity_mw') or 'N/A'} MW\n"
            f"  实施企业: {c.get('implementing_entity') or 'N/A'}\n"
        )

    prompt = (
        "你是一个可再生能源项目数据库专家。请判断以下 Excel 数据行与给出的数据库候选记录是否描述同一个实际项目。\n\n"
        "Excel 行：\n"
        f"- 项目名称: {excel_name}\n"
        f"- 国家 ISO: {excel_iso}\n"
        f"- 地点: {excel_loc}\n"
        f"- 容量: {excel_cap} MW\n"
        f"- 中国实施企业: {excel_entity}\n\n"
        f"数据库候选记录：{candidates_text}\n"
        "判断标准：\n"
        "- 项目名称中英文对应可接受\n"
        "- 容量差异 ±10% 以内可接受\n"
        "- 企业全称与简称/子公司可接受\n"
        "- 如没有候选与 Excel 行描述同一项目，返回 match=false\n\n"
        '只返回 JSON，格式：{"match": true或false, "db_id": 数字或null, "reason": "简短说明"}'
    )

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        result = json.loads(response.text)
        return {
            "match"  : bool(result.get("match", False)),
            "db_id"  : result.get("db_id"),
            "reason" : result.get("reason", ""),
        }
    except Exception as e:
        return {"match": False, "db_id": None, "reason": f"API错误: {e}"}


def apply_update(
    db_id: int,
    row_vals: list,
    cursor: sqlite3.Cursor,
    db_row_current: dict,
    dry_run: bool,
) -> list[str]:
    """
    向 DB 中 id=db_id 的行填充目标字段。
    只填充 DB 字段为空/Unknown 且 Excel 值非空的情况。
    返回实际更新的 DB 列名列表。
    """
    updates: dict[str, str] = {}
    for col_idx, db_col in FIELD_MAP.items():
        excel_val = safe_str(row_vals[col_idx])
        if excel_val is None:
            continue
        db_val = db_row_current.get(db_col)
        if is_empty(db_val):
            updates[db_col] = excel_val

    if updates and not dry_run:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        cursor.execute(
            f"UPDATE extracted_projects SET {set_clause} WHERE id = ?",
            list(updates.values()) + [db_id],
        )
    return list(updates.keys())


def backup_db(db_path: Path) -> None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = db_path.with_suffix(f".db.bak_{ts}")
    shutil.copy2(db_path, bak)
    print(f"[备份] {bak}")


# ── 主流程 ────────────────────────────────────────────────────────────────────
def main(dry_run: bool = False, skip_gemini: bool = False) -> None:
    if not _DB_PATH.exists():
        raise FileNotFoundError(f"数据库不存在: {_DB_PATH}")
    if not _EXCEL_PATH.exists():
        raise FileNotFoundError(f"Excel 文件不存在: {_EXCEL_PATH}")

    # 初始化 Gemini 客户端
    client = None
    if not skip_gemini:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "未找到 GEMINI_API_KEY，请检查 .env 文件，或使用 --skip-gemini 跳过 Gemini 确认。"
            )
        client = genai.Client(api_key=api_key)
        print("[Gemini] 客户端已初始化")

    if not dry_run:
        backup_db(_DB_PATH)

    _LOG_DIR.mkdir(exist_ok=True)
    log_path = _LOG_DIR / "step2_log.csv"

    # 读取 Excel（全部按字符串读）
    df = pd.read_excel(_EXCEL_PATH, dtype=str)
    print(f"[读取] Excel 共 {len(df)} 行，{len(df.columns)} 列")

    # 读取 DB
    db_select_cols = [
        "id", "project_name", "project_name_en", "country", "country_iso",
        "location", "capacity_mw", "implementing_entity",
    ] + list(FIELD_MAP.values())
    db_select_cols = list(dict.fromkeys(db_select_cols))   # 去重，保持顺序

    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(f"SELECT {', '.join(db_select_cols)} FROM extracted_projects")
    all_db_rows = [dict(r) for r in cursor.fetchall()]
    print(f"[读取] DB 共 {len(all_db_rows)} 行")

    # 按 country_iso 分组索引
    iso_index: dict[str, list[dict]] = {}
    for r in all_db_rows:
        iso = (r.get("country_iso") or "").strip().upper()
        if iso:
            iso_index.setdefault(iso, []).append(r)

    # 统计计数
    cnt_auto = cnt_gemini_yes = cnt_gemini_no = cnt_no_candidate = cnt_no_iso = cnt_updated = 0
    log_records: list[dict] = []

    total = len(df)
    for i, excel_row in df.iterrows():
        row_num  = int(i) + 2
        row_vals = excel_row.tolist()

        # 进度显示
        if (int(i) + 1) % 50 == 0:
            print(f"  进度: {int(i) + 1}/{total} 行 ...")

        excel_iso = safe_str(row_vals[F2_ISO])
        if not excel_iso:
            cnt_no_iso += 1
            log_records.append({
                "excel_row": row_num,
                "excel_name": safe_str(row_vals[F2_NAME]) or "",
                "db_id": "", "db_name": "", "score": "",
                "method": "no_iso",
                "fields_updated": "",
                "reason": "Excel ISO 为空",
            })
            continue

        excel_iso_upper = excel_iso.upper()
        candidates = iso_index.get(excel_iso_upper, [])
        if not candidates:
            cnt_no_candidate += 1
            log_records.append({
                "excel_row": row_num,
                "excel_name": safe_str(row_vals[F2_NAME]) or "",
                "db_id": "", "db_name": "", "score": "",
                "method": "no_candidate",
                "fields_updated": "",
                "reason": f"DB 中无 {excel_iso_upper} 国家记录",
            })
            continue

        # 对所有候选打分，取 top-3
        scored = [(compute_score(row_vals, c), c) for c in candidates]
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_candidate = scored[0]
        top3 = scored[:3]

        method = ""
        matched_id   = None
        matched_name = ""
        reason       = ""
        matched_db_row: dict | None = None

        if best_score >= AUTO_MATCH_THRESHOLD:
            method        = "auto"
            matched_id    = best_candidate["id"]
            matched_name  = best_candidate.get("project_name") or best_candidate.get("project_name_en") or ""
            matched_db_row = best_candidate
            cnt_auto += 1

        elif best_score >= SKIP_THRESHOLD:
            if skip_gemini:
                method = "skipped_gemini"
                reason = f"--skip-gemini 模式，最高分 {best_score:.1f}"
            else:
                gemini_result = gemini_confirm(row_vals, top3, client)
                time.sleep(0.5)   # 速率限制

                if gemini_result["match"]:
                    confirm_id = gemini_result.get("db_id")
                    confirmed  = next((c for _, c in top3 if c["id"] == confirm_id), None)
                    if confirmed:
                        method         = "gemini_yes"
                        matched_id     = confirm_id
                        matched_name   = confirmed.get("project_name") or confirmed.get("project_name_en") or ""
                        matched_db_row = confirmed
                        reason         = gemini_result.get("reason", "")
                        cnt_gemini_yes += 1
                    else:
                        method = "gemini_id_mismatch"
                        reason = f"Gemini 返回 db_id={confirm_id} 不在 top3 中"
                        cnt_gemini_no += 1
                else:
                    method = "gemini_no"
                    reason = gemini_result.get("reason", "")
                    cnt_gemini_no += 1
        else:
            method = "no_match"
            reason = f"最高分 {best_score:.1f} < 阈值 {SKIP_THRESHOLD}"

        # 执行更新
        fields_updated: list[str] = []
        if matched_id is not None and matched_db_row is not None:
            fields_updated = apply_update(matched_id, row_vals, cursor, matched_db_row, dry_run)
            if fields_updated:
                cnt_updated += 1

        log_records.append({
            "excel_row"     : row_num,
            "excel_name"    : (safe_str(row_vals[F2_NAME]) or "")[:80],
            "db_id"         : matched_id if matched_id is not None else "",
            "db_name"       : matched_name[:60] if matched_name else "",
            "score"         : f"{best_score:.1f}",
            "method"        : method,
            "fields_updated": ",".join(fields_updated) if fields_updated else "",
            "reason"        : reason,
        })

    if not dry_run:
        conn.commit()
    conn.close()

    # 写审计日志
    fieldnames = ["excel_row", "excel_name", "db_id", "db_name", "score", "method", "fields_updated", "reason"]
    with open(log_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(log_records)

    prefix = "[DRY-RUN] " if dry_run else ""
    print(f"\n{prefix}完成：")
    print(f"  Excel 总行数:          {total}")
    print(f"  无 ISO 码（跳过）:      {cnt_no_iso}")
    print(f"  无 DB 候选（跳过）:     {cnt_no_candidate}")
    print(f"  自动匹配（≥90）:        {cnt_auto}")
    print(f"  Gemini 确认匹配:        {cnt_gemini_yes}")
    print(f"  Gemini/规则 未匹配:     {cnt_gemini_no}")
    print(f"  DB 行已更新字段组:      {cnt_updated if not dry_run else '(dry-run，未实际写入)'}")
    print(f"  审计日志:               {log_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Step 2: AI 辅助模糊匹配并补充字段")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="不实际修改数据库，只打印将要执行的操作"
    )
    parser.add_argument(
        "--skip-gemini", action="store_true",
        help="跳过 Gemini 确认阶段，仅执行得分 ≥90 的自动匹配"
    )
    args = parser.parse_args()
    main(dry_run=args.dry_run, skip_gemini=args.skip_gemini)
