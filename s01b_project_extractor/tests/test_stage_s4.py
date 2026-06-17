"""S4 阶段测试：involvement 校验。"""
from s01b_project_extractor.stages.s4_entity import validate_s4


def test_validate_s4_flags_bad_involvement():
    errs = validate_s4([{"Implementing Chinese Entity": "中国能建", "Type of involvement of Chinese Entities": "乱填"}])
    assert any("参与方式不合法" in m for m in errs)


def test_validate_s4_flags_empty_entity():
    errs = validate_s4([{"Implementing Chinese Entity": "", "Type of involvement of Chinese Entities": "EPC总承包"}])
    assert any("为空" in m for m in errs)


def test_validate_s4_accepts_integrated_provider():
    errs = validate_s4([{"Implementing Chinese Entity": "X", "Type of involvement of Chinese Entities": "一体化方案提供商"}])
    assert errs == []
