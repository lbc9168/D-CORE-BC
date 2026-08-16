"""实体事实去重只删除无冲突的同主体同角色重复。"""

import sqlite3

from s02b_entity_normalizer.tools.entity_deduplicator import (
    apply_dedup_plan,
    build_dedup_plan,
)


def test_dedup_merges_safe_group_and_keeps_amount_conflict(tmp_path):
    """缺失字段可以互补，金额冲突组必须完整保留。"""
    db = tmp_path / "entities.db"
    connection = sqlite3.connect(db)
    connection.execute(
        "CREATE TABLE chinese_entities(id INTEGER PRIMARY KEY, event_id TEXT, implementing_entity TEXT, "
        "implementing_entity_en TEXT, subsidiary_name TEXT, subsidiary_name_en TEXT, "
        "entity_en_quality TEXT, involvement_type TEXT, subcontract_amount_raw TEXT, "
        "subcontract_amount REAL, is_central_soe INTEGER)"
    )
    connection.executemany(
        "INSERT INTO chinese_entities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "E1", "集团", "Group", "子公司", "", "manual_verified", "EPC", "", None, 1),
            (2, "E1", "集团", "", "子公司", "Sub", "", "EPC", "", None, 1),
            (3, "E2", "集团", "Group", "", "", "manual_verified", "EPC", "10", 10.0, 1),
            (4, "E2", "集团", "Group", "", "", "manual_verified", "EPC", "12", 12.0, 1),
        ],
    )
    connection.commit()
    connection.close()

    safe, conflicts = build_dedup_plan(str(db))
    assert len(safe) == 1
    assert len(conflicts) == 1
    apply_dedup_plan(str(db), safe)

    connection = sqlite3.connect(db)
    assert connection.execute("SELECT COUNT(*) FROM chinese_entities").fetchone()[0] == 3
    assert connection.execute(
        "SELECT subsidiary_name_en FROM chinese_entities WHERE id = 1"
    ).fetchone()[0] == "Sub"
    assert connection.execute(
        "SELECT COUNT(*) FROM chinese_entities WHERE event_id = 'E2'"
    ).fetchone()[0] == 2
    connection.close()
