"""S4 阶段测试：involvement 校验 + LLM subsidiary 不变量守护。"""
import pytest
from s01b_project_extractor.stages.s4_entity import validate_s4, run_s4


class _StubLLM4:
    """返回固定 S4 输出的桩 LLM。"""
    def __init__(self, payload): self._p = payload
    def complete(self, prompt, *, stage, json_mode=True):
        assert stage == "s4"
        return self._p


def test_validate_s4_flags_bad_involvement():
    errs = validate_s4([{"Implementing Chinese Entity": "中国能建", "Type of involvement of Chinese Entities": "乱填"}])
    assert any("参与方式不合法" in m for m in errs)


def test_validate_s4_flags_empty_entity():
    errs = validate_s4([{"Implementing Chinese Entity": "", "Type of involvement of Chinese Entities": "EPC总承包"}])
    assert any("为空" in m for m in errs)


def test_validate_s4_accepts_integrated_provider():
    errs = validate_s4([{"Implementing Chinese Entity": "X", "Type of involvement of Chinese Entities": "一体化方案提供商"}])
    assert errs == []


# ── #1 LLM subsidiary 不变量守护（stage 层无条件丢弃 LLM Subsidiary Name）──────


def test_s4_llm_subsidiary_discarded_when_parser_sub_nonempty():
    """LLM 乱填 Subsidiary Name 时，stage 层无条件丢弃，subsidiary 只取 parser 输出（路径1子公司）。"""
    # 路径1：中国葛洲坝集团有限公司 是 中国能源建设集团有限公司 的子公司
    # parser 输出：(中国能源建设集团有限公司, 中国葛洲坝集团有限公司)
    # LLM 乱填了 Subsidiary Name = "幻觉子公司名称"，stage 层应无条件丢弃
    llm = _StubLLM4({"chinese_entities": [{
        "Implementing Chinese Entity": "中国葛洲坝集团有限公司",
        "Type of involvement of Chinese Entities": "EPC总承包",
        "Subsidiary Name": "幻觉子公司名称",  # LLM 乱填
    }]})
    ents = run_s4("title", "content", "项目", "水电", llm=llm)
    e = ents[0]
    # implementing 应被 normalize_entity 替换为集团 canonical
    assert e["Implementing Chinese Entity"] == "中国能源建设集团有限公司"
    # subsidiary 应是 parser 输出（verbatim），而非 LLM 乱填的值
    assert e["Subsidiary Name"] == "中国葛洲坝集团有限公司", (
        f"subsidiary 应来自 parser 输出 '中国葛洲坝集团有限公司'，而非 LLM 乱填 '幻觉子公司名称'，得到: {e.get('Subsidiary Name')!r}"
    )


def test_s4_llm_subsidiary_discarded_when_parser_sub_empty():
    """LLM 乱填 Subsidiary Name，但 parser sub 为空（路径2a/3）时，最终记录 subsidiary 必须为空。"""
    # 路径2a：集团自身精确命中 → parser 输出 sub=""
    # LLM 乱填 Subsidiary Name 但 parser 说 sub 应为空，stage 层应写空
    llm = _StubLLM4({"chinese_entities": [{
        "Implementing Chinese Entity": "中国能源建设集团有限公司",
        "Type of involvement of Chinese Entities": "EPC总承包",
        "Subsidiary Name": "LLM乱填的子公司",  # LLM 乱填
    }]})
    ents = run_s4("title", "content", "项目", "光伏", llm=llm)
    e = ents[0]
    assert e["Implementing Chinese Entity"] == "中国能源建设集团有限公司"
    # parser sub="" → 最终 subsidiary 必须为空，LLM 乱填被无条件丢弃
    assert e.get("Subsidiary Name", "") == "", (
        f"parser sub 为空时，LLM 乱填应被丢弃，得到: {e.get('Subsidiary Name')!r}"
    )
