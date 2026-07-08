"""2B-3 registry 前置：确定性映射、LLM 降级为建议、旧行为可回退。"""
import json
import sqlite3

from s02b_entity_normalizer.company_lookup import build_index
from s02b_entity_normalizer.developer_normalizer import registry_part_mapping

FIXTURE_REGISTRY = {  # 与 test_company_lookup 同款迷你册
    "_meta": {"version": 1, "generated": "2026-01-01", "source": "test"},
    "companies": [{"canonical_cn": "阿布扎比未来能源公司", "canonical_en": "Masdar",
                   "aliases": ["Masdar", "马斯达尔"], "is_chinese": False, "parent_cn": None, "source": "t"}],
}


def _index(tmp_path):
    rp = tmp_path / "reg.json"
    rp.write_text(json.dumps(FIXTURE_REGISTRY, ensure_ascii=False), encoding="utf-8")
    return build_index(str(rp), None)


def test_registry_mapping_only_when_changed(tmp_path):
    idx = _index(tmp_path)
    parts = [("Masdar", 10), ("阿布扎比未来能源公司", 5), ("无关公司", 3)]
    m = registry_part_mapping(parts, idx)
    assert m == {"Masdar": "阿布扎比未来能源公司"}  # 已是规范名的不进映射；未命中不进映射


def test_llm_candidates_exclude_registry_hits(tmp_path):
    """未命中部件才可成为 LLM 候选（Masdar 命中后不得再与任何部件配对）。"""
    from s02b_entity_normalizer.developer_normalizer import _candidates_for_llm
    idx = _index(tmp_path)
    parts = [("Masdar", 10), ("马斯达尔能源公司", 4), ("马斯达尔能源集团", 3)]
    cands = _candidates_for_llm(parts, idx)
    names = {c["name_a"] for c in cands} | {c["name_b"] for c in cands}
    assert "Masdar" not in names


def _mk_db(path):
    """两行：一行 registry 可解(马斯达尔，中文别名，_collect_parts 只留中文部件)，一行仅原样值。"""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE project_events (id INTEGER PRIMARY KEY, project_developer TEXT)")
    conn.executemany("INSERT INTO project_events(project_developer) VALUES (?)", [
        ("马斯达尔; 无关公司",),
        ("马斯达尔",),
    ])
    conn.commit()
    return conn


def test_end_to_end_registry_apply(tmp_path, monkeypatch):
    """registry 命中确定性落库；LLM 段 monkeypatch 短路为空返回，仍生成 suggestions/miss CSV；重跑 0 变更。"""
    db = str(tmp_path / "t.db")
    _mk_db(db).close()

    rp = tmp_path / "reg.json"
    rp.write_text(json.dumps(FIXTURE_REGISTRY, ensure_ascii=False), encoding="utf-8")

    import s02b_entity_normalizer.developer_normalizer as dn

    supporting_docs = tmp_path / "supporting_docs"
    supporting_docs.mkdir()
    monkeypatch.setattr(dn, "_SUPPORTING_DOCS", supporting_docs)
    monkeypatch.setattr(dn, "build_index", lambda: build_index(str(rp), None))
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    monkeypatch.setattr("google.genai.Client", lambda api_key: object())
    monkeypatch.setattr(dn, "_llm_confirm_batch_dev", lambda client, batch: [])

    dn.run(db_path=db, apply=True, use_registry=True)

    conn = sqlite3.connect(db)
    vals = {r[0] for r in conn.execute("SELECT project_developer FROM project_events").fetchall()}
    conn.close()
    assert vals == {"阿布扎比未来能源公司; 无关公司", "阿布扎比未来能源公司"}

    suggestions = list(supporting_docs.glob("developer_registry_suggestions_*.csv"))
    misses = list(supporting_docs.glob("developer_norm_misses_*.csv"))
    assert suggestions, "应生成 registry 补录建议 CSV"
    assert misses, "应生成 miss CSV"

    # 重跑 0 变更（幂等：值已是规范名）
    dn.run(db_path=db, apply=True, use_registry=True)
    conn = sqlite3.connect(db)
    vals2 = {r[0] for r in conn.execute("SELECT project_developer FROM project_events").fetchall()}
    conn.close()
    assert vals2 == vals, "重跑应 0 变更"


# ── ASCII 部件跨语言归一（Masdar→阿布扎比未来能源公司 是 spec 头号验收锚点）────────


def _mk_ascii_db(path):
    """一行含纯 ASCII registry 可解部件(Masdar)的最小库。"""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE project_events (id INTEGER PRIMARY KEY, project_developer TEXT)")
    conn.execute("INSERT INTO project_events(project_developer) VALUES (?)", ("Masdar; 无关公司",))
    conn.commit()
    return conn


def _patch_llm_off(monkeypatch, dn, rp):
    """打桩 build_index(迷你册)/GEMINI_API_KEY/genai.Client/LLM 批确认，端到端不真调 Gemini。"""
    monkeypatch.setattr(dn, "build_index", lambda: build_index(str(rp), None))
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    monkeypatch.setattr("google.genai.Client", lambda api_key: object())
    monkeypatch.setattr(dn, "_llm_confirm_batch_dev", lambda client, batch: [])


def test_ascii_part_cross_language_apply(tmp_path, monkeypatch):
    """use_registry=True：纯 ASCII 部件(Masdar)经 registry 跨语言归一落库。"""
    import s02b_entity_normalizer.developer_normalizer as dn
    db = str(tmp_path / "t.db")
    _mk_ascii_db(db).close()
    rp = tmp_path / "reg.json"
    rp.write_text(json.dumps(FIXTURE_REGISTRY, ensure_ascii=False), encoding="utf-8")
    _patch_llm_off(monkeypatch, dn, rp)

    supporting_docs = tmp_path / "supporting_docs"
    supporting_docs.mkdir()
    monkeypatch.setattr(dn, "_SUPPORTING_DOCS", supporting_docs)
    dn.run(db_path=db, apply=True, use_registry=True)

    conn = sqlite3.connect(db)
    vals = {r[0] for r in conn.execute("SELECT project_developer FROM project_events").fetchall()}
    conn.close()
    assert vals == {"阿布扎比未来能源公司; 无关公司"}


def test_ascii_part_untouched_when_registry_off(tmp_path, monkeypatch):
    """use_registry=False（对照组）：同库同值一字不变（旧行为 100% 等价）。"""
    import s02b_entity_normalizer.developer_normalizer as dn
    db = str(tmp_path / "t.db")
    _mk_ascii_db(db).close()
    rp = tmp_path / "reg.json"
    rp.write_text(json.dumps(FIXTURE_REGISTRY, ensure_ascii=False), encoding="utf-8")
    _patch_llm_off(monkeypatch, dn, rp)

    dn.run(db_path=db, apply=True, use_registry=False)

    conn = sqlite3.connect(db)
    vals = {r[0] for r in conn.execute("SELECT project_developer FROM project_events").fetchall()}
    conn.close()
    assert vals == {"Masdar; 无关公司"}, "对照组不得触发 registry 归一"


def test_ascii_canonical_equal_not_in_mapping(tmp_path):
    """registry 命中但 canonical==原文的 ASCII 部件（canonical_cn 即英文名的外企）不进映射。"""
    from s02b_entity_normalizer.developer_normalizer import _collect_ascii_parts
    reg = json.loads(json.dumps(FIXTURE_REGISTRY, ensure_ascii=False))
    reg["companies"].append({"canonical_cn": "Recurrent Energy", "canonical_en": "Recurrent Energy",
                             "aliases": [], "is_chinese": True, "parent_cn": None, "source": "t"})
    rp = tmp_path / "reg.json"
    rp.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
    idx = build_index(str(rp), None)

    db = str(tmp_path / "t.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE project_events (id INTEGER PRIMARY KEY, project_developer TEXT)")
    conn.executemany("INSERT INTO project_events(project_developer) VALUES (?)", [
        ("Masdar; Recurrent Energy",),
        ("无关公司",),
    ])
    conn.commit()

    ascii_parts = _collect_ascii_parts(conn)
    conn.close()
    names = {n for n, _ in ascii_parts}
    assert names == {"Masdar", "Recurrent Energy"}, "ASCII 集合应为 _collect_parts 的互补（不含中文部件）"
    m = registry_part_mapping(ascii_parts, idx)
    assert m == {"Masdar": "阿布扎比未来能源公司"}, "canonical==原文的 ASCII 命中不进映射"
