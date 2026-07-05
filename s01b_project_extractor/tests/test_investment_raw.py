"""
TDD 测试：投资额原文短语保留（任务⑥）。

需求：
- S3 run_s3：LLM 输出的原文字符串（如"约50亿元人民币"）保留进
  total_investment_usd_raw_phrase，换算字段 Total Project Investment (Million USD) 不变。
- S34 run_s34：同上，finance dict 中含 total_investment_usd_raw_phrase。
- 写库层：total_investment_usd_raw 列存原文短语（非解析后数字）。
- 无投资额信息时 raw_phrase 为空字符串，不硬造。
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


def test_run_s3_preserves_raw_phrase():
    """run_s3 后：total_investment_usd_raw_phrase 保留 LLM 逐字原文短语。"""
    llm = _FakeS3LLM({
        "Total Project Investment (Million USD)": "约50亿元人民币",
        "Financing Model": "Unknown",
    })
    out = run_s3("t", "c", "项目", llm=llm)
    assert out.get("total_investment_usd_raw_phrase") == "约50亿元人民币"


def test_run_s3_converted_number_unaffected():
    """run_s3 原文保留后，换算字段仍经 bri_parse 处理（行为不变）。"""
    llm = _FakeS3LLM({
        "Total Project Investment (Million USD)": "1,200",
        "Financing Model": "信贷融资",
    })
    out = run_s3("t", "c", "项目", llm=llm)
    # 换算字段行为不变
    assert out["Total Project Investment (Million USD)"] == 1200.0
    # 原文短语保留
    assert out.get("total_investment_usd_raw_phrase") == "1,200"


def test_run_s3_empty_investment_raw_phrase_empty():
    """无投资额信息时，total_investment_usd_raw_phrase 为空字符串，不硬造。"""
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


def test_run_s34_preserves_raw_phrase():
    """run_s34 后：finance dict 含 total_investment_usd_raw_phrase（原文短语）。"""
    llm = _FakeS34LLM({
        "chinese_entities": [{"Implementing Chinese Entity": "中国电建",
                              "Type of involvement of Chinese Entities": "EPC总承包"}],
        "Total Project Investment (Million USD)": "约50亿元人民币",
        "Financing Model": "Unknown",
    })
    out = run_s34("t", "c", "项目", "光伏", llm=llm)
    assert out["finance"].get("total_investment_usd_raw_phrase") == "约50亿元人民币"


def test_run_s34_converted_number_unaffected():
    """run_s34 原文保留后，finance 换算字段仍经 bri_parse 处理（行为不变）。"""
    llm = _FakeS34LLM({
        "chinese_entities": [{"Implementing Chinese Entity": "中国能建",
                              "Type of involvement of Chinese Entities": "EPC总承包"}],
        "Total Project Investment (Million USD)": "1,200",
        "Financing Model": "信贷融资",
    })
    out = run_s34("t", "c", "项目", "光伏", llm=llm)
    assert out["finance"]["Total Project Investment (Million USD)"] == 1200.0
    assert out["finance"].get("total_investment_usd_raw_phrase") == "1,200"


def test_run_s34_empty_investment_raw_phrase_empty():
    """S34 无投资额信息时，total_investment_usd_raw_phrase 为空字符串。"""
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
