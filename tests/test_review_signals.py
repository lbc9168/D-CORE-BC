"""signals：从项目记录算可疑信号 → 候选(可疑字段列表)。"""
from review import signals

def test_unknown_entity_flagged():
    proj = {"category": "光伏", "capacity_mw": "50", "involvement_type": "EPC总承包",
            "entities": [{"name": "Unknown"}]}
    assert "entities" in signals.suspicious_fields(proj)

def test_missing_capacity_for_capacity_category_flagged():
    proj = {"category": "光伏", "capacity_mw": "", "involvement_type": "EPC总承包", "entities": [{"name":"中国电建"}]}
    assert "capacity_mw" in signals.suspicious_fields(proj)

def test_no_capacity_category_not_flagged_for_missing_capacity():
    # 储能/输变电建设等 no_capacity 类，容量空不算可疑
    proj = {"category": "储能", "capacity_mw": "", "involvement_type": "EPC总承包", "entities": [{"name":"中国电建"}]}
    assert "capacity_mw" not in signals.suspicious_fields(proj)

def test_unknown_involvement_flagged():
    proj = {"category": "光伏", "capacity_mw": "50", "involvement_type": "Unknown", "entities": [{"name":"中国电建"}]}
    assert "involvement_type" in signals.suspicious_fields(proj)

def test_clean_project_not_suspicious():
    proj = {"category": "光伏", "capacity_mw": "50", "involvement_type": "EPC总承包", "entities": [{"name":"中国电建"}]}
    assert signals.suspicious_fields(proj) == []

def test_placeholder_not_flagged_as_unknown_entity():
    # chinese_unnamed 占位不应被当成"应有名却没提取到"
    proj = {"category": "光伏", "capacity_mw": "50", "involvement_type": "Unknown",
            "entities": [{"name": "未具名中企", "is_unnamed_placeholder": True}]}
    assert "entities" not in signals.suspicious_fields(proj)

def test_empty_entities_flagged():
    proj = {"category": "光伏", "capacity_mw": "50", "involvement_type": "EPC总承包", "entities": []}
    assert "entities" in signals.suspicious_fields(proj)
