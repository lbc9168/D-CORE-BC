"""S2a: s02a 三表数值列 schema —— 原文入 *_raw、解析值入 REAL，canonical 加 excluded。"""
# 先 import 工具模块（顶部 shim 把仓库根加入 sys.path，使 bri_parse 可导入）
from s02a_duplicate_merger.tools.duplicate_merger_tool import (
    _capacity_guard,
    _init_db,
    _write_to_db,
)
import json
import bri_enums
import bri_parse

# 按终态优先级排序取最早 / 最终态阶段（避免硬编码中文，规避控制台编码问题）
_STAGES = sorted(
    (s for s in bri_enums.valid_stages() if bri_enums.stage_priority(s) >= 0),
    key=bri_enums.stage_priority,
)
_EARLY, _TERMINAL = _STAGES[0], _STAGES[-1]


def test_project_events_capacity_real(tmp_path):
    conn = _init_db(str(tmp_path / "t.db"))
    conn.execute(
        "INSERT INTO canonical_projects (id, project_name, category) VALUES ('IND-PV-0001','测试','光伏')"
    )
    raw = "50-60MW"
    conn.execute(
        "INSERT INTO project_events (id, canonical_id, capacity_mw_raw, capacity_mw, "
        "total_investment_usd_raw, total_investment_usd, capacity_source) "
        "VALUES ('IND-PV-0001-01','IND-PV-0001',?,?,?,?,?)",
        (raw, bri_parse.parse_number(raw), "约100", bri_parse.parse_number("约100"), "original"),
    )
    conn.commit()
    row = conn.execute(
        "SELECT capacity_mw, capacity_mw_raw, typeof(capacity_mw), "
        "total_investment_usd, typeof(total_investment_usd) FROM project_events"
    ).fetchone()
    conn.close()
    assert row[0] == 55.0          # 范围取中点
    assert row[1] == "50-60MW"
    assert row[2] == "real"
    assert row[3] == 100.0
    assert row[4] == "real"


def test_canonical_excluded_default_zero(tmp_path):
    conn = _init_db(str(tmp_path / "t.db"))
    conn.execute("INSERT INTO canonical_projects (id, project_name) VALUES ('X','y')")
    conn.commit()
    row = conn.execute("SELECT excluded, excluded_reason FROM canonical_projects").fetchone()
    conn.close()
    assert row[0] == 0
    assert row[1] is None


def test_chinese_entities_subcontract_real(tmp_path):
    conn = _init_db(str(tmp_path / "t.db"))
    raw = "1,200"
    conn.execute(
        "INSERT INTO chinese_entities (event_id, subcontract_amount_raw, subcontract_amount) "
        "VALUES ('e1', ?, ?)",
        (raw, bri_parse.parse_number(raw)),
    )
    conn.commit()
    row = conn.execute(
        "SELECT subcontract_amount, subcontract_amount_raw, typeof(subcontract_amount) "
        "FROM chinese_entities"
    ).fetchone()
    conn.close()
    assert row[0] == 1200.0
    assert row[1] == "1,200"
    assert row[2] == "real"


def _seed_two_stage_canonical(conn):
    """canonical C 下两条事件：早期阶段容量 50、终态阶段容量 100。"""
    conn.execute("INSERT INTO canonical_projects (id, project_name) VALUES ('C','c')")
    conn.execute(
        "INSERT INTO project_events (id, canonical_id, project_stage, capacity_mw) "
        "VALUES ('C-01','C',?,50.0)", (_EARLY,),
    )
    conn.execute(
        "INSERT INTO project_events (id, canonical_id, project_stage, capacity_mw) "
        "VALUES ('C-02','C',?,100.0)", (_TERMINAL,),
    )
    conn.commit()


def test_capacity_guard_uses_terminal_event(tmp_path):
    # 代表容量应取终态事件(100)；新容量 98 与之相差 2% < 20% → 不拦截
    conn = _init_db(str(tmp_path / "t.db"))
    _seed_two_stage_canonical(conn)
    assert _capacity_guard(conn, "C", {"capacity_mw": "98"}) is False
    conn.close()


def test_capacity_guard_blocks_when_far(tmp_path):
    # 新容量 200 与终态代表容量 100 相差 100% > 20% → 拦截
    conn = _init_db(str(tmp_path / "t.db"))
    _seed_two_stage_canonical(conn)
    assert _capacity_guard(conn, "C", {"capacity_mw": "200"}) is True
    conn.close()


def test_amount_and_subcat_columns_exist(tmp_path):
    """指标扩展 2026-W30：金额 4 列 + pv_capacity_mw + canonical.subcategory 建库即有、类型正确。"""
    conn = _init_db(str(tmp_path / "t.db"))
    conn.execute(
        "INSERT INTO canonical_projects (id, project_name, subcategory) VALUES ('C','c','分布式')"
    )
    conn.execute(
        "INSERT INTO project_events (id, canonical_id, contract_amount_usd_raw, contract_amount_usd, "
        "financing_amount_usd_raw, financing_amount_usd, pv_capacity_mw) VALUES ('C-01','C',?,?,?,?,?)",
        ("合同金额约12亿元人民币", 167.0, "银团贷款35亿元", 486.0, 100.0),
    )
    conn.commit()
    row = conn.execute(
        "SELECT contract_amount_usd, typeof(contract_amount_usd), contract_amount_usd_raw, "
        "financing_amount_usd, typeof(financing_amount_usd), pv_capacity_mw FROM project_events"
    ).fetchone()
    sub = conn.execute("SELECT subcategory FROM canonical_projects").fetchone()[0]
    conn.close()
    assert row[0] == 167.0 and row[1] == "real"
    assert row[2] == "合同金额约12亿元人民币"
    assert row[3] == 486.0 and row[4] == "real"
    assert row[5] == 100.0
    assert sub == "分布式"


def test_energy_fact_and_shadow_schema_exist(tmp_path):
    """s02a 新库必须包含事件事实列和不改生产 category 的 canonical 影子快照表。"""
    conn = _init_db(str(tmp_path / "t.db"))
    event_columns = {row[1] for row in conn.execute("PRAGMA table_info(project_events)")}
    canonical_columns = {row[1] for row in conn.execute("PRAGMA table_info(canonical_projects)")}
    shadow_columns = {row[1] for row in conn.execute("PRAGMA table_info(project_classification_shadow)")}
    conn.close()
    assert {
        "generation_sources_json", "storage_status", "storage_power_mw_raw",
        "storage_energy_mwh_raw", "energy_composition_evidence",
    } <= event_columns
    assert {
        "canonical_id", "generation_sources_json", "storage_status", "storage_power_mw",
        "storage_energy_mwh", "proposed_category",
        "technology_tags_json", "classification_version", "needs_review", "review_reason",
        "classification_source", "manual_locked", "updated_at",
    } <= shadow_columns
    assert "technology_tags_json" in canonical_columns


def test_partial_legacy_shadow_schema_is_upgraded(tmp_path):
    """旧库若已有不完整 shadow 表，_init_db 也必须幂等补齐 v2 全部列。"""
    import sqlite3

    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE project_classification_shadow (canonical_id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    conn = _init_db(str(path))
    columns = {row[1] for row in conn.execute("PRAGMA table_info(project_classification_shadow)")}
    conn.close()
    assert {
        "generation_sources_json", "storage_status", "storage_power_mw",
        "storage_energy_mwh", "proposed_category", "technology_tags_json",
        "classification_version", "needs_review", "review_reason",
        "classification_source", "manual_locked", "updated_at",
    } <= columns


def test_s02a_aggregates_energy_facts_and_writes_v2_category_and_tags(tmp_path):
    """s02a 写事件后必须按 canonical 事实聚合，确定性写正式 category、标签及 shadow。"""
    conn = _init_db(str(tmp_path / "taxonomy.db"))
    project = {
        "record_type": "具体项目",
        "project_name": "肯尼亚100MW光储项目",
        "project_name_en": "Kenya 100 MW Solar Storage Project",
        "name_en_quality": "ai_translated",
        "country": "肯尼亚",
        "country_iso": "KEN",
        "bri_country": "Yes",
        "ldc": "No",
        "location": "",
        "category": "综合能源",
        "capacity_mw": "100MW",
        "capacity_details": "PV: 100MW, BESS: 20MW/40MWh",
        "generation_sources_json": '["光伏"]',
        "storage_status": "confirmed",
        "storage_power_mw_raw": "20MW",
        "storage_energy_mwh_raw": "40MWh",
        "storage_power_mw": 20.0,
        "storage_energy_mwh": 40.0,
        "energy_composition_evidence": "建设100MW光伏电站并配套20MW/40MWh储能系统",
        "project_stage": _EARLY,
        "project_developer": "",
        "project_developer_en": "",
        "planned_start_date": "",
        "planned_complete_date": "",
        "total_investment_usd": "",
        "contract_amount_usd": "",
        "contract_amount_usd_raw": "",
        "financing_amount_usd": "",
        "financing_amount_usd_raw": "",
        "investment_date": "",
        "financing_model": "Unknown",
        "financing_details": "",
        "source_links": ["https://example.test/a"],
        "official_website_links": [],
        "non_official_website_links": [],
        "publish_date": "2026-01-01",
        "additional_remarks": "",
        "project_summary": "",
        "excluded": 0,
        "exclude_reason": "",
        "entities": [],
    }
    _write_to_db(conn, [project], trust_dedup=True)
    canonical = conn.execute(
        "SELECT category, technology_tags_json FROM canonical_projects"
    ).fetchone()
    shadow = conn.execute(
        "SELECT proposed_category, technology_tags_json, classification_version, needs_review "
        "FROM project_classification_shadow"
    ).fetchone()
    event = conn.execute(
        "SELECT capacity_mw, storage_power_mw, storage_energy_mwh FROM project_events"
    ).fetchone()
    conn.close()
    assert canonical[0] == "光伏"
    assert json.loads(canonical[1]) == ["光储"]
    assert shadow[0] == "光伏"
    assert json.loads(shadow[1]) == ["光储"]
    assert shadow[2:] == ("energy_taxonomy_v2.1", 0)
    assert event == (100.0, 20.0, 40.0)
