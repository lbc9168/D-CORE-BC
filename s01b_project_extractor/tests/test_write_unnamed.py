"""chinese_unnamed 写库：占位实体行 + is_unnamed_placeholder=1 + review_queue 条目。"""
import sqlite3
from unittest import mock
import pandas as pd
import pytest

from s01b_project_extractor.tools.project_extractor_tool import ProjectExtractorTool


@pytest.fixture()
def tmp_dbs(tmp_path):
    """返回 (proj_db_str, review_db_str) 两个临时路径。"""
    return str(tmp_path / "projects.db"), str(tmp_path / "review.db")


def _make_unnamed_result():
    """构造 orchestrator 返回的 chinese_unnamed result，含占位实体。"""
    return {
        "analysis": {"reasoning": "中企未具名", "project_subject": "某国光伏"},
        "status": "chinese_unnamed",
        "projects": [
            {
                "project_summary": "某国一光伏项目，中国企业参与信息不详",
                "common_info": {
                    "Project Name": "某国光伏项目",
                    "Project Name (EN)": "",
                    "Country": "巴基斯坦",
                    "Category": "光伏",
                    "Project Stage": "在建",
                    "Record Type": "具体项目",
                },
                "chinese_entities": [
                    {
                        "Implementing Chinese Entity": "未具名中企",
                        "Type of involvement of Chinese Entities": "Unknown",
                        "is_unnamed_placeholder": True,
                    }
                ],
            }
        ],
    }


def test_chinese_unnamed_writes_placeholder_entity(tmp_dbs):
    """chinese_unnamed 路径应写入 implementing_entity='未具名中企' 且 is_unnamed_placeholder=1。"""
    proj_db, rev_db = tmp_dbs
    df = pd.DataFrame([
        {
            "原文链接": "http://example.com/news/unnamed1",
            "文章标题": "中国企业参与巴基斯坦光伏项目",
            "正文内容": "一家中国企业在巴基斯坦承建100MW光伏项目，但新闻中未透露企业名称。",
            "发布日期": "2025-01-01",
        }
    ])
    fake_result = _make_unnamed_result()

    tool = ProjectExtractorTool()
    with mock.patch(
        "s01b_project_extractor.tools.project_extractor_tool.extract_one",
        return_value=fake_result,
    ):
        tool._process_df(
            df, "batch_unnamed_test", "20250101_0000",
            client=mock.MagicMock(),
            country_db={},
            projects_db_path=proj_db,
            review_db_path=rev_db,
            max_workers=1,
        )

    # Assert the project row is written
    conn = sqlite3.connect(proj_db)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM extracted_projects").fetchall()]
    conn.close()

    assert len(rows) == 1, f"期望1行，实际 {len(rows)} 行"
    row = rows[0]
    assert row["implementing_entity"] == "未具名中企", f"实际值: {row['implementing_entity']}"
    assert row["is_unnamed_placeholder"] == 1, f"is_unnamed_placeholder 应为1，实际: {row.get('is_unnamed_placeholder')}"
    assert row["project_name"] == "某国光伏项目"
    assert row["excluded"] == 1
    assert row["exclude_source"] == "entity_verification"
    assert row["needs_review"] == 1


def test_chinese_unnamed_enqueues_review_item(tmp_dbs):
    """chinese_unnamed 写库后应在 review_queue 表写入 reason_type='unnamed_entity' 的条目。"""
    from review import queue
    proj_db, rev_db = tmp_dbs
    df = pd.DataFrame([
        {
            "原文链接": "http://example.com/news/unnamed2",
            "文章标题": "中国企业参与某国风电项目",
            "正文内容": "一家中国企业在某国参与风电建设，企业名称未知。",
            "发布日期": "2025-02-01",
        }
    ])
    fake_result = _make_unnamed_result()
    fake_result["projects"][0]["common_info"]["Project Name"] = "某国风电项目"

    tool = ProjectExtractorTool()
    with mock.patch(
        "s01b_project_extractor.tools.project_extractor_tool.extract_one",
        return_value=fake_result,
    ):
        tool._process_df(
            df, "batch_queue_test", "20250201_0000",
            client=mock.MagicMock(),
            country_db={},
            projects_db_path=proj_db,
            review_db_path=rev_db,
            max_workers=1,
        )

    rows = queue.pending(rev_db)
    assert len(rows) == 1, f"期望1条 pending 复核项，实际 {len(rows)} 条"
    assert rows[0]["reason_type"] == "unnamed_entity"
    assert rows[0]["source_link"] == "http://example.com/news/unnamed2"
    assert rows[0]["state"] == "pending"
