"""Layer 1: 格式校验 — 纯 Python，无 API 调用，毫秒级"""

import re
from typing import Any

# ── 合法枚举值 ────────────────────────────────────────────────────────────────

VALID_CATEGORIES = {
    "新能源制造业", "输变电建设", "火电", "光伏", "风电", "水电",
    "核电", "地热及余热", "生物质", "储能", "综合能源",
}

VALID_PROJECT_STAGES = {
    "拟开发", "收购", "中标", "签约", "开工", "在建", "竣工", "投入运营", "Unknown",
}

VALID_INVOLVEMENT_TYPES = {
    "BOT/BOOT", "EPC总承包", "设计/施工服务方", "股权投资方",
    "设备供应商", "投融资+EPC", "融资方", "Unknown",
}

VALID_FINANCING_MODELS = {
    "信贷融资", "债券融资", "股权融资", "混合融资", "其他", "Unknown",
}

VALID_RECORD_TYPES = {"具体项目", "设备订单", "一般合作"}

# ISO 3166-1 alpha-3 正则（3个大写字母）
_ISO3_RE = re.compile(r"^[A-Z]{3}$")

# 日期格式 YYYY-MM-DD
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 中文字符检测
_CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")


def _is_empty(v: Any) -> bool:
    """判断值是否视为空。"""
    if v is None:
        return True
    s = str(v).strip()
    return s in ("", "nan", "NA", "None", "null")


def run_format_checks(records: list[dict]) -> list[str]:
    """
    对一批提取记录执行格式校验。

    Args:
        records: 从 projects.db 查出的字典列表，键为列名

    Returns:
        错误描述列表。空列表表示全部通过。
    """
    errors: list[str] = []

    for rec in records:
        rid = rec.get("id", "?")
        prefix = f"项目 #{rid}"

        # ── 必填字段 ──
        for field in ("project_name", "country", "category", "implementing_entity"):
            if _is_empty(rec.get(field)):
                errors.append(f"{prefix} 缺少必填字段: {field}")

        # ── 项目名称必须含中文 ──
        pname = str(rec.get("project_name", ""))
        if pname and not _CHINESE_RE.search(pname):
            errors.append(f"{prefix} 名称未使用中文: {pname[:40]}")

        # ── 国家ISO ──
        iso = str(rec.get("country_iso", "")).strip()
        if iso and iso != "NA" and not _ISO3_RE.match(iso):
            errors.append(f"{prefix} 国家ISO无效: {iso}")

        # ── Category 枚举 ──
        cat = str(rec.get("category", "")).strip()
        if cat and cat not in VALID_CATEGORIES:
            errors.append(f"{prefix} 分类不合法: {cat}")

        # ── 装机容量 ──
        cap = str(rec.get("capacity_mw", "")).strip()
        if cap and not _is_empty(cap):
            try:
                cap_val = float(cap)
                if cap_val <= 0 or cap_val >= 50000:
                    errors.append(f"{prefix} 容量异常: {cap} MW（应 0 < x < 50000）")
            except ValueError:
                errors.append(f"{prefix} 容量无法解析为数字: {cap}")

        # ── Project Stage 枚举 ──
        stage = str(rec.get("project_stage", "")).strip()
        if stage and stage not in VALID_PROJECT_STAGES:
            errors.append(f"{prefix} 阶段不合法: {stage}")

        # ── 参与方式枚举 ──
        inv = str(rec.get("involvement_type", "")).strip()
        if inv and inv not in VALID_INVOLVEMENT_TYPES:
            errors.append(f"{prefix} 参与方式不合法: {inv}")

        # ── 融资模式枚举 ──
        fin = str(rec.get("financing_model", "")).strip()
        if fin and not _is_empty(fin) and fin not in VALID_FINANCING_MODELS:
            errors.append(f"{prefix} 融资模式不合法: {fin}")

        # ── 日期格式 ──
        for date_field in ("planned_start_date", "planned_complete_date"):
            dval = str(rec.get(date_field, "")).strip()
            if dval and not _is_empty(dval) and not _DATE_RE.match(dval):
                errors.append(f"{prefix} 日期格式错误: {date_field}={dval}")

        # ── Record Type 枚举 ──
        rtype = str(rec.get("record_type", "")).strip()
        if rtype and rtype not in VALID_RECORD_TYPES:
            errors.append(f"{prefix} 记录类型不合法: {rtype}")

    return errors
