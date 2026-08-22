"""P3 (a)：去重分组去掉 stage 维度；跨阶段同项目 → 按阶段分摊 + dedup_group_id 锚定同一 canonical。
P3 (b)：Signal 6 地点/容量硬锚点 + 跨批次 canonical 匹配地点辅助通道 + location_norm 列。
"""
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


def test_mixed_stage_group_two_outputs(monkeypatch):
    """3 条记录：2 条签约同名 + 1 条开工同名 → 输出 2 条（签约合并为 1）均带相同 gid。"""
    a = _proj(project_name="古杜光伏电站", project_stage="签约", capacity_mw="100", source_links=["http://a"])
    b = _proj(project_name="古杜光伏电站", project_stage="签约", capacity_mw="100", source_links=["http://b"])
    c = _proj(project_name="古杜光伏电站", project_stage="开工",  capacity_mw="100", source_links=["http://c"])
    # candidates 由调用者传入：(0,1) 同名同阶段；(0,2) 跨阶段
    monkeypatch.setattr(dmt, "_merge_group", lambda cl, cfg, recs: dict(recs[0]))
    merged = _deduplicate_projects(None, None, [a, b, c], [(0, 1, 100.0), (0, 2, 100.0)])
    assert len(merged) == 2
    gids = [m.get("dedup_group_id") for m in merged]
    assert gids[0] is not None and gids[0] == gids[1]
    stages = {m["project_stage"] for m in merged}
    assert stages == {"签约", "开工"}


# ───────────────────────── P3 (b)：Signal 6 地点/容量锚点 ─────────────────────────

def test_location_capacity_anchor_creates_candidate():
    """名称完全不相似 + 同地名 + 容量 15% 内 → Signal 6 锚点送 LLM 候选。"""
    a = _proj(project_name="达瓦德密风电项目", category="风电", capacity_mw="50",
              location="达瓦德密", source_links=["http://a"])
    b = _proj(project_name="信德绿色能源二期", category="风电", capacity_mw="52",
              location="达瓦德密", source_links=["http://b"])
    assert len(_find_duplicate_candidates([a, b])) == 1


def test_location_anchor_requires_close_capacity():
    """同地名但容量差 10 倍 → 不成候选（防同省项目互串）。"""
    a = _proj(project_name="达瓦德密风电项目", category="风电", capacity_mw="50",
              location="达瓦德密", source_links=["http://a"])
    b = _proj(project_name="信德绿色能源二期", category="风电", capacity_mw="500",
              location="达瓦德密", source_links=["http://b"])
    assert _find_duplicate_candidates([a, b]) == []


def test_location_anchor_prefers_location_norm():
    """原始地名是音译变体（不相等）→ 锚不上；写入 location_norm 后 → 锚上。"""
    a = _proj(project_name="达瓦德密风电项目", category="风电", capacity_mw="50",
              location="达瓦德密", source_links=["http://a"])
    b = _proj(project_name="信德绿色能源二期", category="风电", capacity_mw="52",
              location="达旺德米", source_links=["http://b"])
    assert _find_duplicate_candidates([a, b]) == []
    a["location_norm"] = b["location_norm"] = "达瓦德密"
    assert len(_find_duplicate_candidates([a, b])) == 1


def test_location_anchor_requires_min_capacity_and_both_values():
    """容量缺失或低于 10MW 门槛 → Signal 6 不触发。"""
    a = _proj(project_name="达瓦德密风电项目", category="风电", capacity_mw="",
              location="达瓦德密", source_links=["http://a"])
    b = _proj(project_name="信德绿色能源二期", category="风电", capacity_mw="52",
              location="达瓦德密", source_links=["http://b"])
    assert _find_duplicate_candidates([a, b]) == []
    a["capacity_mw"], b["capacity_mw"] = "5", "5.2"   # 低于 LOCATION_ANCHOR_MIN_MW=10
    assert _find_duplicate_candidates([a, b]) == []


def test_ac_dc_capacity_details_rescue_duplicate_candidate():
    """光伏 AC/DC 代表值不同，但明细共享两种口径时仍应成为候选。"""
    a = _proj(
        project_name="LSS5 BSSB光伏项目", capacity_mw="300",
        capacity_details="300MWac / 459.27MWp", source_links=["http://a"],
    )
    b = _proj(
        project_name="LSS5 BSSB太阳能电站", capacity_mw="459.27",
        capacity_details="300MWac / 459.27MWp", source_links=["http://b"],
    )
    assert len(_find_duplicate_candidates([a, b])) == 1


def test_same_name_with_location_and_developer_conflict_is_not_auto_merged():
    """名称高分也不得覆盖具体场址和开发商同时冲突。"""
    a = _proj(
        project_name="马来西亚30MW太阳能项目", location="霹雳州",
        project_developer="Pekat", source_links=["http://a"],
    )
    b = _proj(
        project_name="马来西亚30MW太阳能项目", location="雪兰莪州",
        project_developer="BGMC", source_links=["http://b"],
    )
    merged = _deduplicate_projects(None, None, [a, b], [(0, 1, 100.0)])
    assert len(merged) == 2


# ───────────────────── P3 (b)：跨批次 canonical 匹配的地点辅助 ─────────────────────

def _seed_canonical(conn, cid="PAK-WD-0001", name="信德风能项目", loc="达瓦德密", cap=50.0):
    conn.execute(
        "INSERT INTO canonical_projects (id, project_name, country, country_iso, category, location, location_norm) "
        "VALUES (?, ?, '巴基斯坦', 'PAK', '风电', ?, ?)",
        (cid, name, loc, loc),
    )
    conn.execute(
        "INSERT INTO project_events (id, canonical_id, project_stage, capacity_mw) VALUES (?, ?, '在建', ?)",
        (f"{cid}-01", cid, cap),
    )
    conn.commit()


def test_canonical_location_assist_sends_to_llm(monkeypatch, tmp_path):
    """名称分 <70 但规范地名相同 + 容量守卫通过 → 送 LLM 确认；确认后匹配既有 canonical。"""
    conn = _init_db(str(tmp_path / "t.db"))
    _seed_canonical(conn)
    calls = []
    monkeypatch.setattr(dmt, "_llm_confirm_canonical",
                        lambda *args, **kw: calls.append(1) or True)
    project = _full_proj(project_name="达瓦德密风电场", category="风电", capacity_mw="52",
                         location="达瓦德密", location_norm="达瓦德密")
    cid, is_new = dmt._find_or_create_canonical(conn, project, client=object(), config=None)
    assert cid == "PAK-WD-0001" and is_new is False
    assert len(calls) == 1
    conn.close()


def test_canonical_no_location_match_creates_new(monkeypatch, tmp_path):
    """名称分 <70 且地点不同 → 不触 LLM，直接新建 canonical。"""
    conn = _init_db(str(tmp_path / "t.db"))
    _seed_canonical(conn)
    calls = []
    monkeypatch.setattr(dmt, "_llm_confirm_canonical",
                        lambda *args, **kw: calls.append(1) or True)
    project = _full_proj(project_name="俾路支风电场", category="风电", capacity_mw="52",
                         location="俾路支", location_norm="俾路支")
    cid, is_new = dmt._find_or_create_canonical(conn, project, client=object(), config=None)
    assert is_new is True and cid != "PAK-WD-0001"
    assert calls == []
    conn.close()


def test_canonical_location_assist_blocked_by_capacity_guard(monkeypatch, tmp_path):
    """规范地名相同但容量差 >20% → 容量守卫拦截，不触 LLM，新建 canonical。"""
    conn = _init_db(str(tmp_path / "t.db"))
    _seed_canonical(conn)  # 既有 canonical 容量 50MW
    calls = []
    monkeypatch.setattr(dmt, "_llm_confirm_canonical",
                        lambda *args, **kw: calls.append(1) or True)
    project = _full_proj(project_name="达瓦德密风电场", category="风电", capacity_mw="500",
                         location="达瓦德密", location_norm="达瓦德密")
    cid, is_new = dmt._find_or_create_canonical(conn, project, client=object(), config=None)
    assert is_new is True and cid != "PAK-WD-0001"
    assert calls == []
    conn.close()


def test_cross_category_exact_name_reuses_immutable_canonical_id(tmp_path):
    """同一项目分类变化后应复用原 ID，而不是因 category 不同创建重复 canonical。"""
    conn = _init_db(str(tmp_path / "t.db"))
    conn.execute(
        "INSERT INTO canonical_projects (id,project_name,country,country_iso,category) "
        "VALUES ('PAK-PV-0001','巴基斯坦达瓦德密能源项目','巴基斯坦','PAK','光伏')"
    )
    conn.execute(
        "INSERT INTO project_events (id,canonical_id,project_stage,capacity_mw) "
        "VALUES ('PAK-PV-0001-01','PAK-PV-0001','在建',50)"
    )
    conn.commit()
    incoming = _full_proj(
        project_name="巴基斯坦达瓦德密能源项目", category="综合能源", capacity_mw="50"
    )
    canonical_id, is_new = dmt._find_or_create_canonical(conn, incoming, client=None, config=None)
    conn.close()
    assert canonical_id == "PAK-PV-0001"
    assert is_new is False


def test_cross_category_same_country_and_capacity_without_name_match_stays_separate(tmp_path):
    """跨类别救援不得仅凭国家和相同容量误并两个不同项目。"""
    conn = _init_db(str(tmp_path / "t.db"))
    _seed_canonical(conn, name="信德风能项目", cap=50.0)
    incoming = _full_proj(
        project_name="俾路支太阳能园区", category="光伏", capacity_mw="50", location="俾路支"
    )
    canonical_id, is_new = dmt._find_or_create_canonical(conn, incoming, client=None, config=None)
    conn.close()
    assert is_new is True
    assert canonical_id != "PAK-WD-0001"


# ───────────────────── P3 (c)：批内 in-memory 地点归一化接线 ─────────────────────

def test_apply_location_norm_sets_norm_per_country(monkeypatch):
    import bri_location
    monkeypatch.setattr(
        bri_location, "build_location_mapping",
        lambda grouped, client: {"巴基斯坦": {"达旺德米": "达瓦德密"}},
    )
    a = _proj(location="达旺德米")
    b = _proj(location="达瓦德密")
    c = _proj(location="达旺德米", country="肯尼亚")   # 别国同名地名不受巴基斯坦映射影响
    n = dmt._apply_location_norm([a, b, c], client=object())
    assert n == 1
    assert a["location_norm"] == "达瓦德密"
    assert "location_norm" not in b and "location_norm" not in c


def test_apply_location_norm_noop_without_client():
    a = _proj(location="达旺德米")
    assert dmt._apply_location_norm([a], client=None) == 0
    assert "location_norm" not in a
