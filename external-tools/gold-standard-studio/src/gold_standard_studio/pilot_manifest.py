"""Define the fixed ten-article Stage 3 Pilot without sampling or production writes."""

from __future__ import annotations

from typing import Any

PILOT_ARTICLES: tuple[dict[str, Any], ...] = (
    {"article_id": 9027, "risk": 2, "difficulty": "中", "kind": "储能双项目", "expected_status": "valid"},
    {"article_id": 10069, "risk": 2, "difficulty": "中", "kind": "无中方参与光伏融资", "expected_status": "no_chinese"},
    {"article_id": 13649, "risk": 2, "difficulty": "中", "kind": "英文风电投资", "expected_status": "valid"},
    {"article_id": 17413, "risk": 1, "difficulty": "低", "kind": "输变电", "expected_status": "valid"},
    {"article_id": 1586, "risk": 3, "difficulty": "高", "kind": "新能源制造业", "expected_status": "valid"},
    {"article_id": 18491, "risk": 3, "difficulty": "高", "kind": "无中方参与火电", "expected_status": "no_chinese"},
    {"article_id": 14595, "risk": 3, "difficulty": "高", "kind": "光柴储微网", "expected_status": "valid"},
    {"article_id": 1533, "risk": 4, "difficulty": "很高", "kind": "复杂多项目与历史项目", "expected_status": "valid"},
    {"article_id": 27309, "risk": 2, "difficulty": "中", "kind": "非能源矿业负样本", "expected_status": "irrelevant"},
    {"article_id": 27082, "risk": 3, "difficulty": "高", "kind": "无中方参与融资负样本", "expected_status": "no_chinese"},
)

PILOT_BY_ID = {item["article_id"]: item for item in PILOT_ARTICLES}


def pilot_article_ids() -> list[int]:
    """Return Pilot article IDs in conflict-first risk order with the baseline first."""
    baseline = [item["article_id"] for item in PILOT_ARTICLES if item["article_id"] == 9027]
    remaining = sorted(
        (item for item in PILOT_ARTICLES if item["article_id"] != 9027),
        key=lambda item: (-item["risk"], item["article_id"]),
    )
    return baseline + [item["article_id"] for item in remaining]
