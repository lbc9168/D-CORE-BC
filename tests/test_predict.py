"""flat_prediction：原始提取结果 → 文章级 {status, projects:[{字段, entities:[{name,involvement_type}]}]}。"""
from eval.predict import flat_prediction

def test_flat_multi_project_with_roles():
    result = {"status":"valid_project","projects":[
        {"common_info":{"Category":"光伏","Capacity (MW)":"50","Project Stage":"中标",
                        "Location(State)":"肯尼亚","Total Project Investment (Million USD)":"100",
                        "Financing Model":"Unknown"},
         "chinese_entities":[{"Implementing Chinese Entity":"中国电建","Type of involvement of Chinese Entities":"EPC总承包"},
                             {"Implementing Chinese Entity":"三峡","Type of involvement of Chinese Entities":"股权投资方"}]},
        {"common_info":{"Category":"风电","Capacity (MW)":"30"},
         "chinese_entities":[{"Implementing Chinese Entity":"金风科技","Type of involvement of Chinese Entities":"设备供应商"}]}]}
    art = flat_prediction(result)
    assert art["status"] == "valid"
    assert len(art["projects"]) == 2
    p0 = art["projects"][0]
    assert p0["category"] == "光伏" and p0["capacity_mw"] == "50" and p0["project_stage"] == "中标"
    assert {e["name"] for e in p0["entities"]} == {"中国电建","三峡"}
    assert {(e["name"], e["involvement_type"]) for e in p0["entities"]} == \
        {("中国电建","EPC总承包"), ("三峡","股权投资方")}
    assert art["projects"][1]["category"] == "风电"

def test_flat_irrelevant_empty_projects():
    art = flat_prediction({"status":"irrelevant"})
    assert art == {"status":"irrelevant", "projects":[]}

def test_flat_no_chinese_empty_projects():
    art = flat_prediction({"status":"no_chinese_participation"})
    assert art["status"] == "no_chinese" and art["projects"] == []

def test_flat_maps_chinese_unnamed():
    from eval.predict import flat_prediction
    art = flat_prediction({"status": "chinese_unnamed_participation", "projects": []})
    assert art["status"] == "chinese_unnamed" and art["projects"] == []

def test_flat_exposes_energy_facts_and_derives_shadow_category():
    result = {"status": "valid_project", "projects": [{"common_info": {
        "Project Name": "A", "Category": "综合能源",
        "generation_sources_json": '["光伏"]',
        "storage_status_normalized": "confirmed",
        "storage_power_mw_parsed": 50,
        "storage_energy_mwh_parsed": 100,
    }}]}
    project = flat_prediction(result)["projects"][0]
    assert project["generation_sources"] == ["光伏"]
    assert project["storage_status"] == "confirmed"
    assert project["proposed_category"] == "光伏"


def test_flat_exposes_all_versioned_projection_fields():
    """Flatten every Studio-scored project field from the staged extractor shape."""
    result = {"status": "valid_project", "projects": [{"common_info": {
        "Project Name": "A", "Country": "肯尼亚", "Project Developer": "中国电建",
        "Planned Start Date": "2026-01-01", "Planned Complete Date": "2027-01-01",
        "Investment Date": "2025-01-01", "Contract Amount (Million USD)": 12.0,
        "Financing Amount (Million USD)": 8.0, "Project Summary": "摘要",
        "Storage Power": "50", "Storage Energy": "100",
    }}]}

    project = flat_prediction(result)["projects"][0]

    assert project["country"] == "肯尼亚"
    assert project["project_developer"] == "中国电建"
    assert project["planned_start_date"] == "2026-01-01"
    assert project["planned_complete_date"] == "2027-01-01"
    assert project["investment_date"] == "2025-01-01"
    assert project["contract_amount_usd"] == 12.0
    assert project["financing_amount_usd"] == 8.0
    assert project["project_summary"] == "摘要"
