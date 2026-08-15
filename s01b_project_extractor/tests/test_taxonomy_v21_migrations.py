"""TX/MF schema 与 energy taxonomy v2.1 数据迁移的 dry-run、备份、幂等和门禁测试。"""
import sqlite3

from scripts.migrate_add_txmf_columns import apply_migration, dry_run as txmf_dry_run
from scripts.migrate_energy_taxonomy_v21 import apply as apply_v21, dry_run as v21_dry_run


def _make_db(path):
    """创建包含迁移所需最小表和一条非受控标签的测试数据库。"""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE canonical_projects (
          id TEXT PRIMARY KEY, category TEXT, technology_tags_json TEXT, excluded INTEGER DEFAULT 0
        );
        CREATE TABLE project_events (id TEXT PRIMARY KEY, canonical_id TEXT);
        CREATE TABLE chinese_entities (id INTEGER PRIMARY KEY, event_id TEXT);
        CREATE TABLE project_classification_shadow (
          canonical_id TEXT PRIMARY KEY, proposed_category TEXT,
          technology_tags_json TEXT, classification_version TEXT
        );
        INSERT INTO canonical_projects VALUES ('TST-HB-0001','综合能源','["光水储"]',0);
        INSERT INTO project_events VALUES ('TST-HB-0001-01','TST-HB-0001');
        INSERT INTO chinese_entities VALUES (1,'TST-HB-0001-01');
        INSERT INTO project_classification_shadow
          VALUES ('TST-HB-0001','综合能源','["光水储"]','energy_taxonomy_v2');
    """)
    conn.commit()
    conn.close()


def test_txmf_schema_migration_adds_raw_and_numeric_columns_idempotently(tmp_path):
    """TX/MF 迁移新增 4 raw + 5 numeric 列，行数不变且重跑无变更。"""
    db = tmp_path / "txmf.db"
    _make_db(db)
    assert len(txmf_dry_run(db)["to_add"]) == 9
    first = apply_migration(db)
    assert len(first["to_add"]) == 9
    assert txmf_dry_run(db)["to_add"] == []
    second = apply_migration(db)
    assert second["to_add"] == []
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM project_events").fetchone()[0] == 1
    conn.close()


def test_taxonomy_v21_migration_clears_extension_and_updates_version(tmp_path):
    """v2.1 只清非受控标签并更新 shadow 版本，不改变 ID、行数或 Category。"""
    db = tmp_path / "taxonomy.db"
    _make_db(db)
    report = v21_dry_run(db)
    assert report["canonical_tag_changes"] == ["TST-HB-0001"]
    assert report["shadow_version_changes"] == 1
    applied = apply_v21(db)
    assert applied["changed_canonical_tags"] == 1
    assert applied["changed_shadow_versions"] == 1
    conn = sqlite3.connect(db)
    canonical = conn.execute(
        "SELECT id,category,technology_tags_json FROM canonical_projects"
    ).fetchone()
    shadow = conn.execute(
        "SELECT technology_tags_json,classification_version FROM project_classification_shadow"
    ).fetchone()
    conn.close()
    assert canonical == ("TST-HB-0001", "综合能源", "[]")
    assert shadow == ("[]", "energy_taxonomy_v2.1")
    rerun = v21_dry_run(db)
    assert rerun["canonical_tag_changes"] == []
    assert rerun["shadow_version_changes"] == 0
