"""S34 合并阶段测试：实体归一 + 金融切分/数值规范 + validate。

旧断言重写清单：
  test_run_s34_splits_entities_and_finance_and_parses_number
    旧：LLM 返回非正式简称 "中国能建"，断言归一到 "中国能源建设集团有限公司"
        （旧实现：包含匹配可能命中；新实现：v2 索引无该简称 → 透传）
    新：LLM 返回 v2 hierarchy 中精确 canonical 名 "中国能源建设集团有限公司"，断言路径2a
"""
import pytest
from s01b_project_extractor.stages.s34_entity_finance import run_s34, validate_s34
from s01b_project_extractor.stages.entity_response import EntityResponseError


class _StubLLM:
    """返回固定合并输出的桩 LLM，断言 stage=s34。"""
    def __init__(self, payload): self._p = payload
    def complete(self, prompt, *, stage, json_mode=True):
        assert stage == "s34"
        return self._p


def test_run_s34_splits_entities_and_finance_and_parses_number():
    # 旧断言：LLM 返回 "中国能建" 并断言归一 → 新实现中非正式简称不在 v2 索引会透传
    # 新断言：LLM 返回 canonical 全称，验证路径2a（集团精确命中 → sub=""）
    content = "中国能源建设集团有限公司签署纳沃伊光伏项目EPC合同，项目获得银行贷款。"
    llm = _StubLLM({
        "chinese_entities": [{"Implementing Chinese Entity": "中国能源建设集团有限公司",
                              "Type of involvement of Chinese Entities": "EPC总承包",
                              "Participation Evidence": [{
                                  "role": "EPC总承包", "quote": content,
                                  "project_anchor": "纳沃伊光伏项目",
                              }]}],
        "Total Project Investment (Million USD)": "1,200",
        "Financing Model": "信贷融资",
        "Financing Evidence": [{
            "tool": "credit", "quote": content, "project_anchor": "纳沃伊光伏项目",
        }],
        "Additional Remarks": "",
    })
    out = run_s34("t", content, "纳沃伊光伏项目", "光伏", llm=llm)
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


def test_run_s34_non_dict_is_visible_parse_failure():
    """Malformed output must never masquerade as an explicit negative entity list."""
    with pytest.raises(EntityResponseError):
        run_s34("t", "c", "项目", "光伏", llm=_StubLLM(["not a dict"]))


def test_validate_s34_flags_bad_involvement_and_empty_entity():
    errs = validate_s34({"chinese_entities": [
        {"Implementing Chinese Entity": "X", "Type of involvement of Chinese Entities": "乱填"},
        {"Implementing Chinese Entity": "", "Type of involvement of Chinese Entities": "EPC总承包"},
    ]})
    assert any("参与方式不合法" in m for m in errs)
    assert any("为空" in m for m in errs)


def test_validate_s34_accepts_clean():
    assert validate_s34({"chinese_entities": [
        {"Implementing Chinese Entity": "中国能建", "Type of involvement of Chinese Entities": "EPC总承包",
         "_participation_evidence_status": "verified"}]}) == []


# ── #1 LLM subsidiary 不变量守护（stage 层无条件丢弃 LLM Subsidiary Name）──────


def test_s34_llm_subsidiary_discarded_when_parser_sub_nonempty():
    """S34：LLM 乱填 Subsidiary Name，stage 层无条件丢弃，subsidiary 只取 parser 输出（路径1）。"""
    llm = _StubLLM({
        "chinese_entities": [{
            "Implementing Chinese Entity": "中国葛洲坝集团有限公司",
            "Type of involvement of Chinese Entities": "EPC总承包",
            "Subsidiary Name": "LLM乱填的子公司",  # LLM 乱填
        }],
        "Financing Model": "Unknown",
    })
    out = run_s34("t", "c", "项目", "水电", llm=llm)
    e = out["chinese_entities"][0]
    assert e["Implementing Chinese Entity"] == "中国能源建设集团有限公司"
    # subsidiary 应来自 parser（verbatim），LLM 乱填被无条件丢弃
    assert e["Subsidiary Name"] == "中国葛洲坝集团有限公司", (
        f"subsidiary 应来自 parser '中国葛洲坝集团有限公司'，非 LLM 乱填，得到: {e.get('Subsidiary Name')!r}"
    )


def test_s34_llm_subsidiary_discarded_when_parser_sub_empty():
    """S34：parser sub 为空（路径2a）时，LLM 乱填 Subsidiary Name 应被丢弃，最终记录为空。"""
    llm = _StubLLM({
        "chinese_entities": [{
            "Implementing Chinese Entity": "中国能源建设集团有限公司",
            "Type of involvement of Chinese Entities": "EPC总承包",
            "Subsidiary Name": "乱填子公司X",  # LLM 乱填
        }],
        "Financing Model": "Unknown",
    })
    out = run_s34("t", "c", "项目", "光伏", llm=llm)
    e = out["chinese_entities"][0]
    assert e["Implementing Chinese Entity"] == "中国能源建设集团有限公司"
    # parser 路径2a sub="" → stage 层必须写空，不能残留 LLM 乱填
    assert e.get("Subsidiary Name", "") == "", (
        f"parser sub 为空时 LLM 乱填应被丢弃，得到: {e.get('Subsidiary Name')!r}"
    )
