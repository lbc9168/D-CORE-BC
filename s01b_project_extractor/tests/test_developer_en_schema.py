"""项目业主英文列：extracted_projects 新增 project_developer_en（与 implementing_entity_en 并列）。"""
from s01b_project_extractor.tools.project_extractor_tool import _init_projects_db


def test_project_developer_en_column_exists_and_writable(tmp_path):
    conn = _init_projects_db(str(tmp_path / "projects.db"))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(extracted_projects)")}
    assert "project_developer" in cols
    assert "project_developer_en" in cols          # 新列
    conn.execute(
        "INSERT INTO extracted_projects (project_name, project_developer, project_developer_en) "
        "VALUES (?, ?, ?)",
        ("测试项目", "印尼国家电力公司(PLN)", "Indonesia's PLN"),
    )
    conn.commit()
    row = conn.execute(
        "SELECT project_developer, project_developer_en FROM extracted_projects"
    ).fetchone()
    conn.close()
    assert row[0] == "印尼国家电力公司(PLN)"
    assert row[1] == "Indonesia's PLN"
