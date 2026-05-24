import eval.metrics as m

def test_status_exact():
    assert m.cmp_status("valid", "valid") is True
    assert m.cmp_status("valid", "irrelevant") is False

def test_enum_normalized_equal():
    assert m.cmp_enum("category", "光伏", "光伏") is True
    assert m.cmp_enum("category", "光伏", "风电") is False

def test_numeric_within_5pct():
    assert m.cmp_numeric("50", "51") is True       # 2% diff -> correct
    assert m.cmp_numeric("50", "60") is False      # 20% diff -> wrong
    assert m.cmp_numeric("50", None) is False      # one side empty = wrong
    assert m.cmp_numeric(None, None) is True       # both empty = correct

def test_align_same_capacity_diff_name():
    pred = [{"project_name":"阿尔法光伏","category":"光伏","capacity_mw":"50"},
            {"project_name":"贝塔电站","category":"光伏","capacity_mw":"48"}]
    gold = [{"project_name":"阿尔法光伏电站","category":"光伏","capacity_mw":"50"},
            {"project_name":"贝塔光伏电站","category":"光伏","capacity_mw":"50"}]
    matched, unp, ung = m.align_projects(pred, gold)
    assert len(matched) == 2 and unp == [] and ung == []

def test_align_oversplit_creates_unmatched_pred():
    pred = [{"project_name":"阿尔法光伏电站"}, {"project_name":"贝塔光伏电站"}, {"project_name":"某配套储能"}]
    gold = [{"project_name":"阿尔法光伏电站"}, {"project_name":"贝塔光伏电站"}]
    matched, unp, ung = m.align_projects(pred, gold)
    assert len(matched) == 2 and len(unp) == 1 and ung == []

def test_align_merge_creates_unmatched_gold():
    pred = [{"project_name":"阿尔法光伏电站"}]
    gold = [{"project_name":"阿尔法光伏电站"}, {"project_name":"贝塔光伏电站"}]
    matched, unp, ung = m.align_projects(pred, gold)
    assert len(matched) == 1 and unp == [] and len(ung) == 1

def test_align_below_threshold_no_match():
    pred = [{"project_name":"完全不相关的名字XYZ"}]
    gold = [{"project_name":"阿尔法光伏电站"}]
    matched, unp, ung = m.align_projects(pred, gold)
    assert matched == [] and len(unp) == 1 and len(ung) == 1

def test_score_entities_all_match_roles_correct():
    pred = [{"name":"中国电建","involvement_type":"EPC总承包"}]
    gold = [{"name":"中国电建","involvement_type":"EPC总承包"}]
    np_, nr_, nf1, rc, rm = m.score_entities(pred, gold)
    assert nf1 == 1.0 and rc == 1 and rm == 1

def test_score_entities_name_match_role_wrong():
    pred = [{"name":"中国电建","involvement_type":"设备供应商"}]
    gold = [{"name":"中国电建","involvement_type":"EPC总承包"}]
    np_, nr_, nf1, rc, rm = m.score_entities(pred, gold)
    assert nf1 == 1.0 and rc == 0 and rm == 1

def test_score_entities_partial_name_overlap():
    pred = [{"name":"中国电建","involvement_type":"EPC总承包"},
            {"name":"三峡","involvement_type":"股权投资方"}]
    gold = [{"name":"中国电建","involvement_type":"EPC总承包"}]
    np_, nr_, nf1, rc, rm = m.score_entities(pred, gold)
    assert nr_ == 1.0 and 0 < np_ < 1 and rm == 1 and rc == 1

def test_score_entities_both_empty():
    assert m.score_entities([], []) == (1.0, 1.0, 1.0, 0, 0)

def _alpha_gold():
    return {"project_name":"阿尔法光伏电站","category":"光伏","capacity_mw":"50","project_stage":"中标",
            "financing_model":"Unknown","total_investment_usd":"-","location":"肯尼亚",
            "entities":[{"name":"中国电建","involvement_type":"EPC总承包"}]}

def test_score_article_worked_example():
    gold = {"status":"valid","projects":[
        _alpha_gold(),
        {"project_name":"贝塔光伏电站","category":"光伏","capacity_mw":"50","project_stage":"中标",
         "financing_model":"Unknown","total_investment_usd":"-","location":"肯尼亚",
         "entities":[{"name":"中国电建","involvement_type":"EPC总承包"}]}]}
    pred = {"status":"valid","projects":[
        {"project_name":"阿尔法光伏","category":"光伏","capacity_mw":"50","project_stage":"中标",
         "financing_model":"Unknown","total_investment_usd":None,"location":"肯尼亚",
         "entities":[{"name":"中国电建","involvement_type":"EPC总承包"}]},
        {"project_name":"贝塔电站","category":"综合能源","capacity_mw":"48","project_stage":"中标",
         "financing_model":"Unknown","total_investment_usd":None,"location":"",
         "entities":[{"name":"中国电建","involvement_type":"设备供应商"}]},
        {"project_name":"某配套储能","category":"储能","capacity_mw":None,"project_stage":"中标",
         "financing_model":"Unknown","total_investment_usd":None,"location":"",
         "entities":[{"name":"中国电建","involvement_type":"EPC总承包"}]}]}
    r = m.score_article(pred, gold)
    assert r["status_correct"] is True and r["scored"] is True
    assert r["project_set"] == (2, 3, 2)            # matched, pred_total, gold_total
    assert r["field_hits"]["category"] == (1, 2)
    assert r["field_hits"]["capacity_mw"] == (2, 2)
    assert r["field_hits"]["project_stage"] == (2, 2)
    sum_name_f1, n_matched, role_correct, role_matched = r["entity"]
    assert sum_name_f1 == 2.0 and n_matched == 2 and role_correct == 1 and role_matched == 2

def test_score_article_status_wrong_skips_projects():
    pred = {"status":"irrelevant","projects":[]}
    gold = {"status":"valid","projects":[_alpha_gold()]}
    r = m.score_article(pred, gold)
    assert r["status_correct"] is False and r["scored"] is False and r["project_set"] is None

def test_score_article_gold_irrelevant_not_scored():
    r = m.score_article({"status":"irrelevant","projects":[]}, {"status":"irrelevant","projects":[]})
    assert r["status_correct"] is True and r["scored"] is False
