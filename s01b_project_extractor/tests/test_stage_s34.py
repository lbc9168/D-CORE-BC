"""S34 合并阶段测试：实体归一 + 金融切分/数值规范 + validate。"""
from s01b_project_extractor.stages.s34_entity_finance import run_s34, validate_s34


class _StubLLM:
    """返回固定合并输出的桩 LLM，断言 stage=s34。"""
    def __init__(self, payload): self._p = payload
    def complete(self, prompt, *, stage, json_mode=True):
        assert stage == "s34"
        return self._p


def test_run_s34_splits_entities_and_finance_and_parses_number():
    llm = _StubLLM({
        "chinese_entities": [{"Implementing Chinese Entity": "中国能建",
                              "Type of involvement of Chinese Entities": "EPC总承包"}],
        "Total Project Investment (Million USD)": "1,200",
        "Financing Model": "信贷融资",
        "Additional Remarks": "",
    })
    out = run_s34("t", "c", "项目", "光伏", llm=llm)
    assert out["finance"]["Total Project Investment (Million USD)"] == 1200.0
    assert out["finance"]["Financing Model"] == "信贷融资"
    assert out["chinese_entities"][0]["Type of involvement of Chinese Entities"] == "EPC总承包"
    # 实体已归一（中国能建 → 顶级集团规范名）
    assert out["chinese_entities"][0]["Implementing Chinese Entity"] == "中国能源建设集团有限公司"


def test_run_s34_non_dict_returns_empty():
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
