"""S01B 专用原子指标进入 S02A 事件列且优先于旧回填的集成测试。"""
from s01b_project_extractor.tools.project_extractor_tool import _init_projects_db
from s02a_duplicate_merger.tools.duplicate_merger_tool import (
    _group_into_logical_projects,
    _init_db,
    _load_from_db,
    _write_to_db,
)


def _project(category):
    """构造可直接写入 S02A 的最小逻辑项目。"""
    return {
        "record_type": "具体项目", "project_name": "肯尼亚测试工程",
        "project_name_en": "Kenya Test", "name_en_quality": "ai_translated",
        "country": "肯尼亚", "country_iso": "KEN", "bri_country": "Yes",
        "ldc": "No", "location": "", "category": category,
        "capacity_mw": "999MW", "capacity_details": "旧明细500kV 88km 2×50MVA",
        "generation_sources_json": '["光伏"]', "storage_status": "confirmed",
        "storage_power_mw": 10.0, "storage_energy_mwh": 20.0,
        "storage_power_mw_raw": "10MW", "storage_energy_mwh_raw": "20MWh",
        "energy_composition_evidence": "光伏配储", "project_stage": "在建",
        "project_developer": "", "project_developer_en": "",
        "planned_start_date": "", "planned_complete_date": "",
        "total_investment_usd": "", "contract_amount_usd": "",
        "contract_amount_usd_raw": "", "financing_amount_usd": "",
        "financing_amount_usd_raw": "", "investment_date": "",
        "financing_model": "Unknown", "financing_details": "",
        "source_links": ["https://example.test/txmf"], "official_website_links": [],
        "non_official_website_links": [], "publish_date": "2026-08-14",
        "additional_remarks": "", "project_summary": "", "excluded": 0,
        "exclude_reason": "", "entities": [],
    }


def test_tx_atomic_values_and_raw_are_written_while_capacity_is_cleared(tmp_path):
    """TX 原子值/raw 直达事件列，能源事实和通用装机被隔离。"""
    conn = _init_db(str(tmp_path / "tx.db"))
    project = _project("输变电建设")
    project.update({
        "voltage_kv_raw": "132kV", "voltage_kv": 132.0,
        "line_length_km_raw": "全长27km", "line_length_km": 27.0,
        "transmission_capacity_mw_raw": "3×40MVA", "transmission_capacity_mw": 120.0,
    })
    _write_to_db(conn, [project], trust_dedup=True)
    row = conn.execute(
        "SELECT capacity_mw_raw,capacity_mw,generation_sources_json,storage_status,"
        "voltage_kv_raw,voltage_kv,line_length_km_raw,line_length_km,"
        "transmission_capacity_mw_raw,transmission_capacity_mw FROM project_events"
    ).fetchone()
    conn.close()
    assert row[:4] == (None, None, "[]", "not_mentioned")
    assert row[4:] == ("132kV", 132.0, "全长27km", 27.0, "3×40MVA", 120.0)


def test_mf_atomic_capacity_is_separate_from_storage_and_generation(tmp_path):
    """MF GWh/年只写生产产能，不能污染发电或项目储能字段。"""
    conn = _init_db(str(tmp_path / "mf.db"))
    project = _project("新能源制造业")
    project.update({
        "production_capacity_raw": "年产能10GWh",
        "production_capacity_value": 10.0,
        "production_capacity_unit": "GWh/年",
    })
    _write_to_db(conn, [project], trust_dedup=True)
    row = conn.execute(
        "SELECT capacity_mw,storage_power_mw,storage_energy_mwh,generation_sources_json,"
        "storage_status,production_capacity_raw,production_capacity_value,production_capacity_unit "
        "FROM project_events"
    ).fetchone()
    conn.close()
    assert row == (None, None, None, "[]", "not_mentioned", "年产能10GWh", 10.0, "GWh/年")


def test_s01b_database_to_s02a_event_preserves_atomic_tx_contract(tmp_path):
    """真实 projects.db 列名映射与归组路径把 S01B TX 原子字段完整送入 S02A。"""
    projects_db = tmp_path / "projects.db"
    extracted = _init_projects_db(str(projects_db))
    extracted.execute(
        """INSERT INTO extracted_projects
           (record_type,project_name,project_name_en,country,country_iso,category,project_stage,
            source_link,publish_date,voltage_kv_raw,voltage_kv,line_length_km_raw,line_length_km,
            transmission_capacity_mw_raw,transmission_capacity_mw,imported_to_s02a)
           VALUES ('具体项目','肯尼亚132kV输电工程','Kenya 132 kV Transmission','肯尼亚','KEN',
                   '输变电建设','在建','https://example.test/end-to-end','2026-08-14',
                   '132kV',132.0,'全长27km',27.0,'3×40MVA',120.0,0)"""
    )
    extracted.commit()
    extracted.close()
    frame, _ids = _load_from_db(str(projects_db))
    logical = _group_into_logical_projects(frame)
    main = _init_db(str(tmp_path / "main.db"))
    _write_to_db(main, logical, trust_dedup=True)
    row = main.execute(
        "SELECT voltage_kv_raw,voltage_kv,line_length_km_raw,line_length_km,"
        "transmission_capacity_mw_raw,transmission_capacity_mw,capacity_mw FROM project_events"
    ).fetchone()
    main.close()
    assert row == ("132kV", 132.0, "全长27km", 27.0, "3×40MVA", 120.0, None)
