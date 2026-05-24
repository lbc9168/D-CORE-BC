"""csv_to_gold：多行 CSV 按 source_link 聚合成 {status, projects[]}；entities 解析 名字(角色) 对。"""
import csv
from eval import csv_to_gold, gold_loader

_COLS = ["source_link","article_id","ai_status","status","ai_project_name","project_name",
         "ai_category","category","ai_capacity_mw","capacity_mw","ai_project_stage","project_stage",
         "ai_location","location","ai_total_investment_usd","total_investment_usd",
         "ai_financing_model","financing_model","ai_entities","entities","notes"]

def _write(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_COLS); w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in _COLS})

def test_two_projects_one_article(tmp_path):
    csvp = tmp_path / "draft.csv"; out = tmp_path / "gold.jsonl"
    _write(csvp, [
        {"source_link":"http://x","ai_status":"valid","ai_project_name":"A电站","ai_category":"光伏",
         "ai_entities":"中国电建(EPC总承包)"},
        {"source_link":"http://x","ai_status":"valid","ai_project_name":"B电站","ai_category":"光伏",
         "ai_entities":"中国电建(EPC总承包); 三峡(股权投资方)"},
    ])
    csv_to_gold.convert(csvp, out)
    rec = gold_loader.load(out)[0]
    assert rec["status"] == "valid" and len(rec["projects"]) == 2
    p1 = rec["projects"][1]
    assert {(e["name"], e["involvement_type"]) for e in p1["entities"]} == \
        {("中国电建","EPC总承包"), ("三峡","股权投资方")}

def test_correction_overrides_and_empty_marker(tmp_path):
    csvp = tmp_path / "draft.csv"; out = tmp_path / "gold.jsonl"
    _write(csvp, [
        {"source_link":"http://x","ai_status":"valid","ai_project_name":"A","ai_category":"光伏",
         "category":"风电","ai_capacity_mw":"50","capacity_mw":"-","ai_entities":"错企(EPC总承包)",
         "entities":"中国电建(设备供应商)"},
    ])
    csv_to_gold.convert(csvp, out)
    p = gold_loader.load(out)[0]["projects"][0]
    assert p["category"] == "风电" and p["capacity_mw"] is None
    assert p["entities"] == [{"name":"中国电建","involvement_type":"设备供应商"}]

def test_irrelevant_zero_project(tmp_path):
    csvp = tmp_path / "draft.csv"; out = tmp_path / "gold.jsonl"
    _write(csvp, [{"source_link":"http://y","ai_status":"irrelevant","status":"irrelevant"}])
    csv_to_gold.convert(csvp, out)
    rec = gold_loader.load(out)[0]
    assert rec["status"] == "irrelevant" and rec["projects"] == []

def test_skip_article_without_status(tmp_path):
    csvp = tmp_path / "draft.csv"; out = tmp_path / "gold.jsonl"
    _write(csvp, [{"source_link":"http://neg","ai_status":"","status":""}])
    n = csv_to_gold.convert(csvp, out)
    assert n == 0
