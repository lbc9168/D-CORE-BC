"""s02a: project_developer_en（业主英文）应从逻辑项目写进 project_events（与 project_developer 并列）。"""
import sys
from pathlib import Path

_root = next(p for p in Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from s02a_duplicate_merger.tools.duplicate_merger_tool import _init_db, _write_to_db


def test_project_events_has_developer_en_column(tmp_path):
    conn = _init_db(str(tmp_path / "bri.db"))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(project_events)")}
    conn.close()
    assert "project_developer" in cols
    assert "project_developer_en" in cols


def test_write_carries_developer_en_to_event(tmp_path):
    """_write_to_db（无 LLM 降级）把 project_developer_en 写进 project_events。"""
    conn = _init_db(str(tmp_path / "bri.db"))
    try:
        proj = {
            "record_type": "具体项目", "project_name": "某光伏电站", "project_name_en": "",
            "name_en_quality": "ai_translated", "country": "印度尼西亚", "country_iso": "IDN",
            "bri_country": "Yes", "ldc": "No", "location": "", "category": "光伏",
            "capacity_mw": "50", "capacity_details": "", "project_stage": "在建",
            "project_developer": "印尼国家电力公司(PLN)", "project_developer_en": "Indonesia's PLN",
            "planned_start_date": "", "planned_complete_date": "",
            "total_investment_usd": "", "investment_date": "", "financing_model": "Unknown",
            "financing_details": "", "source_links": ["http://a"],
            "official_website_links": [], "non_official_website_links": [],
            "publish_date": "2026-01-01", "additional_remarks": "", "project_summary": "",
            "entities": [{"implementing_entity": "中国电建", "implementing_entity_en": "PowerChina",
                          "subsidiary_name": "", "subsidiary_name_en": "", "involvement_type": "EPC总承包",
                          "subcontract_amount": "", "is_central_soe": 0}],
            "excluded": 0, "exclude_reason": "",
        }
        _write_to_db(conn, [proj], client=None, config=None)
        row = conn.execute(
            "SELECT project_developer, project_developer_en FROM project_events").fetchone()
        assert row[0] == "印尼国家电力公司(PLN)"
        assert row[1] == "Indonesia's PLN"
    finally:
        conn.close()
