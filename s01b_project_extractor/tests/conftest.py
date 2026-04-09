"""
共享 Fixture — 供所有测试文件使用

提供：
- make_record()      构造一条合法的 extracted_projects 记录（字典）
- make_records(n)    构造 n 条合法记录
"""

import pytest


def make_record(overrides: dict | None = None) -> dict:
    """
    返回一条通过所有 format_checks 的最小合法记录。
    overrides 中的字段会覆盖默认值，方便单字段测试。
    """
    base = {
        "id": 1,
        # 必填
        "project_name": "巴基斯坦光伏电站项目",
        "country": "巴基斯坦",
        "category": "光伏",
        "implementing_entity": "中国电建",
        # 可选枚举
        "record_type": "具体项目",
        "project_stage": "签约",
        "involvement_type": "EPC总承包",
        "financing_model": None,
        # 可选数值
        "capacity_mw": "100",
        "country_iso": "PAK",
        # 可选日期
        "planned_start_date": None,
        "planned_complete_date": None,
        # 其他字段（guardrail 中不做格式校验，置空即可）
        "project_name_en": None,
        "subsidiary_name": None,
        "project_summary": "中国电建在巴基斯坦签署100MW光伏项目EPC合同。",
        "batch_id": "test_20260409",
        "source_link": "https://example.com/news/1",
        "publish_date": "2026-01-15",
    }
    if overrides:
        base.update(overrides)
    return base


def make_records(n: int, base_overrides: dict | None = None) -> list[dict]:
    """构造 n 条合法记录，id 自动递增。"""
    return [
        make_record({"id": i + 1, **(base_overrides or {})})
        for i in range(n)
    ]
