"""en_backfill registry 优先档：跨源优先级、ASCII 查册、逐段拼接、run_backfill 接线。"""
import json
import sqlite3

from s02b_entity_normalizer import en_backfill
from s02b_entity_normalizer.company_lookup import build_index
from s02b_entity_normalizer.en_backfill import resolve_en

REG = {"_meta": {"version": 1, "generated": "x", "source": "t"},
       "companies": [{"canonical_cn": "阿布扎比未来能源公司", "canonical_en": "Masdar",
                      "aliases": ["Masdar", "马斯达尔"], "is_chinese": False, "parent_cn": None, "source": "t"}]}


def _idx(tmp_path):
    p = tmp_path / "r.json"; p.write_text(json.dumps(REG, ensure_ascii=False), encoding="utf-8")
    return build_index(str(p), None)


def test_registry_beats_cache(tmp_path):
    cache = {"阿布扎比未来能源公司": {"en": "Abu Dhabi Future Energy Co."}}
    en, status = resolve_en(cache, "阿布扎比未来能源公司", index=_idx(tmp_path))
    assert en == "Masdar" and status == "hit_registry"


def test_ascii_value_hits_registry(tmp_path):
    en, status = resolve_en({}, "马斯达尔", index=_idx(tmp_path))
    assert en == "Masdar"
    en2, status2 = resolve_en({}, "Masdar", index=_idx(tmp_path))  # ASCII 值也查册
    assert en2 == "Masdar" and status2 == "hit_registry"


def test_no_index_keeps_legacy_behavior():
    assert resolve_en({}, "Masdar") == (None, "ascii_skip")   # 不传 index=现行为


def test_multipart_mixed_sources(tmp_path):
    cache = {"无关公司": {"en_abbr": "Wuguan Co."}}
    en, status = resolve_en(cache, "马斯达尔; 无关公司", index=_idx(tmp_path))
    assert en == "Masdar; Wuguan Co."


def _mk_db_and_cache(tmp_path):
    """夹具：主库单行（册/缓存英文名不同的公司）+ 缓存文件，返回 (db, cache_path)。"""
    db = str(tmp_path / "main.db")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE chinese_entities (id INTEGER PRIMARY KEY,
            implementing_entity TEXT, implementing_entity_en TEXT,
            subsidiary_name TEXT, subsidiary_name_en TEXT);
        CREATE TABLE project_events (id INTEGER PRIMARY KEY,
            project_developer TEXT, project_developer_en TEXT);
    """)
    conn.execute("INSERT INTO chinese_entities(implementing_entity, implementing_entity_en) VALUES (?,?)",
                 ("阿布扎比未来能源公司", "Old Wrong"))
    conn.commit()
    conn.close()
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(json.dumps(
        {"阿布扎比未来能源公司": {"en": "Abu Dhabi Future Energy Co."}}, ensure_ascii=False), encoding="utf-8")
    return db, str(cache_path)


def test_run_backfill_default_uses_registry(tmp_path, monkeypatch):
    """pipeline 2B-4 入口缺省 registry 优先（spec §5 单一写入方）。"""
    db, cache_path = _mk_db_and_cache(tmp_path)
    idx = _idx(tmp_path)
    monkeypatch.setattr(en_backfill, "build_index", lambda: idx)
    n = en_backfill.run_backfill(db, cache_path)
    assert n == 1
    conn = sqlite3.connect(db)
    en = conn.execute("SELECT implementing_entity_en FROM chinese_entities").fetchone()[0]
    conn.close()
    assert en == "Masdar"


def test_run_backfill_no_registry_keeps_legacy(tmp_path, monkeypatch):
    """use_registry=False 灰度回退位：不建 index，写缓存值（旧口径）。"""
    db, cache_path = _mk_db_and_cache(tmp_path)
    def _boom():
        raise AssertionError("use_registry=False 不得调用 build_index")
    monkeypatch.setattr(en_backfill, "build_index", _boom)
    n = en_backfill.run_backfill(db, cache_path, use_registry=False)
    assert n == 1
    conn = sqlite3.connect(db)
    en = conn.execute("SELECT implementing_entity_en FROM chinese_entities").fetchone()[0]
    conn.close()
    assert en == "Abu Dhabi Future Energy Co."
