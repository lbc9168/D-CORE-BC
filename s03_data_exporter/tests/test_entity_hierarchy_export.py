"""P3: projects_flat entity rows carry v4 group/parent/path metadata."""
import json
import sqlite3

from s03_data_exporter import flat_builder
import bri_enums

CEEC = "ent_405cdb1433d45008b11d"
CPECC = "ent_3d8eb7a5fea850b4bb28"


def _make_src(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE canonical_projects (
            id TEXT PRIMARY KEY, project_name TEXT, project_name_en TEXT,
            name_en_quality TEXT, country TEXT, country_iso TEXT, bri_country TEXT,
            ldc TEXT, location TEXT, category TEXT, excluded INTEGER DEFAULT 0,
            excluded_reason TEXT
        );
        CREATE TABLE project_events (
            id TEXT PRIMARY KEY, canonical_id TEXT, project_stage TEXT,
            capacity_mw REAL, capacity_mw_raw TEXT,
            publish_date TEXT, investment_date TEXT,
            official_website_links TEXT, non_official_website_links TEXT,
            verification_status TEXT, project_developer TEXT, project_developer_en TEXT
        );
        CREATE TABLE chinese_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT,
            implementing_entity TEXT, implementing_entity_en TEXT,
            subsidiary_name TEXT, subsidiary_name_en TEXT,
            involvement_type TEXT, is_central_soe INTEGER,
            entity_id TEXT, entity_name TEXT, group_entity_id TEXT,
            raw_entity_name TEXT, identity_status TEXT, entity_evidence_json TEXT
        );
        """
    )
    terminal = [s for s in bri_enums.valid_stages() if bri_enums.stage_priority(s) >= 0][-1]
    conn.execute(
        "INSERT INTO canonical_projects (id, project_name, category, excluded) VALUES ('A','甲','',0)"
    )
    conn.execute(
        "INSERT INTO project_events (id, canonical_id, project_stage, capacity_mw, publish_date, "
        "project_developer, project_developer_en) VALUES ('A-01','A',?,100.0,'2022-06','业主','Owner')",
        (terminal,),
    )
    conn.execute(
        "INSERT INTO chinese_entities (event_id, implementing_entity, implementing_entity_en, "
        "subsidiary_name, subsidiary_name_en, involvement_type, is_central_soe, entity_id, "
        "entity_name, group_entity_id) VALUES ('A-01','中国能源建设集团有限公司','CEEC',"
        "'中国电力工程顾问集团有限公司','CPECC','EPC总承包',1,?,?,?)",
        (CPECC, "中国电力工程顾问集团有限公司", CEEC),
    )
    conn.commit()
    conn.close()


def test_all_entities_carry_hierarchy_path(tmp_path):
    src, dst = tmp_path / "src.db", tmp_path / "dst.db"
    _make_src(src)
    assert flat_builder.build_flat(src, dst, province_map={}) == 1

    conn = sqlite3.connect(dst)
    all_entities, primary_id, primary_path = conn.execute(
        "SELECT all_entities, primary_entity_id, primary_entity_path FROM projects_flat"
    ).fetchone()
    conn.close()

    rows = json.loads(all_entities)
    assert len(rows) == 1
    entity = rows[0]
    assert entity["entity_id"] == CPECC
    assert entity["entity_name"] == "中国电力工程顾问集团有限公司"
    assert entity["group_id"] == CEEC
    assert entity["parent_name"] == "中国能源建设集团有限公司"
    assert entity["level"] == 2
    assert entity["path"] == ["中国能源建设集团有限公司", "中国电力工程顾问集团有限公司"]
    assert entity["ownership_type"]

    assert primary_id == CEEC
    assert json.loads(primary_path) == ["中国能源建设集团有限公司", "中国电力工程顾问集团有限公司"]
