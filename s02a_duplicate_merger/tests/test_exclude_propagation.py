"""s02a: extracted_projects.excluded 应以 OR 语义传播到 canonical_projects.excluded。"""
import sys
from pathlib import Path
import pandas as pd

_root = next(p for p in Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from s02a_duplicate_merger.tools.duplicate_merger_tool import (
    _group_into_logical_projects, _init_db, _write_to_db,
)


def _df(rows):
    """rows: list of dict（DB 列名）→ 模拟 _load_from_db 后已 rename 的 df。"""
    return pd.DataFrame(rows)


def test_group_ors_excluded_within_group():
    """同组（同信源/名/国/类/容量/阶段）多成员，任一 excluded=1 → 逻辑项目 excluded=1。"""
    df = _df([
        {"Source Link": "http://a", "Project Name": "X项目", "Country": "肯尼亚", "Category": "光伏",
         "Capacity (MW)": "50", "Project Stage": "在建", "Country ISO": "KEN",
         "Implementing Chinese Entity": "中国电建", "Type of involvement of Chinese Entities": "EPC总承包",
         "excluded": "0", "exclude_reason": ""},
        {"Source Link": "http://a", "Project Name": "X项目", "Country": "肯尼亚", "Category": "光伏",
         "Capacity (MW)": "50", "Project Stage": "在建", "Country ISO": "KEN",
         "Implementing Chinese Entity": "中国能建", "Type of involvement of Chinese Entities": "设备供应商",
         "excluded": "1", "exclude_reason": "油气"},
    ])
    projs = _group_into_logical_projects(df)
    assert len(projs) == 1
    assert projs[0]["excluded"] == 1
    assert "油气" in (projs[0]["exclude_reason"] or "")


def test_group_excluded_zero_when_all_in_scope():
    df = _df([
        {"Source Link": "http://b", "Project Name": "Y项目", "Country": "肯尼亚", "Category": "光伏",
         "Capacity (MW)": "10", "Project Stage": "在建", "Country ISO": "KEN",
         "Implementing Chinese Entity": "中国电建", "Type of involvement of Chinese Entities": "EPC总承包",
         "excluded": "0", "exclude_reason": ""},
    ])
    projs = _group_into_logical_projects(df)
    assert projs[0]["excluded"] == 0


def test_write_propagates_excluded_to_canonical(tmp_path):
    """_write_to_db（无 LLM 降级）应把 excluded 写进 canonical_projects。"""
    db = str(tmp_path / "bri.db")
    conn = _init_db(db)
    try:
        proj = {
            "record_type": "具体项目", "project_name": "某油气电站", "project_name_en": "",
            "name_en_quality": "ai_translated", "country": "肯尼亚", "country_iso": "KEN",
            "bri_country": "Yes", "ldc": "No", "location": "", "category": "光伏",
            "capacity_mw": "50", "capacity_details": "", "project_stage": "在建",
            "project_developer": "", "planned_start_date": "", "planned_complete_date": "",
            "total_investment_usd": "", "investment_date": "", "financing_model": "Unknown",
            "financing_details": "", "source_links": ["http://a"],
            "official_website_links": [], "non_official_website_links": [],
            "publish_date": "2026-01-01", "additional_remarks": "", "project_summary": "",
            "entities": [{"implementing_entity": "中国电建", "implementing_entity_en": "",
                          "subsidiary_name": "", "subsidiary_name_en": "", "involvement_type": "EPC总承包",
                          "subcontract_amount": "", "is_central_soe": 0}],
            "excluded": 1, "exclude_reason": "油气管道，非发电",
        }
        _write_to_db(conn, [proj], client=None, config=None)
        row = conn.execute("SELECT excluded, excluded_reason FROM canonical_projects").fetchone()
        assert row[0] == 1
        assert "油气" in (row[1] or "")
    finally:
        conn.close()
