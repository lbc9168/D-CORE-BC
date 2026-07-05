"""
TDD 测试：投资额原文短语保留（任务⑥）。

需求：
- S3 run_s3：LLM 返回 "Total Project Investment Raw" 时优先取该逐字原文短语存入
  total_investment_usd_raw_phrase；LLM 未返回该字段时写空（不变量：非空 ⇔ 原文）。
- S34 run_s34：同上，结果在 finance dict 中。
- 写库层：total_investment_usd_raw 列存原文短语（非解析后数字）。
- 无原文短语时 total_investment_usd_raw 为 NULL/空，不硬造。
"""
import sqlite3
import pandas as pd
import pytest
from s01b_project_extractor.stages.s3_finance import run_s3
from s01b_project_extractor.stages.s34_entity_finance import run_s34
from s01b_project_extractor.tools import project_extractor_tool as pet


# ─────────────────────────────────────────────────────────────────
# S3 stage 层：原文短语保留
# ─────────────────────────────────────────────────────────────────

class _FakeS3LLM:
    """按预设返回固定 S3 输出的桩 LLM。"""
    def __init__(self, payload):
        self._payload = payload

    def complete(self, prompt, *, stage, json_mode=True):
        assert stage == "s3"
        return self._payload


def test_run_s3_raw_phrase_new_field():
    """run_s3：LLM 返回 "Total Project Investment Raw" 时优先取该字段作为原文短语。"""
    llm = _FakeS3LLM({
        "Total Project Investment Raw": "约50亿元人民币",
        "Total Project Investment (Million USD)": "694",
        "Financing Model": "Unknown",
    })
    out = run_s3("t", "c", "项目", llm=llm)
    assert out.get("total_investment_usd_raw_phrase") == "约50亿元人民币"
    # 新字段不应污染 common_info（已 pop）
    assert "Total Project Investment Raw" not in out


def test_run_s3_fallback_to_empty_when_no_raw_field():
    """run_s3 降级路径：LLM 未返回 "Total Project Investment Raw" 时 raw_phrase 为空（不塞换算字段值）。"""
    llm = _FakeS3LLM({
        "Total Project Investment (Million USD)": "约50亿元人民币",
        "Financing Model": "Unknown",
    })
    out = run_s3("t", "c", "项目", llm=llm)
    # 不变量：未返回原文字段 → raw_phrase 必须为空，不污染审计线索
    assert out.get("total_investment_usd_raw_phrase") == ""


def test_run_s3_converted_number_unaffected():
    """run_s3：换算字段仍经 bri_parse 处理（行为不变），与是否有 raw 字段无关。"""
    llm = _FakeS3LLM({
        "Total Project Investment Raw": "约12亿美元",
        "Total Project Investment (Million USD)": "1,200",
        "Financing Model": "信贷融资",
    })
    out = run_s3("t", "c", "项目", llm=llm)
    # 换算字段行为不变
    assert out["Total Project Investment (Million USD)"] == 1200.0
    # 原文短语取新字段
    assert out.get("total_investment_usd_raw_phrase") == "约12亿美元"


def test_run_s3_empty_investment_raw_phrase_empty():
    """S3 无投资额信息且无 Raw 字段时，total_investment_usd_raw_phrase 为空字符串，不硬造。"""
    llm = _FakeS3LLM({
        "Total Project Investment (Million USD)": "",
        "Financing Model": "Unknown",
    })
    out = run_s3("t", "c", "项目", llm=llm)
    assert out.get("total_investment_usd_raw_phrase", "") == ""


def test_run_s3_missing_investment_raw_phrase_empty():
    """LLM 未返回投资额字段时，total_investment_usd_raw_phrase 为空字符串。"""
    llm = _FakeS3LLM({"Financing Model": "Unknown"})
    out = run_s3("t", "c", "项目", llm=llm)
    assert out.get("total_investment_usd_raw_phrase", "") == ""


# ─────────────────────────────────────────────────────────────────
# S34 stage 层：原文短语保留
# ─────────────────────────────────────────────────────────────────

class _FakeS34LLM:
    """按预设返回固定 S34 合并输出的桩 LLM。"""
    def __init__(self, payload):
        self._payload = payload

    def complete(self, prompt, *, stage, json_mode=True):
        assert stage == "s34"
        return self._payload


def test_run_s34_raw_phrase_new_field():
    """run_s34：LLM 返回 "Total Project Investment Raw" 时优先取该字段作为原文短语。"""
    llm = _FakeS34LLM({
        "chinese_entities": [{"Implementing Chinese Entity": "中国电建",
                              "Type of involvement of Chinese Entities": "EPC总承包"}],
        "Total Project Investment Raw": "约50亿元人民币",
        "Total Project Investment (Million USD)": "694",
        "Financing Model": "Unknown",
    })
    out = run_s34("t", "c", "项目", "光伏", llm=llm)
    assert out["finance"].get("total_investment_usd_raw_phrase") == "约50亿元人民币"
    # 新字段不应污染 finance dict（已 pop）
    assert "Total Project Investment Raw" not in out["finance"]


def test_run_s34_fallback_to_empty_when_no_raw_field():
    """run_s34 降级路径：LLM 未返回 "Total Project Investment Raw" 时 raw_phrase 为空。"""
    llm = _FakeS34LLM({
        "chinese_entities": [{"Implementing Chinese Entity": "中国电建",
                              "Type of involvement of Chinese Entities": "EPC总承包"}],
        "Total Project Investment (Million USD)": "约50亿元人民币",
        "Financing Model": "Unknown",
    })
    out = run_s34("t", "c", "项目", "光伏", llm=llm)
    # 不变量：未返回原文字段 → raw_phrase 必须为空，不污染审计线索
    assert out["finance"].get("total_investment_usd_raw_phrase") == ""


def test_run_s34_converted_number_unaffected():
    """run_s34：finance 换算字段仍经 bri_parse 处理（行为不变），与是否有 raw 字段无关。"""
    llm = _FakeS34LLM({
        "chinese_entities": [{"Implementing Chinese Entity": "中国能建",
                              "Type of involvement of Chinese Entities": "EPC总承包"}],
        "Total Project Investment Raw": "约12亿美元",
        "Total Project Investment (Million USD)": "1,200",
        "Financing Model": "信贷融资",
    })
    out = run_s34("t", "c", "项目", "光伏", llm=llm)
    assert out["finance"]["Total Project Investment (Million USD)"] == 1200.0
    assert out["finance"].get("total_investment_usd_raw_phrase") == "约12亿美元"


def test_run_s34_empty_investment_raw_phrase_empty():
    """S34 无投资额信息且无 Raw 字段时，total_investment_usd_raw_phrase 为空字符串。"""
    llm = _FakeS34LLM({
        "chinese_entities": [{"Implementing Chinese Entity": "中国电建",
                              "Type of involvement of Chinese Entities": "EPC总承包"}],
        "Total Project Investment (Million USD)": "",
        "Financing Model": "Unknown",
    })
    out = run_s34("t", "c", "项目", "光伏", llm=llm)
    assert out["finance"].get("total_investment_usd_raw_phrase", "") == ""


# ─────────────────────────────────────────────────────────────────
# 写库层：total_investment_usd_raw 存原文短语
# ─────────────────────────────────────────────────────────────────

@pytest.fixture
def _patch_side_effects(monkeypatch):
    """屏蔽外部副作用：央企查表/翻译。"""
    monkeypatch.setattr(pet, "_resolve_is_central_soe", lambda *a, **k: 0)
    monkeypatch.setattr(pet, "_resolve_entity_en", lambda *a, **k: "EN")


def _make_fake_extract_with_inv_raw(inv_raw_phrase: str, inv_parsed):
    """构造 fake extract_one，模拟 S3 已将原文短语注入 common_info。"""
    def fake_extract_one(title, content, date, *, client, model="x"):
        return {
            "analysis": {"project_subject": "s", "reasoning": "r"},
            "status": "valid_project",
            "projects": [{
                "project_summary": "",
                "in_scope": True,
                "out_of_scope_reason": "",
                "common_info": {
                    "Project Name": "坦桑尼亚100MW风电项目",
                    "Category": "风电",
                    "Capacity (MW)": "100MW",
                    "Country": "坦桑尼亚",
                    "Project Stage": "在建",
                    "capacity_mw_parsed": 100.0,
                    "capacity_mw_raw_phrase": "100MW",
                    "capacity_details_note": None,
                    # S3 注入的原文短语（未经解析的原始文本）
                    "total_investment_usd_raw_phrase": inv_raw_phrase,
                    # S3 已将 Total Project Investment 覆写为解析后值
                    "Total Project Investment (Million USD)": inv_parsed,
                    "Financing Model": "信贷融资",
                    "Financing Model Details": "",
                    "Investment (or Financial Close) Date": "",
                    "Additional Remarks": "",
                },
                "chinese_entities": [{
                    "Implementing Chinese Entity": "中国电建",
                    "Type of involvement of Chinese Entities": "EPC总承包",
                }],
            }],
        }
    return fake_extract_one


def test_write_total_investment_usd_raw_stores_original_phrase(
    tmp_path, monkeypatch, _patch_side_effects
):
    """写库层：total_investment_usd_raw 应存 LLM 原文短语（非解析后数字）。"""
    pdb = str(tmp_path / "projects.db")
    rdb = str(tmp_path / "review.db")

    inv_raw_phrase = "约50亿元人民币"
    monkeypatch.setattr(
        pet, "extract_one",
        _make_fake_extract_with_inv_raw(inv_raw_phrase, inv_parsed=None),
    )

    tool = pet.ProjectExtractorTool()
    tool._process_df(
        pd.DataFrame([{
            "原文链接": "http://x",
            "文章标题": "标题",
            "正文内容": "坦桑尼亚100MW风电项目EPC总承包中国电建",
            "发布日期": "2026-01-01",
        }]),
        batch_id="b1", ts="20260601_t", client=None,
        country_db={}, projects_db_path=pdb, review_db_path=rdb,
    )

    conn = sqlite3.connect(pdb)
    row = conn.execute(
        "SELECT total_investment_usd_raw, total_investment_usd FROM extracted_projects"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == inv_raw_phrase, (
        f"total_investment_usd_raw 应为原文短语 {inv_raw_phrase!r}，实际: {row[0]!r}"
    )


def test_write_total_investment_usd_raw_empty_when_no_investment(
    tmp_path, monkeypatch, _patch_side_effects
):
    """无投资额信息时，total_investment_usd_raw 为空/None，不硬造。"""
    pdb = str(tmp_path / "projects.db")
    rdb = str(tmp_path / "review.db")

    monkeypatch.setattr(
        pet, "extract_one",
        _make_fake_extract_with_inv_raw("", inv_parsed=None),
    )

    tool = pet.ProjectExtractorTool()
    tool._process_df(
        pd.DataFrame([{
            "原文链接": "http://x",
            "文章标题": "标题",
            "正文内容": "坦桑尼亚100MW风电项目EPC总承包中国电建",
            "发布日期": "2026-01-01",
        }]),
        batch_id="b1", ts="20260601_t", client=None,
        country_db={}, projects_db_path=pdb, review_db_path=rdb,
    )

    conn = sqlite3.connect(pdb)
    row = conn.execute(
        "SELECT total_investment_usd_raw FROM extracted_projects"
    ).fetchone()
    conn.close()
    assert row is not None
    assert not row[0], f"无投资额时 total_investment_usd_raw 应为空，实际: {row[0]!r}"


# ─────────────────────────────────────────────────────────────────
# 新字段路径端到端：LLM 返回 "Total Project Investment Raw" → 落库
# ─────────────────────────────────────────────────────────────────

def _make_fake_extract_with_new_raw_field(raw_phrase: str, inv_parsed):
    """构造 fake extract_one，模拟 S3 从 LLM 新字段取到原文短语后注入 common_info。"""
    def fake_extract_one(title, content, date, *, client, model="x"):
        return {
            "analysis": {"project_subject": "s", "reasoning": "r"},
            "status": "valid_project",
            "projects": [{
                "project_summary": "",
                "in_scope": True,
                "out_of_scope_reason": "",
                "common_info": {
                    "Project Name": "巴基斯坦200MW光伏项目",
                    "Category": "光伏",
                    "Capacity (MW)": "200MW",
                    "Country": "巴基斯坦",
                    "Project Stage": "建设完成",
                    "capacity_mw_parsed": 200.0,
                    "capacity_mw_raw_phrase": "200MW",
                    "capacity_details_note": None,
                    # S3 从 "Total Project Investment Raw" 字段取到原文短语并注入
                    "total_investment_usd_raw_phrase": raw_phrase,
                    "Total Project Investment (Million USD)": inv_parsed,
                    "Financing Model": "未知",
                    "Financing Model Details": "",
                    "Investment (or Financial Close) Date": "",
                    "Additional Remarks": "",
                },
                "chinese_entities": [{
                    "Implementing Chinese Entity": "中国能建",
                    "Type of involvement of Chinese Entities": "EPC总承包",
                }],
            }],
        }
    return fake_extract_one


def test_write_new_raw_field_stores_phrase_end_to_end(
    tmp_path, monkeypatch, _patch_side_effects
):
    """端到端：LLM 返回 "Total Project Investment Raw" 字段 → S3 注入 → 写库存原文短语。"""
    pdb = str(tmp_path / "projects.db")
    rdb = str(tmp_path / "review.db")

    raw_phrase = "约18亿巴基斯坦卢比"
    monkeypatch.setattr(
        pet, "extract_one",
        _make_fake_extract_with_new_raw_field(raw_phrase, inv_parsed=None),
    )

    tool = pet.ProjectExtractorTool()
    tool._process_df(
        pd.DataFrame([{
            "原文链接": "http://y",
            "文章标题": "标题2",
            "正文内容": "巴基斯坦200MW光伏项目EPC总承包中国能建",
            "发布日期": "2026-03-15",
        }]),
        batch_id="b2", ts="20260601_t", client=None,
        country_db={}, projects_db_path=pdb, review_db_path=rdb,
    )

    conn = sqlite3.connect(pdb)
    row = conn.execute(
        "SELECT total_investment_usd_raw FROM extracted_projects"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == raw_phrase, (
        f"端到端：total_investment_usd_raw 应为原文短语 {raw_phrase!r}，实际: {row[0]!r}"
    )


def test_write_fallback_no_raw_field_stores_null_end_to_end(
    tmp_path, monkeypatch, _patch_side_effects
):
    """端到端：LLM 未返回 Raw 字段（降级路径）→ 写库 total_investment_usd_raw 为空/NULL。"""
    pdb = str(tmp_path / "projects.db")
    rdb = str(tmp_path / "review.db")

    # 降级路径：raw_phrase 为空字符串（S3 未采集到原文短语）
    monkeypatch.setattr(
        pet, "extract_one",
        _make_fake_extract_with_new_raw_field("", inv_parsed=694.0),
    )

    tool = pet.ProjectExtractorTool()
    tool._process_df(
        pd.DataFrame([{
            "原文链接": "http://z",
            "文章标题": "标题3",
            "正文内容": "巴基斯坦200MW光伏项目EPC总承包中国能建",
            "发布日期": "2026-04-01",
        }]),
        batch_id="b3", ts="20260601_t", client=None,
        country_db={}, projects_db_path=pdb, review_db_path=rdb,
    )

    conn = sqlite3.connect(pdb)
    row = conn.execute(
        "SELECT total_investment_usd_raw, total_investment_usd FROM extracted_projects"
    ).fetchone()
    conn.close()
    assert row is not None
    assert not row[0], (
        f"降级路径：total_investment_usd_raw 应为空/NULL，实际: {row[0]!r}"
    )
    # 换算字段仍正常存库
    assert row[1] == 694.0, f"total_investment_usd 应为 694.0，实际: {row[1]!r}"
