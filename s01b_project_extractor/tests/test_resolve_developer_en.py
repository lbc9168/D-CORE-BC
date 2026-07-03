"""s01b 翻译入口：ASCII 直通、developer 拆串、垃圾键拒写缓存。"""
import pandas as pd
import sqlite3
from s01b_project_extractor.tools import project_extractor_tool as pet


class _FakeResp:
    text = "Whatever Co"


class _FakeModels:
    def __init__(self):
        self.calls = 0
    def generate_content(self, **kwargs):
        self.calls += 1
        return _FakeResp()


class _FakeClient:
    def __init__(self):
        self.models = _FakeModels()


def test_ascii_passthrough_no_cache_no_client(monkeypatch):
    """无中文字符 → 原样返回：不查缓存、不调翻译、不写缓存。client=None 证明无 API 调用。"""
    saved = []
    monkeypatch.setattr(pet, "_load_name_cache", lambda: {})
    monkeypatch.setattr(pet, "_save_name_cache", lambda c: saved.append(1))
    assert pet._resolve_entity_en("ACWA Power", None) == "ACWA Power"
    assert saved == []


def test_developer_split_and_rejoin(monkeypatch):
    """多公司串拆开逐家解析，以 '; ' 拼回（缓存命中，零 API 调用）。"""
    cache = {"中国电建": {"en": "PowerChina", "en_abbr": None},
             "阳光电源": {"en": "Sungrow", "en_abbr": None}}
    monkeypatch.setattr(pet, "_load_name_cache", lambda: cache)
    assert pet._resolve_developer_en("中国电建;阳光电源", None) == "PowerChina; Sungrow"
    assert pet._resolve_developer_en("中国电建、阳光电源", None) == "PowerChina; Sungrow"


def test_developer_single_name_delegates(monkeypatch):
    cache = {"中国电建": {"en": "PowerChina", "en_abbr": None}}
    monkeypatch.setattr(pet, "_load_name_cache", lambda: cache)
    assert pet._resolve_developer_en("中国电建", None) == "PowerChina"
    assert pet._resolve_developer_en("", None) == ""


def test_separator_string_never_cached(monkeypatch):
    """含分隔符的串（上游漏拆）翻译结果照常返回，但不得写入缓存。"""
    saved = []
    monkeypatch.setattr(pet, "_load_name_cache", lambda: {})
    monkeypatch.setattr(pet, "_save_name_cache", lambda c: saved.append(dict(c)))
    client = _FakeClient()
    out = pet._resolve_entity_en("中国电建;阳光电源", client)
    assert out == "Whatever Co"
    assert saved == [], "多公司串不得固化进缓存"


def test_toolong_key_never_cached(monkeypatch):
    saved = []
    monkeypatch.setattr(pet, "_load_name_cache", lambda: {})
    monkeypatch.setattr(pet, "_save_name_cache", lambda c: saved.append(dict(c)))
    long_name = "中" * 31
    out = pet._resolve_entity_en(long_name, _FakeClient())
    assert out == "Whatever Co"
    assert saved == []


def test_normal_name_still_cached(monkeypatch):
    """正常中文公司名照旧写缓存（守卫不误伤主路径）。"""
    saved = []
    monkeypatch.setattr(pet, "_load_name_cache", lambda: {})
    monkeypatch.setattr(pet, "_save_name_cache", lambda c: saved.append(dict(c)))
    out = pet._resolve_entity_en("测试能源公司", _FakeClient())
    assert out == "Whatever Co"
    assert len(saved) == 1 and "测试能源公司" in saved[0]


def test_write_path_uses_developer_split(tmp_path, monkeypatch):
    """集成：_process_df 写库后 project_developer_en 是拆串翻译的 '; ' 拼接。"""
    cache = {"中国电建": {"en": "PowerChina", "en_abbr": None},
             "阳光电源": {"en": "Sungrow", "en_abbr": None},
             "某矿业公司": {"en": "Some Mining Co", "en_abbr": None}}
    monkeypatch.setattr(pet, "_load_name_cache", lambda: cache)
    monkeypatch.setattr(pet, "_resolve_is_central_soe", lambda *a, **k: 0)

    def fake_extract_one(title, content, date, *, client, model="x"):
        return {"analysis": {"project_subject": "s", "reasoning": "r"},
                "status": "valid_project",
                "projects": [{
                    "project_summary": "", "in_scope": True, "out_of_scope_reason": "",
                    "common_info": {"Project Name": "肯尼亚50MW光伏项目", "Category": "光伏",
                                    "Capacity (MW)": "50", "Capacity Details": "",
                                    "Country": "肯尼亚", "Project Stage": "在建",
                                    "Project Developer": "中国电建;阳光电源"},
                    "chinese_entities": [{"Implementing Chinese Entity": "中国电建",
                                          "Type of involvement of Chinese Entities": "EPC总承包"}],
                }]}
    monkeypatch.setattr(pet, "extract_one", fake_extract_one)

    df = pd.DataFrame([{"原文链接": "http://x1", "文章标题": "标题",
                        "正文内容": "正文含发电 50MW 内容", "发布日期": "2026-01-01"}])
    tool = pet.ProjectExtractorTool()
    tool._process_df(df, batch_id="b1", ts="20260704_t", client=None,
                     country_db={}, projects_db_path=str(tmp_path / "p.db"),
                     review_db_path=str(tmp_path / "r.db"))
    conn = sqlite3.connect(str(tmp_path / "p.db"))
    dev_en = conn.execute("SELECT project_developer_en FROM extracted_projects").fetchone()[0]
    conn.close()
    assert dev_en == "PowerChina; Sungrow"
