"""缓存卫生：垃圾键/值规则扫描、corrections upsert、备份写回。"""
import json
from pathlib import Path
import pytest
from s01b_project_extractor.tools.cache_hygiene import (
    apply_changes, load_corrections, plan_corrections, scan_cache,
)


def _cache():
    return {
        "中国电建": {"en": "Power Construction Corporation of China (PowerChina)",
                     "en_abbr": "PowerChina", "verified": True, "source": "official"},
        "阳光电源股份有限公司": {"en": "Sungrow Power Supply Co., Ltd.", "en_abbr": None,
                                 "verified": False, "source": "ai"},
        "中国能源建设集团党委副书记、总经理陈观福": {"en": "CEEC", "en_abbr": None,
                                                     "verified": False, "source": "ai"},   # R1 分隔符+R2 头衔
        "N/A": {"en": "N/A", "en_abbr": None, "verified": False, "source": "ai"},           # R3 占位
        "四家全球知名公司": {"en": "**1. 腾讯控股有限公司 (Tencent)**\n**2. ...", "en_abbr": None,
                             "verified": False, "source": "ai"},                            # R3 泛指 + R6 噪声值
        "未指明": {"en": "未指明", "en_abbr": None, "verified": False, "source": "ai"},      # R3 + R5 值含中文
        "这是一个超过三十个字符的假想公司名称用来测试超长键规则触发不触发": {                 # R4 超长键
            "en": "X Co", "en_abbr": None, "verified": False, "source": "ai"},
        "中国企业与挪威Scatec公司": {"en": "[中国企业] & Scatec ASA", "en_abbr": None,
                                     "verified": False, "source": "ai"},                    # R5 值含中文
    }


def test_scan_flags_junk_rules():
    findings = scan_cache(_cache())
    flagged = {f["key"] for f in findings}
    assert "中国能源建设集团党委副书记、总经理陈观福" in flagged
    assert "N/A" in flagged
    assert "四家全球知名公司" in flagged
    assert "未指明" in flagged
    assert "这是一个超过三十个字符的假想公司名称用来测试超长键规则触发不触发" in flagged
    assert "中国企业与挪威Scatec公司" in flagged
    by_key = {f["key"]: f["rule"] for f in findings}
    assert by_key["N/A"] == "R3_key_placeholder"


def test_scan_never_flags_clean_or_verified():
    findings = scan_cache(_cache())
    flagged = {f["key"] for f in findings}
    assert "中国电建" not in flagged, "verified 条目绝不列入清理"
    assert "阳光电源股份有限公司" not in flagged, "干净 ai 条目不误报"


def test_corrections_roundtrip(tmp_path):
    csv_path = tmp_path / "corr.csv"
    csv_path.write_text(
        'cn,en,en_abbr,verified,source\n'
        '上能电气,"Sineng Electric Co., Ltd.",Sineng Electric,false,correction-20260704\n'
        '中国电建,"Power Construction Corporation of China (PowerChina)",PowerChina,true,official\n',
        encoding="utf-8-sig")
    corr = load_corrections(str(csv_path))
    assert corr[0]["cn"] == "上能电气" and corr[0]["en"].endswith("Ltd.")
    plan = plan_corrections(_cache(), corr)
    # 上能电气是新键 → is_new；中国电建与现值一致 → 不在 would-change 里
    assert any(p["cn"] == "上能电气" and p["is_new"] for p in plan)
    assert not any(p["cn"] == "中国电建" for p in plan)


def test_apply_removes_upserts_and_backs_up(tmp_path):
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(json.dumps({"_comment": "meta", **_cache()}, ensure_ascii=False),
                          encoding="utf-8")
    corr = [{"cn": "上能电气", "en": "Sineng Electric Co., Ltd.",
             "en_abbr": "Sineng Electric", "verified": False, "source": "correction-20260704"}]
    new_cache, removed = apply_changes(str(cache_path), remove_keys=["N/A", "未指明"], corrections=corr)
    assert removed == 2
    assert "N/A" not in new_cache and "未指明" not in new_cache
    assert new_cache["上能电气"]["en_abbr"] == "Sineng Electric"
    on_disk = json.loads(cache_path.read_text(encoding="utf-8"))
    assert on_disk["_comment"] == "meta", "_ 开头元字段必须保留"
    assert "上能电气" in on_disk and "N/A" not in on_disk
    assert list(tmp_path.glob("cache.json.bak.*")), "apply 前必须备份"


def test_apply_never_removes_verified(tmp_path):
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(json.dumps(_cache(), ensure_ascii=False), encoding="utf-8")
    new_cache, removed = apply_changes(str(cache_path), remove_keys=["中国电建"], corrections=[])
    assert removed == 0, "verified 键不应被删除"
    assert "中国电建" in new_cache, "verified 键即使被点名也不删（防误操作）"
    on_disk = json.loads(cache_path.read_text(encoding="utf-8"))
    assert "中国电建" in on_disk, "verified 键必须保留在磁盘文件里"
