"""run_eval 多项目端到端：注入 mock extract_fn（不调 Gemini），断言 project_set/字段/实体/报告。"""
import json
import sqlite3
from eval import run_eval, gold_loader

def _make_raw_news(path):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE raw_news (id INTEGER, url TEXT, title TEXT, content TEXT, author TEXT, publish_date TEXT, crawl_time TEXT, passed_keyword_filter INTEGER, batch_id TEXT)")
    c.execute("INSERT INTO raw_news VALUES (1,'http://x','双电站','正文双电站','','2024-01-01','',1,'b')")
    c.commit(); c.close()

def _write_gold(path):
    lines = [json.dumps({"source_link":"http://x","status":"valid","projects":[
        {"project_name":"阿尔法光伏电站","category":"光伏","capacity_mw":"50","project_stage":"中标",
         "financing_model":"Unknown","entities":[{"name":"中国电建","involvement_type":"EPC总承包"}]},
        {"project_name":"贝塔光伏电站","category":"光伏","capacity_mw":"50","project_stage":"中标",
         "financing_model":"Unknown","entities":[{"name":"中国电建","involvement_type":"EPC总承包"}]}]}, ensure_ascii=False)]
    path.write_text("\n".join(lines), encoding="utf-8")

def _mock_extract_fn(title, content, date):
    # 阿尔法对、贝塔类别错+角色错、多吐一个储能（过度拆分）
    return {"status":"valid_project","projects":[
        {"common_info":{"Project Name":"阿尔法光伏","Category":"光伏","Capacity (MW)":"50","Project Stage":"中标","Financing Model":"Unknown"},
         "chinese_entities":[{"Implementing Chinese Entity":"中国电建","Type of involvement of Chinese Entities":"EPC总承包"}]},
        {"common_info":{"Project Name":"贝塔电站","Category":"综合能源","Capacity (MW)":"48","Project Stage":"中标","Financing Model":"Unknown"},
         "chinese_entities":[{"Implementing Chinese Entity":"中国电建","Type of involvement of Chinese Entities":"设备供应商"}]},
        {"common_info":{"Project Name":"某配套储能","Category":"储能","Project Stage":"中标","Financing Model":"Unknown"},
         "chinese_entities":[{"Implementing Chinese Entity":"中国电建","Type of involvement of Chinese Entities":"EPC总承包"}]}]}

def test_e2e_multiproject(tmp_path):
    raw = tmp_path / "raw.db"; gold = tmp_path / "gold.jsonl"
    _make_raw_news(raw); _write_gold(gold)
    rep = run_eval.run_eval(gold, extract_fn=_mock_extract_fn, extractor_version="e2e",
                            raw_news_db=raw, cache_dir=tmp_path/"c", reports_dir=tmp_path/"r", use_cache=False)
    assert rep["n_gold"] == 1 and rep["n_scored_articles"] == 1
    assert rep["status_accuracy"] == 1.0
    assert rep["project_set"]["matched"] == 2 and rep["project_set"]["pred_total"] == 3
    assert rep["project_set"]["gold_total"] == 2
    assert abs(rep["project_set"]["recall"] - 1.0) < 1e-9
    assert rep["fields"]["category"]["correct"] == 1 and rep["fields"]["category"]["total"] == 2
    assert rep["fields"]["capacity_mw"]["correct"] == 2
    assert abs(rep["entity"]["name_f1_mean"] - 1.0) < 1e-9
    assert rep["entity"]["role_accuracy"] == 0.5
    assert rep["weighted_total"] == 0.85
    assert rep["fields"]["category"]["total"] == 2
    assert rep["taxonomy"]["generation_sources"]["gold_total"] == 0
    assert rep["taxonomy"]["storage_status"]["total"] == 0
    written = list((tmp_path/"r").glob("*.json"))
    assert len(written) == 1

def test_e2e_gold_loader_rejects_bad(tmp_path):
    p = tmp_path / "g.jsonl"
    p.write_text('{"source_link":"http://a","status":"valid","projects":[]}\n{"source_link":"http://b","status":"BAD","projects":[]}', encoding="utf-8")
    import pytest
    with pytest.raises(ValueError) as ei:
        gold_loader.load(p)
    assert "第 2 行" in str(ei.value)


def test_e2e_reports_deterministic_slices_and_separate_challenge_metrics(tmp_path):
    """Aggregate split, category, and risk slices without folding Challenge into regression metrics."""
    raw = tmp_path / "raw.db"; gold = tmp_path / "gold.jsonl"
    _make_raw_news(raw)
    lines = [
        {"source_link": "http://x", "status": "valid", "dataset_split": "regression",
         "risk_tags": ["capacity"], "projects": [{"project_name": "阿尔法光伏", "category": "光伏"}]},
        {"source_link": "http://x", "status": "valid", "dataset_split": "challenge",
         "risk_tags": ["hard"], "projects": [{"project_name": "阿尔法光伏", "category": "光伏"}]},
    ]
    gold.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in lines), encoding="utf-8")
    report = run_eval.run_eval(gold, extract_fn=_mock_extract_fn, extractor_version="slices",
                               raw_news_db=raw, cache_dir=tmp_path / "c", reports_dir=tmp_path / "r",
                               use_cache=False)

    assert report["weighted_total_label"] == "legacy"
    assert report["slices"]["split"]["regression"]["n_gold"] == 1
    assert report["slices"]["category"]["光伏"]["n_gold"] == 2
    assert report["slices"]["risk_tag"]["capacity"]["n_gold"] == 1
    assert report["core_metrics"]["regression"]["n_gold"] == 1
    assert report["core_metrics"]["challenge"]["n_gold"] == 1


def test_e2e_predeclares_empty_groups_and_excludes_challenge_from_regression(tmp_path):
    """Skipped or failed rows keep their groups, while Challenge accuracy cannot change regression core metrics."""
    raw = tmp_path / "raw.db"; gold = tmp_path / "gold.jsonl"
    _make_raw_news(raw)
    lines = [
        {"source_link": "http://x", "status": "valid", "dataset_split": "regression", "risk_tags": [],
         "projects": [{"project_name": "阿尔法光伏", "category": "光伏", "location": None,
                       "field_eligibility": {"location": True, "generation_sources": True, "financing_amount_usd": False},
                       "generation_sources": None}]},
        {"source_link": "http://x", "status": "valid", "dataset_split": "challenge", "risk_tags": ["hard"],
         "projects": [{"project_name": "不相同的项目", "category": "风电"}]},
        {"source_link": "http://missing", "status": "valid", "dataset_split": "development", "risk_tags": ["missing"],
         "projects": [{"project_name": "缺失", "category": "水电"}]},
    ]
    gold.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in lines), encoding="utf-8")
    report = run_eval.run_eval(gold, extract_fn=_mock_extract_fn, extractor_version="predeclared",
                               raw_news_db=raw, cache_dir=tmp_path / "c", reports_dir=tmp_path / "r", use_cache=False)

    assert report["core_metrics"]["regression"]["status_accuracy"] == 1.0
    assert report["core_metrics"]["regression"]["fields"]["location"] == {"correct": 1, "total": 1, "accuracy": 1.0}
    assert report["core_metrics"]["regression"]["fields"]["generation_sources"]["accuracy"] == 1.0
    assert report["core_metrics"]["regression"]["fields"]["financing_amount_usd"]["total"] == 0
    assert report["core_metrics"]["regression"]["project_set"]["recall"] == 1.0
    assert report["core_metrics"]["challenge"]["project_set"]["recall"] == 0.0
    assert report["slices"]["split"]["development"]["n_gold"] == 1
    assert report["slices"]["split"]["development"]["evaluated_count"] == 0
    assert report["slices"]["split"]["challenge"]["evaluated_count"] == 1
    assert report["slices"]["risk_tag"]["unspecified"]["n_gold"] == 1
    assert report["slices"]["category"]["水电"]["evaluated_count"] == 0
