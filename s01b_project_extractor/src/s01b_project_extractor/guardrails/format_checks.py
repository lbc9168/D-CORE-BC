"""Layer 1: 格式校验 — 纯 Python，无 API 调用，毫秒级"""

import re
from typing import Any

import bri_enums as _enums

# ── 合法枚举集合从单一可信源 bri_enums 动态构建（禁硬编码；新枚举值自动生效）。


def valid_categories_set() -> set[str]:
    """合法 category 集合（含 Unknown）。"""
    return set(_enums.valid_categories())


def valid_stages_set() -> set[str]:
    """合法 project_stage 集合。"""
    return set(_enums.valid_stages())


def valid_involvement_set() -> set[str]:
    """合法 involvement_type 集合。"""
    return set(_enums.valid_involvement())


def valid_financing_set() -> set[str]:
    """合法 financing_model 集合。"""
    return set(_enums.valid_financing())


# Record Type 非 enums_mapping.json 管辖（提取层内部三分类），保留硬编码并注明。
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


def run_format_checks_with_ids(
    records: list[dict],
) -> tuple[list[str], set[int]]:
    """
    对一批提取记录执行格式校验，同时返回失败记录的 ID 集合。

    Args:
        records: 从 projects.db 查出的字典列表，键为列名

    Returns:
        (errors, failed_ids)：
            errors     — 错误描述列表，空列表表示全部通过。
            failed_ids — 有至少一条错误的记录 id（int）集合；id 非 int 时跳过。
    """
    errors: list[str] = []
    failed_ids: set[int] = set()

    # 枚举集合在循环外构建一次，避免逐条记录重复加载 JSON（性能优化）。
    valid_cats = valid_categories_set()
    valid_stages = valid_stages_set()
    valid_inv = valid_involvement_set()
    valid_fin = valid_financing_set()

    for rec in records:
        rid = rec.get("id", "?")
        prefix = f"项目 #{rid}"
        rec_errors: list[str] = []

        # ── 必填字段 ──
        for field in ("project_name", "country", "category", "implementing_entity"):
            if _is_empty(rec.get(field)):
                rec_errors.append(f"{prefix} 缺少必填字段: {field}")

        # ── 项目名称必须含中文 ──
        pname = str(rec.get("project_name", ""))
        if pname and not _CHINESE_RE.search(pname):
            rec_errors.append(f"{prefix} 名称未使用中文: {pname[:40]}")

        # ── 国家ISO ──
        iso = str(rec.get("country_iso", "")).strip()
        if iso and iso != "NA" and not _ISO3_RE.match(iso):
            rec_errors.append(f"{prefix} 国家ISO无效: {iso}")

        # ── Category 枚举 ──
        cat = str(rec.get("category", "")).strip()
        if cat and cat not in valid_cats:
            rec_errors.append(f"{prefix} 分类不合法: {cat}")

        # ── 装机容量 ──
        cap = str(rec.get("capacity_mw", "")).strip()
        if cap and not _is_empty(cap):
            try:
                cap_val = float(cap)
                if cap_val <= 0 or cap_val >= 50000:
                    rec_errors.append(f"{prefix} 容量异常: {cap} MW（应 0 < x < 50000）")
            except ValueError:
                rec_errors.append(f"{prefix} 容量无法解析为数字: {cap}")

        # ── Project Stage 枚举 ──
        stage = str(rec.get("project_stage", "")).strip()
        if stage and stage not in valid_stages:
            rec_errors.append(f"{prefix} 阶段不合法: {stage}")

        # ── 参与方式枚举 ──
        inv = str(rec.get("involvement_type", "")).strip()
        if inv and inv not in valid_inv:
            rec_errors.append(f"{prefix} 参与方式不合法: {inv}")

        # ── 融资模式枚举 ──
        fin = str(rec.get("financing_model", "")).strip()
        if fin and not _is_empty(fin) and fin not in valid_fin:
            rec_errors.append(f"{prefix} 融资模式不合法: {fin}")

        # ── 日期格式 ──
        for date_field in ("planned_start_date", "planned_complete_date"):
            dval = str(rec.get(date_field, "")).strip()
            if dval and not _is_empty(dval) and not _DATE_RE.match(dval):
                rec_errors.append(f"{prefix} 日期格式错误: {date_field}={dval}")

        # ── Record Type 枚举 ──
        rtype = str(rec.get("record_type", "")).strip()
        if rtype and rtype not in VALID_RECORD_TYPES:
            rec_errors.append(f"{prefix} 记录类型不合法: {rtype}")

        if rec_errors:
            errors.extend(rec_errors)
            if isinstance(rid, int):
                failed_ids.add(rid)

    return errors, failed_ids


def run_format_checks(records: list[dict]) -> list[str]:
    """
    对一批提取记录执行格式校验（向后兼容接口）。

    Args:
        records: 从 projects.db 查出的字典列表，键为列名

    Returns:
        错误描述列表。空列表表示全部通过。
    """
    errors, _ = run_format_checks_with_ids(records)
    return errors
