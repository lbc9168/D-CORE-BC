"""en_translate_residual（2B-6 剩余名称翻译回填）：扫描口径 / 四层解析 / fill-if-null / 残余缓存复用。"""
import json
import sqlite3

from s02b_entity_normalizer import en_translate_residual as etr
from s02b_entity_normalizer.company_lookup import build_index

REG = {"_meta": {"version": 1, "generated": "x", "source": "t"},
       "companies": [{"canonical_cn": "阿布扎比未来能源公司", "canonical_en": "Masdar",
                      "aliases": ["Masdar", "马斯达尔"], "is_chinese": False, "parent_cn": None, "source": "t"}]}


def _idx(tmp_path):
    p = tmp_path / "r.json"; p.write_text(json.dumps(REG, ensure_ascii=False), encoding="utf-8")
    return build_index(str(p), None)


def _empty_cache(tmp_path):
    p = tmp_path / "cache.json"; p.write_text("{}", encoding="utf-8")
    return str(p)


def _mk_db(tmp_path):
    """夹具：P1 正常 / P2 excluded；覆盖 en 空、en 含中文、en 有效三态与 Unknown 占位。"""
    db = str(tmp_path / "main.db")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE canonical_projects (id TEXT PRIMARY KEY, excluded INTEGER DEFAULT 0);
        CREATE TABLE project_events (id TEXT PRIMARY KEY, canonical_id TEXT,
            project_developer TEXT, project_developer_en TEXT);
        CREATE TABLE chinese_entities (id INTEGER PRIMARY KEY, event_id TEXT,
            implementing_entity TEXT, implementing_entity_en TEXT,
            subsidiary_name TEXT, subsidiary_name_en TEXT);
    """)
    conn.executemany("INSERT INTO canonical_projects VALUES (?,?)", [("P1", 0), ("P2", 1)])
    conn.executemany("INSERT INTO project_events VALUES (?,?,?,?)", [
        ("P1-01", "P1", "小众业主甲", None),          # en 空 → 待翻
        ("P1-02", "P1", "小众业主乙", "小众业主乙"),   # en 含中文 → 待翻
        ("P1-03", "P1", "大公司", "Big Co."),         # en 有效 → 不动
        ("P2-01", "P2", "排除项目业主", None),         # excluded 项目 → 不扫
    ])
    conn.executemany(
        "INSERT INTO chinese_entities(event_id, implementing_entity, implementing_entity_en) VALUES (?,?,?)",
        [("P1-01", "小众实体丙", ""), ("P1-01", "Unknown", None)])
    conn.commit(); conn.close()
    return db


def test_scan_only_untranslated_nonexcluded(tmp_path):
    """扫描只取：en 空/含中文、挂在 excluded=0 项目下、非占位、含中文的 cn 值。"""
    conn = sqlite3.connect(_mk_db(tmp_path))
    rows = etr.scan_targets(conn)
    conn.close()
    cns = {(r["table"], r["cn"]) for r in rows}
    assert ("project_events", "小众业主甲") in cns
    assert ("project_events", "小众业主乙") in cns
    assert ("chinese_entities", "小众实体丙") in cns
    assert ("project_events", "大公司") not in cns
    assert ("project_events", "排除项目业主") not in cns
    assert not any(r["cn"] == "Unknown" for r in rows)


def test_resolve_residual_tier_order(tmp_path):
    """部件级优先级：registry > 主缓存 > 残余缓存；ASCII 直通；全未中才报 missing。"""
    idx = _idx(tmp_path)
    cache = {"缓存公司": {"en_abbr": "CacheCo"}}
    residual = {"残余公司": {"en": "ResidualCo"}}
    en, missing = etr.resolve_residual("马斯达尔; 缓存公司; 残余公司; ACME; 新公司", cache, residual, idx)
    assert en is None and missing == ["新公司"]
    en2, missing2 = etr.resolve_residual("马斯达尔; 缓存公司; 残余公司; ACME", cache, residual, idx)
    assert en2 == "Masdar; CacheCo; ResidualCo; ACME" and missing2 == []


def test_residual_cache_value_with_cjk_is_invalid():
    """残余缓存值含中文视为无效条目，不采用。"""
    en, missing = etr.resolve_residual("某公司", {}, {"某公司": {"en": "某 Company"}}, None)
    assert en is None and missing == ["某公司"]


_TRANS = {"小众业主甲": "Dev A Co.", "小众业主乙": "Dev B Co.", "小众实体丙": "Ent C Co."}


def test_apply_translates_fills_and_caches(tmp_path):
    """apply：LLM 只翻缺口部件；fill-if-null 落库（有效 en 不动、excluded 不动）；写残余缓存与 CSV。"""
    db = _mk_db(tmp_path)
    residual_path = tmp_path / "residual.json"
    calls = []

    def fake_translate(names):
        calls.append(sorted(names))
        return {n: _TRANS[n] for n in names}

    report = etr.run_residual_translate(
        db, cache_path=_empty_cache(tmp_path), residual_path=str(residual_path),
        use_registry=False, apply=True, translate_fn=fake_translate,
        out_csv=str(tmp_path / "new.csv"))
    assert sorted(sum(calls, [])) == ["小众业主乙", "小众业主甲", "小众实体丙"]
    assert report["rows_updated"] == 3 and report["parts_translated"] == 3

    conn = sqlite3.connect(db)
    got = dict(conn.execute("SELECT id, project_developer_en FROM project_events").fetchall())
    ent = conn.execute("SELECT implementing_entity_en FROM chinese_entities WHERE implementing_entity='小众实体丙'").fetchone()[0]
    conn.close()
    assert got["P1-01"] == "Dev A Co."
    assert got["P1-02"] == "Dev B Co."          # 含中文的旧 en 被替换
    assert got["P1-03"] == "Big Co."            # 有效 en 不动
    assert got["P2-01"] is None                 # excluded 项目不动
    assert ent == "Ent C Co."
    saved = json.loads(residual_path.read_text(encoding="utf-8"))
    assert saved["小众业主甲"]["en"] == "Dev A Co." and saved["小众业主甲"]["verified"] is False
    assert (tmp_path / "new.csv").exists()


def test_second_run_hits_residual_cache_no_llm(tmp_path):
    """缓存复用：第二次遇到同名，直接回填，不再调 LLM。"""
    db = _mk_db(tmp_path)
    residual_path = tmp_path / "residual.json"
    cache = _empty_cache(tmp_path)
    etr.run_residual_translate(db, cache_path=cache, residual_path=str(residual_path),
                               use_registry=False, apply=True,
                               translate_fn=lambda names: {n: _TRANS[n] for n in names},
                               out_csv=str(tmp_path / "a.csv"))
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO project_events VALUES ('P1-04','P1','小众业主甲',NULL)")
    conn.commit(); conn.close()

    def boom(names):
        raise AssertionError(f"缓存应命中，不得调 LLM: {names}")

    report = etr.run_residual_translate(db, cache_path=cache, residual_path=str(residual_path),
                                        use_registry=False, apply=True, translate_fn=boom,
                                        out_csv=str(tmp_path / "b.csv"))
    assert report["rows_updated"] == 1 and report["parts_translated"] == 0
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT project_developer_en FROM project_events WHERE id='P1-04'").fetchone()[0] == "Dev A Co."
    conn.close()


def test_invalid_translation_not_cached_not_applied(tmp_path):
    """翻译结果含中文/为空 → 不写缓存、不落库（防污染，等下次重试）。"""
    db = _mk_db(tmp_path)
    residual_path = tmp_path / "residual.json"
    report = etr.run_residual_translate(
        db, cache_path=_empty_cache(tmp_path), residual_path=str(residual_path),
        use_registry=False, apply=True,
        translate_fn=lambda names: {n: "还是中文" for n in names},
        out_csv=str(tmp_path / "new.csv"))
    assert report["parts_translated"] == 0 and report["parts_failed"] == 3
    assert report["rows_updated"] == 0
    assert not residual_path.exists() or json.loads(residual_path.read_text(encoding="utf-8")) == {}
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT project_developer_en FROM project_events WHERE id='P1-01'").fetchone()[0] is None
    conn.close()


def test_multipart_translates_only_missing_part(tmp_path):
    """多公司串：主缓存已中的部件不重复翻，只翻缺口段，整串拼 '; ' 回填。"""
    db = str(tmp_path / "m.db")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE canonical_projects (id TEXT PRIMARY KEY, excluded INTEGER DEFAULT 0);
        CREATE TABLE project_events (id TEXT PRIMARY KEY, canonical_id TEXT,
            project_developer TEXT, project_developer_en TEXT);
        CREATE TABLE chinese_entities (id INTEGER PRIMARY KEY, event_id TEXT,
            implementing_entity TEXT, implementing_entity_en TEXT,
            subsidiary_name TEXT, subsidiary_name_en TEXT);
        INSERT INTO canonical_projects VALUES ('P1', 0);
        INSERT INTO project_events VALUES ('P1-01','P1','甲公司; 乙公司',NULL);
    """)
    conn.commit(); conn.close()
    cache_p = tmp_path / "cache.json"
    cache_p.write_text(json.dumps({"甲公司": {"en_abbr": "JiaCo"}}, ensure_ascii=False), encoding="utf-8")
    calls = []

    def fake_translate(names):
        calls.append(sorted(names))
        return {n: "YiCo" for n in names}

    etr.run_residual_translate(db, cache_path=str(cache_p), residual_path=str(tmp_path / "r.json"),
                               use_registry=False, apply=True, translate_fn=fake_translate,
                               out_csv=str(tmp_path / "new.csv"))
    assert calls == [["乙公司"]]
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT project_developer_en FROM project_events").fetchone()[0] == "JiaCo; YiCo"
    conn.close()


def test_ascii_name_passthrough_fills_en_no_llm(tmp_path):
    """纯 ASCII 业主/实体名（已是英文，只是 _en 为空）：直通复制进 _en，不调 LLM。"""
    db = str(tmp_path / "a.db")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE canonical_projects (id TEXT PRIMARY KEY, excluded INTEGER DEFAULT 0);
        CREATE TABLE project_events (id TEXT PRIMARY KEY, canonical_id TEXT,
            project_developer TEXT, project_developer_en TEXT);
        CREATE TABLE chinese_entities (id INTEGER PRIMARY KEY, event_id TEXT,
            implementing_entity TEXT, implementing_entity_en TEXT,
            subsidiary_name TEXT, subsidiary_name_en TEXT);
        INSERT INTO canonical_projects VALUES ('P1', 0);
        INSERT INTO project_events VALUES ('P1-01','P1','Nepal Electricity Authority (NEA)',NULL);
    """)
    conn.commit(); conn.close()

    def boom(names):
        raise AssertionError(f"ASCII 直通不得调 LLM: {names}")

    report = etr.run_residual_translate(db, cache_path=_empty_cache(tmp_path),
                                        residual_path=str(tmp_path / "r.json"),
                                        use_registry=False, apply=True, translate_fn=boom,
                                        out_csv=str(tmp_path / "n.csv"))
    assert report["parts_translated"] == 0 and report["parts_missing"] == 0
    conn = sqlite3.connect(db)
    en = conn.execute("SELECT project_developer_en FROM project_events").fetchone()[0]
    conn.close()
    assert en == "Nepal Electricity Authority (NEA)"


def test_preview_no_llm_no_write(tmp_path):
    """preview：不调 LLM、不写库、不写缓存，只报缺口计数。"""
    db = _mk_db(tmp_path)
    residual_path = tmp_path / "residual.json"

    def boom(names):
        raise AssertionError("preview 不得调用 LLM")

    report = etr.run_residual_translate(db, cache_path=_empty_cache(tmp_path),
                                        residual_path=str(residual_path),
                                        use_registry=False, apply=False, translate_fn=boom)
    assert report["parts_missing"] == 3 and report["rows_updated"] == 0
    assert not residual_path.exists()
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT project_developer_en FROM project_events WHERE id='P1-01'").fetchone()[0] is None
    conn.close()
