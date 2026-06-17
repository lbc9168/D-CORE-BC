"""s01b: _process_df 应按 S2 的 in_scope=false 把记录写为 excluded=1（extraction 来源）。"""
import sqlite3
import pandas as pd
import pytest
from s01b_project_extractor.tools import project_extractor_tool as pet


@pytest.fixture
def _patch_side_effects(monkeypatch):
    """屏蔽外部副作用：央企查表/翻译；只留写库逻辑。"""
    monkeypatch.setattr(pet, "_resolve_is_central_soe", lambda *a, **k: 0)
    monkeypatch.setattr(pet, "_resolve_entity_en", lambda *a, **k: "EN")


def _df_one_row():
    return pd.DataFrame([{
        "原文链接": "http://x1", "文章标题": "标题", "正文内容": "正文含发电 50MW 内容", "发布日期": "2026-01-01",
    }])


def test_in_scope_false_writes_excluded(tmp_path, monkeypatch, _patch_side_effects):
    """S2 判 in_scope=false → 写库 excluded=1, exclude_source='extraction', 带 reason。"""
    pdb = str(tmp_path / "projects.db"); rdb = str(tmp_path / "review.db")

    def fake_extract_one(title, content, date, *, client, model="x"):
        return {
            "analysis": {"project_subject": "s", "reasoning": "r"},
            "status": "valid_project",
            "projects": [{
                "project_summary": "", "in_scope": False, "out_of_scope_reason": "油气管道，非发电",
                "common_info": {"Project Name": "某油气管道", "Category": "综合能源",
                                "Capacity (MW)": "", "Country": "肯尼亚", "Project Stage": "在建"},
                "chinese_entities": [{"Implementing Chinese Entity": "中国石油",
                                      "Type of involvement of Chinese Entities": "EPC总承包"}],
            }],
        }
    monkeypatch.setattr(pet, "extract_one", fake_extract_one)

    tool = pet.ProjectExtractorTool()
    tool._process_df(_df_one_row(), batch_id="b1", ts="20260601_t", client=None,
                     country_db={}, projects_db_path=pdb, review_db_path=rdb)

    conn = sqlite3.connect(pdb)
    row = conn.execute("SELECT excluded, exclude_source, exclude_reason FROM extracted_projects").fetchone()
    conn.close()
    assert row[0] == 1, "in_scope=false 应写 excluded=1"
    assert row[1] == "extraction"
    assert "油气" in (row[2] or "")


def test_in_scope_true_writes_not_excluded(tmp_path, monkeypatch, _patch_side_effects):
    """S2 默认 in_scope=true → excluded=0。"""
    pdb = str(tmp_path / "projects.db"); rdb = str(tmp_path / "review.db")

    def fake_extract_one(title, content, date, *, client, model="x"):
        return {
            "analysis": {"project_subject": "s", "reasoning": "r"},
            "status": "valid_project",
            "projects": [{
                "project_summary": "", "in_scope": True, "out_of_scope_reason": "",
                "common_info": {"Project Name": "肯尼亚50MW光伏项目", "Category": "光伏",
                                "Capacity (MW)": "50", "Country": "肯尼亚", "Project Stage": "在建"},
                "chinese_entities": [{"Implementing Chinese Entity": "中国电建",
                                      "Type of involvement of Chinese Entities": "EPC总承包"}],
            }],
        }
    monkeypatch.setattr(pet, "extract_one", fake_extract_one)

    tool = pet.ProjectExtractorTool()
    tool._process_df(_df_one_row(), batch_id="b1", ts="20260601_t2", client=None,
                     country_db={}, projects_db_path=pdb, review_db_path=rdb)

    conn = sqlite3.connect(pdb)
    row = conn.execute("SELECT excluded FROM extracted_projects").fetchone()
    conn.close()
    assert row[0] == 0
