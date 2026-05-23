"""S2a: s02a 三表数值列 schema —— 原文入 *_raw、解析值入 REAL，canonical 加 excluded。"""
# 先 import 工具模块（顶部 shim 把仓库根加入 sys.path，使 bri_parse 可导入）
from s02a_duplicate_merger.tools.duplicate_merger_tool import _init_db
import bri_parse


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
