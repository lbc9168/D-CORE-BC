"""s01b: 写库前确定性非能源兜底（Layer 0）+ unnamed 分支 in_scope 出口修复。"""
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


def _run(monkeypatch, tmp_path, status, proj):
    def fake_extract_one(title, content, date, *, client, model="x"):
        return {"analysis": {"project_subject": "s", "reasoning": "r"},
                "status": status, "projects": [proj]}
    monkeypatch.setattr(pet, "extract_one", fake_extract_one)
    pdb = str(tmp_path / "projects.db")
    tool = pet.ProjectExtractorTool()
    tool._process_df(_df_one_row(), batch_id="b1", ts="20260703_t", client=None,
                     country_db={}, projects_db_path=pdb,
                     review_db_path=str(tmp_path / "review.db"))
    conn = sqlite3.connect(pdb)
    row = conn.execute(
        "SELECT excluded, exclude_source, exclude_reason, capacity_mw FROM extracted_projects"
    ).fetchone()
    conn.close()
    return row


def test_valid_nonenergy_forced_excluded(tmp_path, monkeypatch, _patch_side_effects):
    """S2 说 in_scope=true 但名称是钾肥矿 → 兜底强制 excluded=1, source=deterministic_guard。"""
    row = _run(monkeypatch, tmp_path, "valid_project", {
        "project_summary": "并购世界级钾盐矿", "in_scope": True, "out_of_scope_reason": "",
        "common_info": {"Project Name": "加拿大盐湖钾肥矿业并购项目", "Category": "综合能源",
                        "Capacity (MW)": "3800000", "Capacity Details": "380万吨/年",
                        "Country": "加拿大", "Project Stage": "签约"},
        "chinese_entities": [{"Implementing Chinese Entity": "某矿业公司",
                              "Type of involvement of Chinese Entities": "投资"}],
    })
    assert row[0] == 1, "兜底应强制 excluded=1"
    assert row[1] == "deterministic_guard"
    assert row[2]


def test_valid_legit_energy_not_touched(tmp_path, monkeypatch, _patch_side_effects):
    """矿山光储微网（audit legit）→ 兜底放行 excluded=0。"""
    row = _run(monkeypatch, tmp_path, "valid_project", {
        "project_summary": "", "in_scope": True, "out_of_scope_reason": "",
        "common_info": {"Project Name": "赞比亚乔瓦矿山光储微网项目", "Category": "综合能源",
                        "Capacity (MW)": "30", "Capacity Details": "PV 20MW + BESS 10MWh",
                        "Country": "赞比亚", "Project Stage": "在建"},
        "chinese_entities": [{"Implementing Chinese Entity": "中国电建",
                              "Type of involvement of Chinese Entities": "EPC总承包"}],
    })
    assert row[0] == 0


def test_unnamed_in_scope_false_now_excluded(tmp_path, monkeypatch, _patch_side_effects):
    """修复缺口：chinese_unnamed 分支此前丢失 in_scope=false → 现在应写 excluded=1/extraction。"""
    row = _run(monkeypatch, tmp_path, "chinese_unnamed", {
        "project_summary": "", "in_scope": False, "out_of_scope_reason": "油气管道，非发电",
        "common_info": {"Project Name": "某油气管道项目", "Category": "综合能源",
                        "Capacity (MW)": "", "Capacity Details": "",
                        "Country": "肯尼亚", "Project Stage": "在建"},
        "chinese_entities": [],
    })
    assert row[0] == 1, "unnamed 分支应尊重 in_scope=false"
    assert row[1] == "extraction"
    assert "油气" in (row[2] or "")


def test_unnamed_nonenergy_forced_excluded(tmp_path, monkeypatch, _patch_side_effects):
    """chinese_unnamed + in_scope=true 但名称非能源 → 兜底同样生效。"""
    row = _run(monkeypatch, tmp_path, "chinese_unnamed", {
        "project_summary": "", "in_scope": True, "out_of_scope_reason": "",
        "common_info": {"Project Name": "伊拉克迈赫穆尔水泥厂项目", "Category": "综合能源",
                        "Capacity (MW)": "", "Capacity Details": "",
                        "Country": "伊拉克", "Project Stage": "在建"},
        "chinese_entities": [],
    })
    assert row[0] == 1
    assert row[1] == "deterministic_guard"


def test_tonnage_capacity_blanked(tmp_path, monkeypatch, _patch_side_effects):
    """容量明细为吨/年 → capacity_mw 置 NULL（raw 保留），不再产出 3.8TW 怪值。"""
    row = _run(monkeypatch, tmp_path, "valid_project", {
        "project_summary": "并购钾盐矿", "in_scope": True, "out_of_scope_reason": "",
        "common_info": {"Project Name": "加拿大盐湖钾肥矿业并购项目", "Category": "综合能源",
                        "Capacity (MW)": "3800000", "Capacity Details": "380万吨/年",
                        "Country": "加拿大", "Project Stage": "签约"},
        "chinese_entities": [{"Implementing Chinese Entity": "某矿业公司",
                              "Type of involvement of Chinese Entities": "投资"}],
    })
    assert row[3] is None, "吨/年容量应置 NULL"


def test_mw_capacity_kept(tmp_path, monkeypatch, _patch_side_effects):
    """正常 MW 容量不受守卫影响（S2 prompt 改后 LLM 输出原文短语如「50MW」而非裸数字）。"""
    row = _run(monkeypatch, tmp_path, "valid_project", {
        "project_summary": "", "in_scope": True, "out_of_scope_reason": "",
        "common_info": {"Project Name": "肯尼亚50MW光伏项目", "Category": "光伏",
                        "Capacity (MW)": "50MW", "Capacity Details": "",
                        "Country": "肯尼亚", "Project Stage": "在建"},
        "chinese_entities": [{"Implementing Chinese Entity": "中国电建",
                              "Type of involvement of Chinese Entities": "EPC总承包"}],
    })
    assert row[3] == 50.0
