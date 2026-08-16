"""中方实体外科恢复按稳定 ID 工作且默认可审计。"""

import json
import sqlite3

from s02b_entity_normalizer.tools.entity_recovery import (
    apply_recovery_plan,
    build_recovery_plan,
)


def _make_fixture(tmp_path):
    """创建一份包含历史误改行和一条增量误改行的最小数据库组。"""
    baseline = tmp_path / "baseline.db"
    connection = sqlite3.connect(baseline)
    connection.execute(
        "CREATE TABLE chinese_entities(id INTEGER PRIMARY KEY, implementing_entity TEXT, implementing_entity_en TEXT)"
    )
    connection.execute("INSERT INTO chinese_entities VALUES (1, '中国能源建设集团', 'Energy China')")
    connection.commit()
    connection.close()

    current = tmp_path / "current.db"
    connection = sqlite3.connect(current)
    connection.execute(
        "CREATE TABLE chinese_entities(id INTEGER PRIMARY KEY, event_id TEXT, implementing_entity TEXT, "
        "implementing_entity_en TEXT, subsidiary_name TEXT, involvement_type TEXT)"
    )
    connection.execute("CREATE TABLE project_events(id TEXT PRIMARY KEY, source_links TEXT)")
    connection.execute(
        "INSERT INTO project_events VALUES ('EV-01', ?)",
        (json.dumps(["https://example.test/1"]),),
    )
    connection.executemany(
        "INSERT INTO chinese_entities VALUES (?, 'EV-01', ?, ?, ?, ?)",
        [
            (1, "中南电力设计院", "CSEPDI", "", "EPC"),
            (2, "中南电力设计院", "CSEPDI", "中国能建国际", "EPC"),
        ],
    )
    connection.commit()
    connection.close()

    projects = tmp_path / "projects.db"
    connection = sqlite3.connect(projects)
    connection.execute(
        "CREATE TABLE extracted_projects(id INTEGER PRIMARY KEY, project_name TEXT, record_type TEXT, "
        "implementing_entity TEXT, implementing_entity_en TEXT, subsidiary_name TEXT, "
        "subsidiary_name_en TEXT, involvement_type TEXT, subcontract_amount_usd TEXT, "
        "is_central_soe INTEGER, source_link TEXT, publish_date TEXT, imported_to_s02a INTEGER)"
    )
    connection.execute(
        "INSERT INTO extracted_projects VALUES "
        "(10, '项目', '具体项目', '中国能源建设集团', 'Energy China', '中国能建国际', "
        "'Energy China International', 'EPC', '', 1, 'https://example.test/1', '2026-07-03', 1)"
    )
    connection.commit()
    connection.close()
    return current, baseline, projects


def test_recovery_restores_baseline_and_campaign_rows(tmp_path):
    """历史行从基线恢复，新增行从 campaign 原子记录重建。"""
    current, baseline, projects = _make_fixture(tmp_path)
    plan, stats = build_recovery_plan(
        str(current), str(baseline), str(projects), "2026-07-01", "2026-07-30"
    )
    assert stats["baseline_changes"] == 1
    assert stats["campaign_changes"] == 1

    result = apply_recovery_plan(str(current), plan)
    assert result["updated"] == 2
    connection = sqlite3.connect(current)
    values = connection.execute(
        "SELECT id, implementing_entity FROM chinese_entities ORDER BY id"
    ).fetchall()
    connection.close()
    assert values == [(1, "中国能源建设集团"), (2, "中国能源建设集团")]
