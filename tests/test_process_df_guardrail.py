"""确认 _process_df 写库后调用批级 guardrail（format+batch），bulk 路径不再裸奔。"""
import sqlite3
from unittest import mock
import pandas as pd
from s01b_project_extractor.tools.project_extractor_tool import ProjectExtractorTool


def test_process_df_invokes_format_checks(tmp_path):
    proj_db = str(tmp_path / "p.db"); rev_db = str(tmp_path / "r.db")
    df = pd.DataFrame([{"原文链接": "u1", "文章标题": "肯尼亚光伏", "正文内容": "中国能建在肯尼亚建光伏电站 100MW 在建", "发布日期": "2025-01-01"}])
    fake_result = {"analysis": {"reasoning": "ok"}, "status": "valid_project",
                   "projects": [{"project_summary": "s", "common_info": {"Project Name": "肯尼亚100MW光伏项目", "Country": "肯尼亚", "Category": "光伏", "Project Stage": "在建"},
                                 "chinese_entities": [{"Implementing Chinese Entity": "中国能建", "Type of involvement of Chinese Entities": "EPC总承包"}]}]}
    tool = ProjectExtractorTool()
    with mock.patch("s01b_project_extractor.tools.project_extractor_tool.extract_one", return_value=fake_result), \
         mock.patch("s01b_project_extractor.tools.project_extractor_tool.run_format_checks", return_value=[]) as mfc:
        tool._process_df(df, "batch1", "20250101_0000", client=mock.MagicMock(),
                         country_db={}, projects_db_path=proj_db, review_db_path=rev_db, max_workers=1)
        assert mfc.called  # 写库路径确实跑了格式校验
    rows = sqlite3.connect(proj_db).execute("SELECT project_name FROM extracted_projects").fetchall()
    assert rows and "光伏" in rows[0][0]
