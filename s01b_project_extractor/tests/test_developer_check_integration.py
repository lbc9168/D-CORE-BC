"""集成：developer 守卫接线 _process_df（写库前置空承包方重名业主）+ evidence 字段落库。"""
import sqlite3

import pandas as pd

from s01b_project_extractor.tools import project_extractor_tool as pet


def _run_process_df(monkeypatch, tmp_path, fake_result):
    """跑一遍 _process_df（单篇文章、假 extract_one、零 API），返回 projects.db 连接。"""
    monkeypatch.setattr(pet, "_load_name_cache", lambda: {})
    monkeypatch.setattr(pet, "_save_name_cache", lambda c: None)
    monkeypatch.setattr(pet, "_resolve_is_central_soe", lambda *a, **k: 0)
    monkeypatch.setattr(pet, "extract_one",
                        lambda title, content, date, *, client, model="x": fake_result)
    df = pd.DataFrame([{"原文链接": "http://x1", "文章标题": "标题",
                        "正文内容": "正文含发电 50MW 内容", "发布日期": "2026-01-01"}])
    tool = pet.ProjectExtractorTool()
    tool._process_df(df, batch_id="b1", ts="20260704_t", client=None,
                     country_db={}, projects_db_path=str(tmp_path / "p.db"),
                     review_db_path=str(tmp_path / "r.db"))
    return sqlite3.connect(str(tmp_path / "p.db"))


def _valid_project(common_extra: dict, entities: list[dict]) -> dict:
    common = {"Project Name": "肯尼亚50MW光伏项目", "Category": "光伏",
              "Capacity (MW)": "50", "Capacity Details": "",
              "Country": "肯尼亚", "Project Stage": "在建"}
    common.update(common_extra)
    return {"analysis": {"project_subject": "s", "reasoning": "r"},
            "status": "valid_project",
            "projects": [{"project_summary": "", "in_scope": True, "out_of_scope_reason": "",
                          "common_info": common, "chinese_entities": entities}]}


def test_contractor_developer_blanked_before_write(monkeypatch, tmp_path):
    """developer=同事件EPC实体 → 写库时 project_developer 与 _en 均为空。"""
    result = _valid_project(
        {"Project Developer": "中国电建", "Project Developer Evidence": "项目由中国电建投资建设"},
        [{"Implementing Chinese Entity": "中国电力建设集团有限公司",
          "Subsidiary Name": "中国电建集团国际工程有限公司",
          "Type of involvement of Chinese Entities": "EPC总承包"}],
    )
    conn = _run_process_df(monkeypatch, tmp_path, result)
    dev, dev_en = conn.execute(
        "SELECT project_developer, project_developer_en FROM extracted_projects").fetchone()
    conn.close()
    assert dev == ""
    assert dev_en == ""


def test_invest_developer_survives_write(monkeypatch, tmp_path):
    """developer=同事件股权投资方 → 原样写库（守卫不误伤）。"""
    result = _valid_project(
        {"Project Developer": "国家电投", "Project Developer Evidence": "国家电投持有项目股权"},
        [{"Implementing Chinese Entity": "国家电投",
          "Type of involvement of Chinese Entities": "股权投资方"}],
    )
    conn = _run_process_df(monkeypatch, tmp_path, result)
    dev = conn.execute("SELECT project_developer FROM extracted_projects").fetchone()[0]
    conn.close()
    assert dev == "国家电投"


def test_developer_evidence_persisted(monkeypatch, tmp_path):
    """S2 输出的 Project Developer Evidence 落库到 project_developer_evidence 列。"""
    result = _valid_project(
        {"Project Developer": "ACWA Power",
         "Project Developer Evidence": "业主方ACWA Power对项目交付提出要求"},
        [{"Implementing Chinese Entity": "南瑞继保",
          "Type of involvement of Chinese Entities": "一体化方案提供商"}],
    )
    conn = _run_process_df(monkeypatch, tmp_path, result)
    ev = conn.execute(
        "SELECT project_developer_evidence FROM extracted_projects").fetchone()[0]
    conn.close()
    assert ev == "业主方ACWA Power对项目交付提出要求"


def test_developer_evidence_persisted_unnamed_path(monkeypatch, tmp_path):
    """chinese_unnamed 路径同样落 evidence（两条写库分支契约一致）。"""
    result = {"analysis": {"project_subject": "s", "reasoning": "r"},
              "status": "chinese_unnamed",
              "projects": [{"project_summary": "", "in_scope": True, "out_of_scope_reason": "",
                            "common_info": {"Project Name": "肯尼亚50MW光伏项目", "Category": "光伏",
                                            "Capacity (MW)": "50", "Country": "肯尼亚",
                                            "Project Stage": "在建",
                                            "Project Developer": "肯尼亚电力公司",
                                            "Project Developer Evidence": "由肯尼亚电力公司投资建设"},
                            "chinese_entities": []}]}
    conn = _run_process_df(monkeypatch, tmp_path, result)
    dev, ev = conn.execute(
        "SELECT project_developer, project_developer_evidence FROM extracted_projects").fetchone()
    conn.close()
    assert dev == "肯尼亚电力公司"
    assert ev == "由肯尼亚电力公司投资建设"


def test_candidate_list_developer_blanked_before_write(monkeypatch, tmp_path):
    """合格开发商名单即使含投资类实体，也不得作为具体项目 developer 写库。"""
    result = _valid_project(
        {"Project Developer": "国家电投; ACWA Power",
         "Project Developer Evidence": "第七轮合格开发商入围名单"},
        [{"Implementing Chinese Entity": "国家电投",
          "Type of involvement of Chinese Entities": "股权投资方"}],
    )
    conn = _run_process_df(monkeypatch, tmp_path, result)
    dev, dev_en = conn.execute(
        "SELECT project_developer, project_developer_en FROM extracted_projects"
    ).fetchone()
    conn.close()
    assert dev == ""
    assert dev_en == ""


def test_null_developer_evidence_is_treated_as_missing(monkeypatch, tmp_path):
    """模型返回 JSON null 时生产入口仍按缺少 evidence 清空 developer。"""
    result = _valid_project(
        {"Project Developer": "ACWA Power", "Project Developer Evidence": None},
        [],
    )
    conn = _run_process_df(monkeypatch, tmp_path, result)
    dev, dev_en = conn.execute(
        "SELECT project_developer, project_developer_en FROM extracted_projects"
    ).fetchone()
    conn.close()
    assert dev == ""
    assert dev_en == ""
