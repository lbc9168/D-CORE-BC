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

def test_entity_set_f1():
    p, r, f1 = m.entity_prf(["中国电建","三峡"], ["中国电建"])
    assert r == 1.0 and 0 < p < 1 and 0 < f1 < 1

def test_score_record_skips_fields_when_status_wrong():
    pred = {"status": "irrelevant", "category": "光伏"}
    gold = {"status": "valid", "category": "风电"}
    rec = m.score_record(pred, gold)
    assert rec["status_correct"] is False
    assert rec["fields"] == {}   # status wrong -> fields not scored
