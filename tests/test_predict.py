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
