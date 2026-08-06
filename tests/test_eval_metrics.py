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

def test_norm_name_unit_aliases():
    # 中英容量单位归一：兆瓦↔MW、吉瓦↔GW、兆瓦时↔MWh、千瓦↔kW
    assert m._norm_name("印度3.4吉瓦光伏项目") == m._norm_name("印度3.4GW光伏项目")
    assert m._norm_name("捷克320兆瓦时储能项目") == m._norm_name("捷克320MWh储能项目")
    assert m._norm_name("匈牙利480兆瓦光伏项目") == m._norm_name("匈牙利480MW光伏项目")

def test_align_matches_across_unit_notation():
    # 同一项目，中文单位 vs 英文单位，应能对齐（修复匹配器单位盲点）
    pred = [{"project_name": "匈牙利480兆瓦光伏项目"}]
    gold = [{"project_name": "匈牙利480MW光伏项目"}]
    matched, unp, ung = m.align_projects(pred, gold)
    assert len(matched) == 1 and unp == [] and ung == []

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

def test_taxonomy_metrics_only_count_explicitly_labeled_gold_fields():
    gold = {"status": "valid", "projects": [{
        **_alpha_gold(), "generation_sources": ["光伏"], "storage_status": "confirmed",
        "storage_power_mw": 50, "storage_energy_mwh": 100,
    }]}
    pred = {"status": "valid", "projects": [{
        **_alpha_gold(), "generation_sources": ["光伏", "风电"], "storage_status": "confirmed",
        "storage_power_mw": 51, "storage_energy_mwh": 106, "proposed_category": "综合能源",
    }]}
    taxonomy = m.score_article(pred, gold)["taxonomy"]
    assert taxonomy["generation_sources"] == (1, 2, 1)
    assert taxonomy["storage_status"] == (1, 1)
    assert taxonomy["storage_power_mw"] == (1, 1)
    assert taxonomy["storage_energy_mwh"] == (0, 1)
    assert taxonomy["proposed_category"] == (0, 1)

def test_unlabeled_gold_project_does_not_enter_taxonomy_denominators():
    gold = {"status": "valid", "projects": [_alpha_gold()]}
    pred = {"status": "valid", "projects": [{**_alpha_gold(), "generation_sources": ["光伏"]}]}
    taxonomy = m.score_article(pred, gold)["taxonomy"]
    assert taxonomy["generation_sources"] == (0, 0, 0)
    assert taxonomy["storage_status"] == (0, 0)


def test_eligibility_excludes_only_the_inapplicable_field_from_denominator():
    """A Studio not-applicable field must not affect legacy field denominators."""
    gold = {"status": "valid", "projects": [{
        **_alpha_gold(), "financing_amount_usd": None,
        "field_eligibility": {"financing_amount_usd": False},
    }]}
    pred = {"status": "valid", "projects": [{**_alpha_gold(), "financing_amount_usd": 999}]}

    result = m.score_article(pred, gold)

    assert result["field_hits"]["financing_amount_usd"] == (0, 0)
    assert result["field_hits"]["capacity_mw"] == (1, 1)


def test_explicit_not_mentioned_field_scores_correct_empty_value():
    """An eligible null from Studio denotes an evaluated not-mentioned answer."""
    gold = {"status": "valid", "projects": [{
        **_alpha_gold(), "financing_amount_usd": None,
        "field_eligibility": {"financing_amount_usd": True},
    }]}
    pred = {"status": "valid", "projects": [{**_alpha_gold(), "financing_amount_usd": None}]}

    assert m.score_article(pred, gold)["field_hits"]["financing_amount_usd"] == (1, 1)


def test_full_field_dispatch_uses_date_and_normalized_name_semantics():
    """Versioned dates and developer names follow their registry metric groups."""
    gold = {"status": "valid", "projects": [{
        **_alpha_gold(), "country": "肯尼亚", "project_developer": "中国电建有限公司",
        "planned_start_date": "2026-01-01", "planned_complete_date": "2027-01-01",
        "investment_date": "2025-01-01", "contract_amount_usd": 10, "financing_amount_usd": 20,
    }]}
    pred = {"status": "valid", "projects": [{
        **_alpha_gold(), "country": "肯尼亚", "project_developer": "中国电建",
        "planned_start_date": "2026-01-01", "planned_complete_date": "2027-01-02",
        "investment_date": "2025-01-01", "contract_amount_usd": 10.4, "financing_amount_usd": 25,
    }]}

    hits = m.score_article(pred, gold)["field_hits"]

    assert hits["country"] == (1, 1)
    assert hits["project_developer"] == (1, 1)
    assert hits["planned_start_date"] == (1, 1)
    assert hits["planned_complete_date"] == (0, 1)
    assert hits["contract_amount_usd"] == (1, 1)
    assert hits["financing_amount_usd"] == (0, 1)


def test_unmatched_gold_project_does_not_add_eligibility_denominators():
    """Only aligned projects contribute field eligibility totals in a multi-project article."""
    gold = {"status": "valid", "projects": [
        {**_alpha_gold(), "field_eligibility": {"financing_amount_usd": False}},
        {"project_name": "未匹配风电", "category": "风电", "financing_amount_usd": 10,
         "field_eligibility": {"financing_amount_usd": True}},
    ]}
    pred = {"status": "valid", "projects": [{**_alpha_gold(), "financing_amount_usd": 999}]}

    result = m.score_article(pred, gold)

    assert result["project_set"] == (1, 1, 2)
    assert result["field_hits"]["financing_amount_usd"] == (0, 0)


def test_eligible_null_values_score_as_explicit_empty_for_all_scalar_families():
    """Rich not-mentioned nulls have one denominator and accept only the correct empty representation."""
    gold = {"status": "valid", "projects": [{
        **_alpha_gold(), "category": None, "storage_status": None, "capacity_mw": None,
        "planned_start_date": None, "project_developer": None, "location": None,
        "field_eligibility": {
            "category": True, "storage_status": True, "capacity_mw": True,
            "planned_start_date": True, "project_developer": True, "location": True,
        },
    }]}
    pred = {"status": "valid", "projects": [{
        **_alpha_gold(), "category": "", "storage_status": "not_mentioned", "capacity_mw": "",
        "planned_start_date": "", "project_developer": "", "location": "",
    }]}

    hits = m.score_article(pred, gold)["field_hits"]

    assert {field: hits[field] for field in ("category", "storage_status", "capacity_mw", "planned_start_date", "project_developer", "location")} == {
        "category": (1, 1), "storage_status": (1, 1), "capacity_mw": (1, 1),
        "planned_start_date": (1, 1), "project_developer": (1, 1), "location": (1, 1),
    }


def test_eligible_null_enum_rejects_a_nonempty_value():
    """Only empty strings and the storage-specific not-mentioned marker can match a null enum."""
    gold = {"status": "valid", "projects": [{
        **_alpha_gold(), "category": None, "field_eligibility": {"category": True},
    }]}
    pred = {"status": "valid", "projects": [{**_alpha_gold(), "category": "光伏"}]}

    assert m.score_article(pred, gold)["field_hits"]["category"] == (0, 1)


def test_eligible_null_generation_sources_is_an_empty_set_denominator():
    """An explicitly eligible null source list scores one correct empty-set field, not no field."""
    gold = {"status": "valid", "projects": [{
        **_alpha_gold(), "generation_sources": None, "field_eligibility": {"generation_sources": True},
    }]}
    pred = {"status": "valid", "projects": [{**_alpha_gold(), "generation_sources": []}]}

    result = m.score_article(pred, gold)

    assert result["field_hits"]["generation_sources"] == (1, 1)
    assert result["set_hits"]["generation_sources"] == (0, 0, 0, 1, 1)


def test_location_uses_normalized_exact_and_honors_eligibility():
    """Location is evaluable only when eligible and compares normalized text exactly."""
    gold = {"status": "valid", "projects": [{
        **_alpha_gold(), "location": " Nairobi ", "field_eligibility": {"location": True},
    }]}
    pred = {"status": "valid", "projects": [{**_alpha_gold(), "location": "nairobi"}]}
    ineligible = {"status": "valid", "projects": [{
        **_alpha_gold(), "location": "Nairobi", "field_eligibility": {"location": False},
    }]}

    assert m.score_article(pred, gold)["field_hits"]["location"] == (1, 1)
    assert m.score_article({"status": "valid", "projects": [{**_alpha_gold(), "location": "wrong"}]}, ineligible)["field_hits"]["location"] == (0, 0)


def test_location_normalization_matches_studio_nfkc_punctuation_policy():
    """Location equivalence uses Studio's NFKC, casefold, punctuation-removal canonical text form."""
    gold = {"status": "valid", "projects": [{
        **_alpha_gold(), "location": "Ｎａｉｒｏｂｉ， Kenya!", "field_eligibility": {"location": True},
    }]}
    equivalent = {"status": "valid", "projects": [{**_alpha_gold(), "location": "nairobi kenya"}]}
    different = {"status": "valid", "projects": [{**_alpha_gold(), "location": "mombasa kenya"}]}

    assert m.score_article(equivalent, gold)["field_hits"]["location"] == (1, 1)
    assert m.score_article(different, gold)["field_hits"]["location"] == (0, 1)
