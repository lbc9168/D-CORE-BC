"""测试能源分类影子汇总：只写 shadow 快照，不修改正式类别、编号或子类。"""

import csv
import json
import sqlite3

from s01b_project_extractor.tools.energy_taxonomy_shadow import run


def _seed_db(path):
    """创建带能源事实列和影子表的最小主库 fixture。"""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE canonical_projects (
            id TEXT PRIMARY KEY, project_name TEXT, category TEXT, subcategory TEXT, excluded INTEGER DEFAULT 0
        );
        CREATE TABLE project_events (
            id TEXT PRIMARY KEY, canonical_id TEXT, capacity_details TEXT, project_summary TEXT,
            generation_sources_json TEXT, storage_status TEXT,
            storage_power_mw_raw TEXT, storage_energy_mwh_raw TEXT,
            storage_power_mw REAL, storage_energy_mwh REAL, energy_composition_evidence TEXT
        );
        CREATE TABLE project_classification_shadow (
            canonical_id TEXT PRIMARY KEY, generation_sources_json TEXT NOT NULL DEFAULT '[]',
            storage_status TEXT NOT NULL DEFAULT 'not_mentioned', storage_power_mw REAL,
            storage_energy_mwh REAL, proposed_category TEXT,
            technology_tags_json TEXT NOT NULL DEFAULT '[]', classification_version TEXT NOT NULL,
            needs_review INTEGER NOT NULL DEFAULT 0, review_reason TEXT,
            classification_source TEXT NOT NULL DEFAULT 'policy', manual_locked INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
    """)
    conn.execute(
        "INSERT INTO canonical_projects VALUES ('ITA-PV-0001','意大利光储项目','综合能源','光储',0)"
    )
    conn.execute(
        """INSERT INTO project_events VALUES (
           'ITA-PV-0001-01','ITA-PV-0001','PV 35MW; BESS 13.75MW/50.16MWh','光伏配套储能',
           '["光伏"]','confirmed','13.75MW','50.16MWh',13.75,50.16,'光伏配套储能')"""
    )
    conn.commit()
    conn.close()


def test_dry_run_reports_difference_without_writing(tmp_path):
    """若 dry-run 插入 shadow 行或修改正式类别，本测试失败。"""
    db = tmp_path / "bri.db"
    _seed_db(db)
    report = run(str(db), apply=False)
    conn = sqlite3.connect(db)
    category, subcategory = conn.execute(
        "SELECT category, subcategory FROM canonical_projects WHERE id='ITA-PV-0001'"
    ).fetchone()
    shadow_count = conn.execute("SELECT COUNT(*) FROM project_classification_shadow").fetchone()[0]
    conn.close()
    assert report["would_change"] == 1
    assert report["rows"][0]["proposed_category"] == "光伏"
    assert category == "综合能源" and subcategory == "光储"
    assert shadow_count == 0


def test_apply_writes_shadow_only_and_preserves_identity(tmp_path):
    """apply 只能更新影子快照，canonical ID、正式类别和旧子类必须逐字不变。"""
    db = tmp_path / "bri.db"
    _seed_db(db)
    run(str(db), apply=True)
    conn = sqlite3.connect(db)
    canonical = conn.execute("SELECT id, category, subcategory FROM canonical_projects").fetchone()
    shadow = conn.execute(
        "SELECT generation_sources_json, storage_status, proposed_category, technology_tags_json, "
        "classification_version, needs_review, storage_power_mw, storage_energy_mwh "
        "FROM project_classification_shadow"
    ).fetchone()
    conn.close()
    assert canonical == ("ITA-PV-0001", "综合能源", "光储")
    assert json.loads(shadow[0]) == ["光伏"]
    assert shadow[1:3] == ("confirmed", "光伏")
    assert json.loads(shadow[3]) == ["光储", "含储能"]
    assert shadow[4] == "energy_taxonomy_v1"
    assert shadow[5] == 0
    assert shadow[6:] == (13.75, 50.16)


def test_manual_locked_shadow_is_not_overwritten(tmp_path):
    """人工锁定的影子裁定必须在重复运行中保持不变。"""
    db = tmp_path / "bri.db"
    _seed_db(db)
    conn = sqlite3.connect(db)
    conn.execute(
        """INSERT INTO project_classification_shadow
           (canonical_id,generation_sources_json,storage_status,proposed_category,
            technology_tags_json,classification_version,needs_review,review_reason,
            classification_source,manual_locked,updated_at) VALUES
           ('ITA-PV-0001','["风电"]','confirmed','风电','["风储","含储能"]',
            'manual_v1',0,'','manual',1,'2026-01-01')"""
    )
    conn.commit()
    conn.close()
    run(str(db), apply=True)
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT proposed_category, classification_version, manual_locked FROM project_classification_shadow"
    ).fetchone()
    conn.close()
    assert row == ("风电", "manual_v1", 1)


def test_reports_write_csv_and_json(tmp_path):
    """影子报告必须同时提供可人工复核的 CSV 和机器可读 JSON。"""
    db = tmp_path / "bri.db"
    csv_path = tmp_path / "shadow.csv"
    json_path = tmp_path / "shadow.json"
    _seed_db(db)
    run(str(db), apply=False, out_csv=str(csv_path), out_json=str(json_path))
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert rows[0]["canonical_id"] == "ITA-PV-0001"
    assert rows[0]["current_category"] == "综合能源"
    assert rows[0]["proposed_category"] == "光伏"
    assert payload["would_change"] == 1
    assert payload["rows"][0]["technology_tags"] == ["光储", "含储能"]
    assert payload["rows"][0]["storage_energy_mwh"] == 50.16
