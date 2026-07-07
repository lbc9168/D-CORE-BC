"""en_backfill registry 优先档：跨源优先级、ASCII 查册、逐段拼接。"""
import json
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
