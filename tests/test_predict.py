from eval.predict import flat_prediction

def test_flat_valid_main_project():
    result = {
        "status": "valid_project",
        "projects": [{
            "common_info": {"Category": "光伏", "Capacity (MW)": "50",
                            "Project Stage": "运营", "Location(State)": "X省",
                            "Total Project Investment (Million USD)": "100",
                            "Financing Model": "EPC"},
            "chinese_entities": [
                {"Implementing Chinese Entity": "中国电建",
                 "Type of involvement of Chinese Entities": "EPC总承包"},
                {"Implementing Chinese Entity": "三峡"},
            ],
        }],
    }
    flat = flat_prediction(result)
    assert flat["status"] == "valid"
    assert flat["category"] == "光伏"
    assert flat["capacity_mw"] == "50"
    assert flat["involvement_type"] == "EPC总承包"
    assert set(flat["entities"]) == {"中国电建", "三峡"}

def test_flat_irrelevant():
    flat = flat_prediction({"status": "irrelevant"})
    assert flat["status"] == "irrelevant"
    assert flat["entities"] == []

def test_flat_no_chinese():
    flat = flat_prediction({"status": "no_chinese_participation"})
    assert flat["status"] == "no_chinese"
