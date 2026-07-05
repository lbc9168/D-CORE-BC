"""S34 合并阶段测试：实体归一 + 金融切分/数值规范 + validate。

旧断言重写清单：
  test_run_s34_splits_entities_and_finance_and_parses_number
    旧：LLM 返回非正式简称 "中国能建"，断言归一到 "中国能源建设集团有限公司"
        （旧实现：包含匹配可能命中；新实现：v2 索引无该简称 → 透传）
    新：LLM 返回 v2 hierarchy 中精确 canonical 名 "中国能源建设集团有限公司"，断言路径2a
"""
from s01b_project_extractor.stages.s34_entity_finance import run_s34, validate_s34


class _StubLLM:
    """返回固定合并输出的桩 LLM，断言 stage=s34。"""
    def __init__(self, payload): self._p = payload
    def complete(self, prompt, *, stage, json_mode=True):
        assert stage == "s34"
        return self._p


def test_run_s34_splits_entities_and_finance_and_parses_number():
    # 旧断言：LLM 返回 "中国能建" 并断言归一 → 新实现中非正式简称不在 v2 索引会透传
    # 新断言：LLM 返回 canonical 全称，验证路径2a（集团精确命中 → sub=""）
    llm = _StubLLM({
        "chinese_entities": [{"Implementing Chinese Entity": "中国能源建设集团有限公司",
                              "Type of involvement of Chinese Entities": "EPC总承包"}],
        "Total Project Investment (Million USD)": "1,200",
        "Financing Model": "信贷融资",
        "Additional Remarks": "",
    })
    out = run_s34("t", "c", "项目", "光伏", llm=llm)
    assert out["finance"]["Total Project Investment (Million USD)"] == 1200.0
    assert out["finance"]["Financing Model"] == "信贷融资"
    assert out["chinese_entities"][0]["Type of involvement of Chinese Entities"] == "EPC总承包"
    # 路径2a：集团自身精确命中 → implementing=canonical，sub=""
    assert out["chinese_entities"][0]["Implementing Chinese Entity"] == "中国能源建设集团有限公司"
    assert out["chinese_entities"][0].get("Subsidiary Name", "") == ""


def test_run_s34_subsidiary_entity_normalization():
    """子公司精确命中（路径1）：implementing=集团 canonical，Subsidiary Name=verbatim（不变量）。"""
    llm = _StubLLM({
        "chinese_entities": [{"Implementing Chinese Entity": "中国葛洲坝集团有限公司",
                              "Type of involvement of Chinese Entities": "EPC总承包",
                              "Subsidiary Name": ""}],
        "Financing Model": "Unknown",
    })
    out = run_s34("t", "c", "项目", "水电", llm=llm)
    e = out["chinese_entities"][0]
    assert e["Implementing Chinese Entity"] == "中国能源建设集团有限公司"
    assert e["Subsidiary Name"] == "中国葛洲坝集团有限公司"  # sub = verbatim（不变量）


def test_run_s34_non_dict_returns_empty():
    """S34 返回非 dict 时按空处理。"""
    out = run_s34("t", "c", "项目", "光伏", llm=_StubLLM(["not a dict"]))
    assert out == {"chinese_entities": [], "finance": {}}


def test_validate_s34_flags_bad_involvement_and_empty_entity():
    errs = validate_s34({"chinese_entities": [
        {"Implementing Chinese Entity": "X", "Type of involvement of Chinese Entities": "乱填"},
        {"Implementing Chinese Entity": "", "Type of involvement of Chinese Entities": "EPC总承包"},
    ]})
    assert any("参与方式不合法" in m for m in errs)
    assert any("为空" in m for m in errs)


def test_validate_s34_accepts_clean():
    assert validate_s34({"chinese_entities": [
        {"Implementing Chinese Entity": "中国能建", "Type of involvement of Chinese Entities": "EPC总承包"}]}) == []
