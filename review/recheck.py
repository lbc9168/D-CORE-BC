"""recheck：第二遍带原文 AI 复核编排器——从 review_queue 高+中驱动，复用 critic/apply/run_review 闭包。"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sqlite3
import sys
from datetime import datetime
from typing import Callable, Optional

_root = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from pydantic import ValidationError  # noqa: E402

from review import apply, critic, queue  # noqa: E402
from review.export_queue import export_pending  # noqa: E402
from review.run_review import (  # noqa: E402  复用闭包工厂 + 默认校验器（不重复造）
    _make_update_field, _make_exclude_field, _default_value_validator, _UPDATABLE,
)
from eval.article_store import get_article  # noqa: E402

logger = logging.getLogger(__name__)

_HI_MID = ("高", "中")


def load_recheck_candidates(review_db: str, projects_db: str) -> list[dict]:
    """读 review_queue 中 severity∈{高,中} 的行，按 canonical_id(=extracted_projects.id) 聚合为候选。

    每候选：{row_id, source_link, suspicious_with_prior:[{field,prior_reason,prior_suggested,
    prior_conf,prior_evidence,current_value}], full_project:{...}}。
    """
    rconn = sqlite3.connect(str(review_db))
    rconn.row_factory = sqlite3.Row
    try:
        qrows = rconn.execute(
            "SELECT canonical_id, source_link, reason_type, field, suggested_value, "
            "critic_confidence, evidence FROM review_queue WHERE severity IN (?, ?)",
            _HI_MID,
        ).fetchall()
    finally:
        rconn.close()

    pconn = sqlite3.connect(str(projects_db))
    pconn.row_factory = sqlite3.Row
    try:
        pcols = {r[1] for r in pconn.execute("PRAGMA table_info(extracted_projects)")}
        prows = {r["id"]: r for r in pconn.execute("SELECT * FROM extracted_projects")}
    finally:
        pconn.close()

    agg: dict[int, dict] = {}
    for q in qrows:
        cid = q["canonical_id"]
        if not (cid and str(cid).isdigit()):
            continue
        rid = int(cid)
        pr = prows.get(rid)
        if pr is None:
            logger.warning("recheck 候选 row_id=%s 在 extracted_projects 找不到，跳过", rid)
            continue
        fld = q["field"]
        current_value = pr[fld] if (fld and fld in pcols) else None
        prior = {
            "field": fld, "prior_reason": q["reason_type"], "prior_suggested": q["suggested_value"],
            "prior_conf": q["critic_confidence"], "prior_evidence": q["evidence"],
            "current_value": current_value,
        }
        if rid not in agg:
            agg[rid] = {
                "row_id": rid, "source_link": q["source_link"] or (pr["source_link"] if "source_link" in pcols else ""),
                "suspicious_with_prior": [],
                "full_project": {
                    "project_name": pr["project_name"] if "project_name" in pcols else "",
                    "category": pr["category"] if "category" in pcols else "",
                    "capacity_mw": pr["capacity_mw"] if "capacity_mw" in pcols else "",
                    "involvement_type": pr["involvement_type"] if "involvement_type" in pcols else "",
                    "implementing_entity": pr["implementing_entity"] if "implementing_entity" in pcols else "",
                    "source_link": pr["source_link"] if "source_link" in pcols else "",
                },
            }
        agg[rid]["suspicious_with_prior"].append(prior)
    return list(agg.values())
