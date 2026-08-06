"""测试能源影子 schema 迁移：默认只读、apply 备份且保持 canonical ID 集合不变。"""

import sqlite3

from scripts.migrate_add_energy_taxonomy_shadow import apply_migration, dry_run


def _legacy_db(path):
    """创建迁移前最小 canonical/project_events 数据库。"""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE canonical_projects (id TEXT PRIMARY KEY, category TEXT);
        CREATE TABLE project_events (
            id TEXT PRIMARY KEY, canonical_id TEXT, storage_power_mw REAL, storage_energy_mwh REAL
        );
        INSERT INTO canonical_projects VALUES ('ITA-PV-0001','综合能源');
        INSERT INTO project_events VALUES ('ITA-PV-0001-01','ITA-PV-0001',13.75,50.16);
    """)
    conn.commit()
    conn.close()


def test_dry_run_never_changes_schema(tmp_path):
    """默认 dry-run 只报告缺失对象，不能创建列或表。"""
    db = tmp_path / "bri.db"
    _legacy_db(db)
    report = dry_run(db)
    conn = sqlite3.connect(db)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(project_events)")}
    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='project_classification_shadow'"
    ).fetchone()
    conn.close()
    assert "project_events.generation_sources_json" in report["to_add"]
    assert "generation_sources_json" not in columns
    assert table is None


def test_apply_adds_schema_backs_up_and_preserves_ids(tmp_path):
    """apply 应创建完整 schema 和非空备份，同时保持项目与事件 ID 集合不变。"""
    db = tmp_path / "bri.db"
    _legacy_db(db)
    report = apply_migration(db)
    conn = sqlite3.connect(db)
    project_ids = {row[0] for row in conn.execute("SELECT id FROM canonical_projects")}
    event_ids = {row[0] for row in conn.execute("SELECT id FROM project_events")}
    event_columns = {row[1] for row in conn.execute("PRAGMA table_info(project_events)")}
    shadow_columns = {row[1] for row in conn.execute("PRAGMA table_info(project_classification_shadow)")}
    conn.close()
    backups = list(tmp_path.glob("bri.db.bak.*"))
    assert project_ids == {"ITA-PV-0001"}
    assert event_ids == {"ITA-PV-0001-01"}
    assert "generation_sources_json" in event_columns
    assert "manual_locked" in shadow_columns
    assert {"storage_power_mw", "storage_energy_mwh"}.issubset(shadow_columns)
    assert backups and backups[0].stat().st_size > 0
    assert report["to_add"]


def test_apply_is_idempotent(tmp_path):
    """重复 apply 不得重复建表或改变项目行数。"""
    db = tmp_path / "bri.db"
    _legacy_db(db)
    apply_migration(db)
    second = apply_migration(db)
    conn = sqlite3.connect(db)
    count = conn.execute("SELECT COUNT(*) FROM canonical_projects").fetchone()[0]
    conn.close()
    assert count == 1
    assert second["to_add"] == []
