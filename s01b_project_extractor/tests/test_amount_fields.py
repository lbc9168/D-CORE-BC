"""TDD 测试：合同金额/融资额度两类新金额字段（指标扩展 2026-W30）。

需求：
- S3/S34 stage：LLM 返回 "Contract Amount Raw"/"Financing Amount Raw" 时逐字原文短语
  存入 *_raw_phrase；未返回写空（不变量：非空 ⇔ 原文）；数值字段经 bri_parse。
- 写库层：contract_amount_usd_raw/financing_amount_usd_raw 存原文短语，数值列存解析值。
- 三类金额可同时有值（不互斥，见 prompt B.5），互不污染。
"""
import sqlite3

import pandas as pd
import pytest

from s01b_project_extractor.stages.s3_finance import run_s3
from s01b_project_extractor.stages.s34_entity_finance import run_s34
from s01b_project_extractor.tools import project_extractor_tool as pet


class _FakeLLM:
    """按预设返回固定输出的桩 LLM（s3/s34 通用）。"""
    def __init__(self, payload, stage):
        self._payload = payload
        self._stage = stage

    def complete(self, prompt, *, stage, json_mode=True):
        assert stage == self._stage
        return self._payload


# ── S3 stage 层 ──────────────────────────────────────────────────

def test_run_s3_contract_and_financing_raw_phrases():
    """run_s3：两类新金额的 Raw 字段逐字保留，数值经 bri_parse。"""
    llm = _FakeLLM({
        "Contract Amount Raw": "合同金额约12亿元人民币",
        "Contract Amount (Million USD)": "167",
        "Financing Amount Raw": "银团贷款35亿元",
        "Financing Amount (Million USD)": "486",
        "Financing Model": "信贷融资",
    }, stage="s3")
    out = run_s3("t", "c", "项目", llm=llm)
    assert out.get("contract_amount_usd_raw_phrase") == "合同金额约12亿元人民币"
    assert out["Contract Amount (Million USD)"] == 167.0
    assert out.get("financing_amount_usd_raw_phrase") == "银团贷款35亿元"
    assert out["Financing Amount (Million USD)"] == 486.0
    # Raw 字段已 pop，不污染 common_info
    assert "Contract Amount Raw" not in out
    assert "Financing Amount Raw" not in out


def test_run_s3_missing_amount_fields_phrases_empty():
    """LLM 未返回新金额字段时 raw_phrase 为空（不变量：非空 ⇔ 原文）。"""
    llm = _FakeLLM({"Financing Model": "Unknown"}, stage="s3")
    out = run_s3("t", "c", "项目", llm=llm)
    assert out.get("contract_amount_usd_raw_phrase") == ""
    assert out.get("financing_amount_usd_raw_phrase") == ""


def test_run_s3_three_amounts_coexist():
    """三类金额可同时有值（"总投资50亿其中银团贷款35亿"场景），互不覆盖。"""
    llm = _FakeLLM({
        "Total Project Investment Raw": "总投资50亿元",
        "Total Project Investment (Million USD)": "694",
        "Financing Amount Raw": "其中银团贷款35亿元",
        "Financing Amount (Million USD)": "486",
        "Financing Model": "信贷融资",
    }, stage="s3")
    out = run_s3("t", "c", "项目", llm=llm)
    assert out["Total Project Investment (Million USD)"] == 694.0
    assert out["Financing Amount (Million USD)"] == 486.0
    assert out.get("contract_amount_usd_raw_phrase") == ""


# ── S34 stage 层 ─────────────────────────────────────────────────

def test_run_s34_contract_and_financing_in_finance_dict():
    """run_s34：新金额字段进 finance dict，Raw 已 pop。"""
    llm = _FakeLLM({
        "chinese_entities": [{"Implementing Chinese Entity": "中国电建",
                              "Type of involvement of Chinese Entities": "EPC总承包"}],
        "Contract Amount Raw": "中标金额8.5亿美元",
        "Contract Amount (Million USD)": "850",
        "Financing Model": "Unknown",
    }, stage="s34")
    out = run_s34("t", "c", "项目", "光伏", llm=llm)
    assert out["finance"].get("contract_amount_usd_raw_phrase") == "中标金额8.5亿美元"
    assert out["finance"]["Contract Amount (Million USD)"] == 850.0
    assert out["finance"].get("financing_amount_usd_raw_phrase") == ""
    assert "Contract Amount Raw" not in out["finance"]


# ── 写库层端到端 ─────────────────────────────────────────────────

@pytest.fixture
def _patch_side_effects(monkeypatch):
    """屏蔽外部副作用：央企查表/翻译。"""
    monkeypatch.setattr(pet, "_resolve_is_central_soe", lambda *a, **k: 0)
    monkeypatch.setattr(pet, "_resolve_entity_en", lambda *a, **k: "EN")


def _make_fake_extract(common_extra: dict):
    """构造 fake extract_one，模拟 stage 层已注入金额字段的 common_info。"""
    def fake_extract_one(title, content, date, *, client, model="x"):
        common = {
            "Project Name": "坦桑尼亚100MW风电项目",
            "Category": "风电",
            "Capacity (MW)": "100MW",
            "Country": "坦桑尼亚",
            "Project Stage": "在建",
            "capacity_mw_parsed": 100.0,
            "capacity_mw_raw_phrase": "100MW",
            "capacity_details_note": None,
            "Financing Model": "信贷融资",
            "Financing Model Details": "",
            "Investment (or Financial Close) Date": "",
            "Additional Remarks": "",
        }
        common.update(common_extra)
        return {
            "analysis": {"project_subject": "s", "reasoning": "r"},
            "status": "valid_project",
            "projects": [{
                "project_summary": "",
                "in_scope": True,
                "out_of_scope_reason": "",
                "common_info": common,
                "chinese_entities": [{
                    "Implementing Chinese Entity": "中国电建",
                    "Type of involvement of Chinese Entities": "EPC总承包",
                }],
            }],
        }
    return fake_extract_one


def test_write_contract_and_financing_columns(tmp_path, monkeypatch, _patch_side_effects):
    """写库层：新 4 列存原文短语+解析数值，与投资额并存互不污染。"""
    pdb = str(tmp_path / "projects.db")
    rdb = str(tmp_path / "review.db")

    monkeypatch.setattr(pet, "extract_one", _make_fake_extract({
        "total_investment_usd_raw_phrase": "总投资50亿元",
        "Total Project Investment (Million USD)": 694.0,
        "contract_amount_usd_raw_phrase": "合同金额约12亿元人民币",
        "Contract Amount (Million USD)": 167.0,
        "financing_amount_usd_raw_phrase": "银团贷款35亿元",
        "Financing Amount (Million USD)": 486.0,
    }))

    tool = pet.ProjectExtractorTool()
    tool._process_df(
        pd.DataFrame([{
            "原文链接": "http://x",
            "文章标题": "标题",
            "正文内容": "坦桑尼亚100MW风电项目EPC总承包中国电建",
            "发布日期": "2026-01-01",
        }]),
        batch_id="b1", ts="20260724_t", client=None,
        country_db={}, projects_db_path=pdb, review_db_path=rdb,
    )

    conn = sqlite3.connect(pdb)
    row = conn.execute(
        """SELECT contract_amount_usd_raw, contract_amount_usd,
                  financing_amount_usd_raw, financing_amount_usd,
                  total_investment_usd
           FROM extracted_projects"""
    ).fetchone()
    conn.close()
    assert row == ("合同金额约12亿元人民币", 167.0, "银团贷款35亿元", 486.0, 694.0)


def test_write_amount_columns_null_when_absent(tmp_path, monkeypatch, _patch_side_effects):
    """无合同/融资信息时新 4 列为空/NULL，不硬造。"""
    pdb = str(tmp_path / "projects.db")
    rdb = str(tmp_path / "review.db")

    monkeypatch.setattr(pet, "extract_one", _make_fake_extract({
        "total_investment_usd_raw_phrase": "",
        "Total Project Investment (Million USD)": None,
        "contract_amount_usd_raw_phrase": "",
        "Contract Amount (Million USD)": "",
        "financing_amount_usd_raw_phrase": "",
        "Financing Amount (Million USD)": "",
    }))

    tool = pet.ProjectExtractorTool()
    tool._process_df(
        pd.DataFrame([{
            "原文链接": "http://y",
            "文章标题": "标题2",
            "正文内容": "坦桑尼亚100MW风电项目EPC总承包中国电建",
            "发布日期": "2026-01-02",
        }]),
        batch_id="b2", ts="20260724_t", client=None,
        country_db={}, projects_db_path=pdb, review_db_path=rdb,
    )

    conn = sqlite3.connect(pdb)
    row = conn.execute(
        """SELECT contract_amount_usd_raw, contract_amount_usd,
                  financing_amount_usd_raw, financing_amount_usd
           FROM extracted_projects"""
    ).fetchone()
    conn.close()
    assert row == (None, None, None, None)
