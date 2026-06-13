"""P3 (a)：去重分组去掉 stage 维度；跨阶段同项目 → 按阶段分摊 + dedup_group_id 锚定同一 canonical。"""
import sys
from pathlib import Path

_root = next(p for p in Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import s02a_duplicate_merger.tools.duplicate_merger_tool as dmt
from s02a_duplicate_merger.tools.duplicate_merger_tool import (
    _deduplicate_projects, _find_duplicate_candidates, _init_db, _write_to_db,
)


def _proj(**kw):
    """候选筛查/合并用的最小项目 dict。"""
    base = {
        "project_name": "", "project_name_en": "", "country": "巴基斯坦", "country_iso": "PAK",
        "category": "光伏", "project_stage": "在建", "capacity_mw": "", "location": "",
        "source_links": [], "entities": [],
    }
    base.update(kw)
    return base


def _full_proj(**kw):
    """_write_to_db 用的全字段项目 dict。"""
    base = {
        "record_type": "具体项目", "project_name": "", "project_name_en": "",
        "name_en_quality": "ai_translated",
        "country": "巴基斯坦", "country_iso": "PAK", "bri_country": "是", "ldc": "否",
        "location": "信德省", "category": "光伏", "capacity_mw": "100", "capacity_details": "",
        "project_stage": "在建", "project_developer": "",
        "planned_start_date": "", "planned_complete_date": "",
        "total_investment_usd": "", "investment_date": "",
        "financing_model": "", "financing_details": "",
        "source_links": [], "official_website_links": [], "non_official_website_links": [],
        "publish_date": "", "additional_remarks": "", "project_summary": "",
        "excluded": 0, "exclude_reason": "", "entities": [],
    }
    base.update(kw)
    return base


def test_cross_stage_records_become_candidates():
    """同国同类同名、不同 stage 的两条记录应进同一候选组（改前预分组按 stage 隔开 → 0 候选）。"""
    a = _proj(project_name="古杜光伏电站", project_stage="签约", capacity_mw="100", source_links=["http://a"])
    b = _proj(project_name="古杜光伏电站", project_stage="开工", capacity_mw="100", source_links=["http://b"])
    cands = _find_duplicate_candidates([a, b])
    assert len(cands) == 1


def test_cross_stage_group_keeps_one_record_per_stage():
    """确认重复的跨阶段组：不折叠成一条，按阶段各留一条，且共享 dedup_group_id。"""
    a = _proj(project_name="古杜光伏电站", project_stage="签约", capacity_mw="100", source_links=["http://a"])
    b = _proj(project_name="古杜光伏电站", project_stage="开工", capacity_mw="100", source_links=["http://b"])
    # score=100 走 ≥97 高分自动确认路径，不触 LLM，client/config 传 None 安全
    merged = _deduplicate_projects(None, None, [a, b], [(0, 1, 100.0)])
    assert len(merged) == 2
    assert {m["project_stage"] for m in merged} == {"签约", "开工"}
    gids = [m.get("dedup_group_id") for m in merged]
    assert gids[0] is not None and gids[0] == gids[1]


def test_same_stage_group_still_merges_to_single_record(monkeypatch):
    """同阶段重复组：行为不变，仍合并为一条，且不带 dedup_group_id。"""
    a = _proj(project_name="古杜光伏电站", capacity_mw="100", source_links=["http://a"])
    b = _proj(project_name="古杜光伏电站", capacity_mw="100", source_links=["http://b"])
    monkeypatch.setattr(dmt, "_merge_group", lambda c, cfg, recs: dict(recs[0]))
    merged = _deduplicate_projects(None, None, [a, b], [(0, 1, 100.0)])
    assert len(merged) == 1
    assert merged[0].get("dedup_group_id") is None


def test_write_to_db_same_gid_shares_canonical(tmp_path):
    """相同 dedup_group_id 的两条记录（名称完全不同）落库为 1 canonical + 2 events。"""
    conn = _init_db(str(tmp_path / "t.db"))
    p1 = _full_proj(project_name="古杜光伏电站", project_stage="签约", dedup_group_id="grp-0")
    p2 = _full_proj(project_name="信德省太阳能项目", project_stage="开工", dedup_group_id="grp-0")
    cnt = _write_to_db(conn, [p1, p2])  # client=None 降级路径，零 LLM
    assert cnt["canonical_new"] == 1
    assert cnt["event_new"] == 2
    conn.close()
