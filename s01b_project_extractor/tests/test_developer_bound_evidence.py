"""Developer 只能从目标项目的原文取得，企业身份与参与标签均不能代替。"""
import pytest
from s01b_project_extractor.guardrails.developer_check import clean_developer


def _clean(names, quote, project="纳沃伊光伏项目", *, content=None, entities=None):
    return clean_developer(names, entities or [], "光伏", quote, title="", content=content if content is not None else quote, project_name=project)[0]


@pytest.mark.parametrize(("names", "quote", "expected"), [
    ("ACWA Power;中国电建", "纳沃伊光伏项目由ACWA Power投资建设，中国电建承建该项目。", "ACWA Power"),
    ("中国电建", "中国电建为纳沃伊光伏项目供应设备。", ""),
    ("甲公司", "甲公司是布哈拉光伏项目开发商，甲公司为纳沃伊光伏项目供货。", ""),
    ("甲公司", "甲公司开发布哈拉光伏项目并为纳沃伊光伏项目供货。", ""),
    ("甲集团;甲集团乙公司", "纳沃伊光伏项目由甲集团乙公司投资建设。", "甲集团乙公司"),
    ("甲公司;乙公司", "甲公司是纳沃伊光伏项目的开发商，乙公司提供施工服务。", "甲公司"),
    ("甲公司", "甲公司是纳沃伊光伏项目合格开发商入围名单中的一家。", ""),
    ("甲公司", "甲公司中标纳沃伊光伏项目EPC合同。", ""),
    ("新辰能源", "纳沃伊光伏项目由新辰能源投资建设。", "新辰能源"),
    ("甲公司;乙公司", "纳沃伊光伏项目由甲公司与乙公司共同投资建设。", "甲公司;乙公司"),
    ("甲公司", "纳沃伊光伏项目正式开工。甲公司投资建设该项目。", "甲公司"),
    ("甲公司", "甲公司不是纳沃伊光伏项目的开发商。", ""),
])
def test_project_and_subject_specific_developer(names, quote, expected):
    assert _clean(names, quote) == expected


def test_nonempty_model_quote_is_not_original_evidence():
    assert _clean("甲公司", "纳沃伊光伏项目由甲公司投资建设。", content="甲公司中标纳沃伊光伏项目EPC合同。") == ""


def test_positive_developer_evidence_not_overruled_by_another_model_role():
    quote = "纳沃伊光伏项目由甲公司投资建设。"
    entities = [{"Implementing Chinese Entity": "甲公司", "Type of involvement of Chinese Entities": "设备供应商"}]
    assert _clean("甲公司", quote, entities=entities) == "甲公司"


def test_empty_developer_is_never_filled_from_entities():
    assert _clean("", "纳沃伊光伏项目由甲公司投资建设。", entities=[{"Implementing Chinese Entity": "甲公司"}]) == ""


@pytest.mark.parametrize("project", ["纳沃伊100MW光伏项目", "100MW光伏项目"])
def test_capacity_only_developer_anchor_is_not_a_project_identity(project):
    assert _clean("甲公司", "100MW光伏项目由甲公司投资建设。", project=project) == ""
