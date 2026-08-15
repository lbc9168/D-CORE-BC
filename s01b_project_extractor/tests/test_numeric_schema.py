"""S2a: extracted_projects 数值列 schema —— 原文入 *_raw，解析值入 REAL。"""
# 先 import 工具模块（其顶部 shim 会把仓库根加入 sys.path，使 bri_parse 可导入）
from s01b_project_extractor.tools.project_extractor_tool import _init_projects_db
import bri_parse


def test_capacity_stored_as_real(tmp_path):
    conn = _init_projects_db(str(tmp_path / "projects.db"))
    raw = "约50MW"
    conn.execute(
        "INSERT INTO extracted_projects (project_name, capacity_mw_raw, capacity_mw, capacity_source) "
        "VALUES (?, ?, ?, ?)",
        ("测试项目", raw, bri_parse.parse_number(raw), "original"),
    )
    conn.commit()
    row = conn.execute(
        "SELECT capacity_mw, capacity_mw_raw, typeof(capacity_mw), capacity_source "
        "FROM extracted_projects"
    ).fetchone()
    conn.close()
    assert row[0] == 50.0
    assert row[1] == "约50MW"
    assert row[2] == "real"
    assert row[3] == "original"


def test_energy_fact_columns_exist_in_extracted_projects(tmp_path):
    """新建和旧库升级路径都必须提供完整事件级能源事实列。"""
    conn = _init_projects_db(str(tmp_path / "projects.db"))
    columns = {row[1]: row[2] for row in conn.execute("PRAGMA table_info(extracted_projects)")}
    conn.close()
    assert columns["generation_sources_json"] == "TEXT"
    assert columns["storage_status"] == "TEXT"
    assert columns["storage_power_mw_raw"] == "TEXT"
    assert columns["storage_energy_mwh_raw"] == "TEXT"
    assert columns["energy_composition_evidence"] == "TEXT"


def test_txmf_atomic_columns_exist_in_new_and_upgraded_projects_db(tmp_path):
    """S01B 新建/升级库都提供四 raw、三 TX 数值和一组 MF 数值单位列。"""
    conn = _init_projects_db(str(tmp_path / "projects.db"))
    columns = {row[1]: row[2] for row in conn.execute("PRAGMA table_info(extracted_projects)")}
    conn.close()
    expected = {
        "voltage_kv_raw": "TEXT", "voltage_kv": "REAL",
        "line_length_km_raw": "TEXT", "line_length_km": "REAL",
        "transmission_capacity_mw_raw": "TEXT", "transmission_capacity_mw": "REAL",
        "production_capacity_raw": "TEXT", "production_capacity_value": "REAL",
        "production_capacity_unit": "TEXT",
    }
    assert {key: columns[key] for key in expected} == expected
