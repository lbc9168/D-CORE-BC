"""dedup_cache + 缓存集成 + 续跑/失败重跑 测试。"""
import sys
from pathlib import Path

import pytest

_root = next(p for p in Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from s02a_duplicate_merger.tools import dedup_cache as dc
import s02a_duplicate_merger.tools.duplicate_merger_tool as dmt


# ─────────────────────────── Task 1：dedup_cache 模块 ───────────────────────────

def _p(name, **kw):
    base = {"project_name": name, "country": "巴基斯坦", "category": "光伏",
            "capacity_mw": "100", "source_links": ["http://a"]}
    base.update(kw)
    return base


def test_pair_key_symmetric():
    a, b = _p("项目A"), _p("项目B", source_links=["http://b"])
    assert dc.pair_key(a, b) == dc.pair_key(b, a)


def test_pair_key_distinguishes_content():
    a, b, c = _p("项目A"), _p("项目B"), _p("项目C")
    assert dc.pair_key(a, b) != dc.pair_key(a, c)


def test_pair_key_stable_across_calls():
    a, b = _p("项目A"), _p("项目B")
    assert dc.pair_key(a, b) == dc.pair_key(dict(a), dict(b))


def test_init_load_upsert_roundtrip(tmp_path):
    db = str(tmp_path / "c.db")
    dc.init_cache(db)
    assert dc.load_cache(db) == {}
    dc.upsert(db, [("k1", 1, "done"), ("k2", 0, "done"), ("k3", None, "failed")])
    got = dc.load_cache(db)
    assert got["k1"] == (1, "done")
    assert got["k2"] == (0, "done")
    assert got["k3"] == (None, "failed")


def test_upsert_replaces(tmp_path):
    db = str(tmp_path / "c.db")
    dc.init_cache(db)
    dc.upsert(db, [("k1", None, "failed")])
    dc.upsert(db, [("k1", 1, "done")])
    assert dc.load_cache(db)["k1"] == (1, "done")


def test_init_idempotent(tmp_path):
    db = str(tmp_path / "c.db")
    dc.init_cache(db); dc.init_cache(db)
    dc.upsert(db, [("k", 1, "done")])
    assert dc.load_cache(db)["k"] == (1, "done")


def test_init_creates_missing_parent_dir(tmp_path):
    """父目录不存在时 init_cache 应自建（缓存在写库前先用，data/db/ 可能尚未建）。"""
    db = str(tmp_path / "data" / "db" / "c.db")   # data/db/ 不存在
    dc.init_cache(db)
    dc.upsert(db, [("k", 0, "done")])
    assert dc.load_cache(db)["k"] == (0, "done")


# ──────────────────── Task 2：_llm_confirm 抛错版 / 兼容版 ────────────────────

def _min_proj(name):
    return {"project_name": name, "project_name_en": "", "source_links": [], "location": "",
            "country": "巴基斯坦", "category": "光伏", "capacity_mw": "1",
            "project_stage": "在建", "project_developer": ""}


class _FakeModels:
    def generate_content(self, *a, **k):
        raise RuntimeError("should be intercepted by _llm_call_with_retry mock")


class _FakeClient:
    models = _FakeModels()


def test_llm_confirm_raising_propagates(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("socket boom")
    monkeypatch.setattr(dmt, "_llm_call_with_retry", boom)
    with pytest.raises(RuntimeError):
        dmt._llm_confirm_raising(_FakeClient(), None, _min_proj("A"), _min_proj("B"))


def test_llm_confirm_wrapper_swallows(monkeypatch):
    monkeypatch.setattr(dmt, "_llm_confirm_raising",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert dmt._llm_confirm(None, None, _min_proj("A"), _min_proj("B")) is False


def test_partial_overlap_relationship_is_not_merged(monkeypatch):
    """项目包部分重叠必须映射为不合并。"""
    class _Response:
        text = '{"relationship":"partial_overlap","confidence":"high","reasoning":"容量和项目数量不同"}'

    monkeypatch.setattr(dmt, "_llm_call_with_retry", lambda *a, **k: _Response())
    assert dmt._llm_confirm_raising(_FakeClient(), None, _min_proj("A"), _min_proj("B")) is False


def test_dedup_prompt_uses_four_relationship_states(monkeypatch):
    """去重调用必须要求四态输出而非仅返回 same_project 布尔值。"""
    captured = {}

    class _Response:
        text = '{"relationship":"distinct","confidence":"high","reasoning":"不同项目"}'

    def fake_call(*args, **kwargs):
        captured["prompt"] = kwargs["contents"]
        return _Response()

    monkeypatch.setattr(dmt, "_llm_call_with_retry", fake_call)
    assert dmt._llm_confirm_raising(_FakeClient(), None, _min_proj("A"), _min_proj("B")) is False
    assert "partial_overlap" in captured["prompt"]
    assert "项目数量、总容量或地域组合不同" in captured["prompt"]


# ──────────────── Task 3：_deduplicate_projects 缓存集成 ────────────────

def _proj_full(name, stage="在建", **kw):
    base = {"record_type": "具体项目", "project_name": name, "project_name_en": "",
            "name_en_quality": "ai_translated", "country": "巴基斯坦", "country_iso": "PAK",
            "bri_country": "是", "ldc": "否", "location": "", "category": "光伏",
            "capacity_mw": "100", "capacity_details": "", "project_stage": stage,
            "project_developer": "", "planned_start_date": "", "planned_complete_date": "",
            "total_investment_usd": "", "investment_date": "", "financing_model": "",
            "financing_details": "", "source_links": [f"http://{name}"],
            "official_website_links": [], "non_official_website_links": [],
            "publish_date": "", "additional_remarks": "", "project_summary": "",
            "excluded": 0, "exclude_reason": "", "entities": []}
    base.update(kw)
    return base


def test_cache_skips_already_confirmed(monkeypatch, tmp_path):
    a, b, c = _proj_full("古杜电站"), _proj_full("信德二期"), _proj_full("旁遮普三期")
    projects = [a, b, c]
    cands = [(0, 1, 60.0), (0, 2, 60.0)]
    cache_db = str(tmp_path / "cache.db")
    dc.init_cache(cache_db)
    dc.upsert(cache_db, [(dc.pair_key(a, b), 1, "done")])
    calls = []
    monkeypatch.setattr(dmt, "_llm_confirm_raising",
                        lambda cl, cf, x, y: calls.append((x["project_name"], y["project_name"])) or False)
    merged = dmt._deduplicate_projects(object(), None, projects, cands, cache_db=cache_db)
    assert len(calls) == 1
    assert len(merged) == 2


def test_cache_persists_and_retries_failed(monkeypatch, tmp_path):
    a, b = _proj_full("古杜电站"), _proj_full("信德二期")
    projects = [a, b]
    cands = [(0, 1, 60.0)]
    cache_db = str(tmp_path / "cache.db")
    seq = {"n": 0}
    def flaky(cl, cf, x, y):
        seq["n"] += 1
        if seq["n"] == 1:
            raise RuntimeError("socket boom")
        return True
    monkeypatch.setattr(dmt, "_llm_confirm_raising", flaky)
    merged = dmt._deduplicate_projects(object(), None, projects, cands, cache_db=cache_db)
    assert seq["n"] == 2
    assert dc.load_cache(cache_db)[dc.pair_key(a, b)] == (1, "done")
    assert len(merged) == 1


def test_no_cache_db_unchanged(monkeypatch):
    """cache_db=None：仍正常确认+合并（no-cache 路径走 _confirm_llm_pairs→_llm_confirm_raising）。"""
    a, b = _proj_full("古杜电站"), _proj_full("信德二期")
    monkeypatch.setattr(dmt, "_llm_confirm_raising", lambda cl, cf, x, y: True)
    merged = dmt._deduplicate_projects(object(), None, [a, b], [(0, 1, 60.0)])
    assert len(merged) == 1


def test_high_score_project_packages_still_require_llm(monkeypatch):
    """项目包即使名称高分也不得绕过四态 LLM 判断自动合并。"""
    a = _proj_full("乌兹别克斯坦新能源项目包", project_summary="六座电站，总容量2.5GW")
    b = _proj_full("乌兹别克斯坦新能源项目包", project_summary="五座电站，总容量2.3GW")
    calls = []
    monkeypatch.setattr(
        dmt,
        "_llm_confirm_raising",
        lambda cl, cf, x, y: calls.append((x["project_summary"], y["project_summary"])) or False,
    )
    merged = dmt._deduplicate_projects(object(), None, [a, b], [(0, 1, 100.0)])
    assert len(calls) == 1
    assert len(merged) == 2


# ──────────────── 并行组装：_assemble_multi_group ────────────────

def test_assemble_multi_group_cross_stage():
    """跨阶段组：按 stage 分摊、各阶段一条、共享 gid（不触 _merge_group，单条阶段直接用）。"""
    a, b = _proj_full("古杜", stage="签约"), _proj_full("古杜", stage="开工")
    out = dmt._assemble_multi_group([0, 1], 7, [a, b], None, None)
    assert len(out) == 2
    assert {r["project_stage"] for r in out} == {"签约", "开工"}
    assert all(r["dedup_group_id"] == "grp-7" for r in out)


def test_assemble_multi_group_same_stage_merges(monkeypatch):
    """同阶段组：调 _merge_group 合一条，单阶段无 gid。"""
    monkeypatch.setattr(dmt, "_merge_group", lambda c, cf, recs: dict(recs[0]))
    a, b = _proj_full("古杜A", stage="在建"), _proj_full("古杜B", stage="在建")
    out = dmt._assemble_multi_group([0, 1], 3, [a, b], None, None)
    assert len(out) == 1
    assert out[0].get("dedup_group_id") is None


def test_parallel_assembly_multiple_groups(monkeypatch):
    """并行组装多个合并组：结果含每组的合并体 + 未合并的单体，总数正确。"""
    monkeypatch.setattr(dmt, "_merge_group", lambda c, cf, recs: dict(recs[0]))
    monkeypatch.setattr(dmt, "_llm_confirm_raising", lambda cl, cf, x, y: True)
    # 3 组：(0,1) 合并、(2,3) 合并、4 单体
    projs = [_proj_full(f"P{i}") for i in range(5)]
    cands = [(0, 1, 60.0), (2, 3, 60.0)]
    merged = dmt._deduplicate_projects(object(), None, projs, cands)
    assert len(merged) == 3   # 两个合并体 + 一个单体


# ──────────────── trust_dedup：从空库全局重建跳过 canonical LLM 重匹配 ────────────────

def test_trust_dedup_creates_own_canonical_no_llm(tmp_path):
    """trust_dedup=True：名称相似的两条非 gid 也各建自己的 canonical，全程不调 LLM。"""
    conn = dmt._init_db(str(tmp_path / "t.db"))
    a = _proj_full("古杜光伏电站")
    b = _proj_full("古杜光伏电站项目")   # 名称高度相似（若走匹配会撞）
    # client=object()：若误触 _find_or_create_canonical / _llm_confirm_canonical 会 AttributeError
    cnt = dmt._write_to_db(conn, [a, b], client=object(), config=None, trust_dedup=True)
    assert cnt["canonical_new"] == 2     # 各建自己的，未合并
    assert cnt["event_new"] == 2
    conn.close()


def test_trust_dedup_gid_reuses_canonical(tmp_path):
    """trust_dedup=True：同 gid 跨阶段两条 → 1 canonical + 2 event。"""
    conn = dmt._init_db(str(tmp_path / "t.db"))
    p1 = _proj_full("古杜", stage="签约", dedup_group_id="grp-0")
    p2 = _proj_full("信德省项目", stage="开工", dedup_group_id="grp-0")
    cnt = dmt._write_to_db(conn, [p1, p2], client=object(), config=None, trust_dedup=True)
    assert cnt["canonical_new"] == 1
    assert cnt["event_new"] == 2
    conn.close()


def test_create_canonical_inserts_row(tmp_path):
    """_create_canonical 直接插一条 canonical 并返回 id。"""
    conn = dmt._init_db(str(tmp_path / "t.db"))
    cid = dmt._create_canonical(conn, _proj_full("某项目"), canonical_cache={})
    row = conn.execute("SELECT project_name FROM canonical_projects WHERE id=?", (cid,)).fetchone()
    assert row[0] == "某项目"
    conn.close()
