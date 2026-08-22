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
    errs = validate_s4([{
        "Implementing Chinese Entity": "X",
        "Type of involvement of Chinese Entities": "一体化方案提供商",
        "_participation_evidence_status": "verified",
    }])
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


def test_joint_development_does_not_imply_equity_role():
    """联合开发而无持股证据时，股权投资方必须降级 Unknown。"""
    llm = _StubLLM4({"chinese_entities": [{
        "Implementing Chinese Entity": "中国能源建设集团有限公司",
        "Type of involvement of Chinese Entities": "股权投资方",
    }]})
    entities = run_s4("title", "双方将联合开发该项目。", "项目", "光伏", llm=llm)
    assert entities[0]["Type of involvement of Chinese Entities"] == "Unknown"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("中国能建签署该项目EPC总承包合同。", "EPC总承包"),
        ("中国能建认购项目公司股份。", "股权投资方"),
        ("中国能建认购项目公司股份并签署EPC总承包合同。", "投融资+EPC"),
        ("中国能建与合作伙伴共同推进项目。", "Unknown"),
    ],
)
def test_integrated_role_is_reduced_to_supported_components(content, expected):
    """组合角色必须按股权和 EPC 的实际证据保留已证实部分。"""
    evidence = []
    if "认购" in content:
        evidence.append({"role": "股权投资方", "quote": content, "project_anchor": "项目公司"})
    if "EPC" in content:
        evidence.append({"role": "EPC总承包", "quote": content, "project_anchor": "项目公司"})
    bound_content = content.replace("该项目", "纳沃伊项目").replace("项目公司", "纳沃伊项目公司")
    anchor = "纳沃伊项目公司" if "项目公司" in bound_content else "纳沃伊项目"
    for item in evidence:
        item["quote"] = bound_content
        item["project_anchor"] = anchor
    llm = _StubLLM4({"chinese_entities": [{
        "Implementing Chinese Entity": "中国能建",
        "Type of involvement of Chinese Entities": "投融资+EPC",
        "Participation Evidence": evidence,
    }]})
    entities = run_s4("title", bound_content, "纳沃伊项目公司", "光伏", llm=llm)
    assert entities[0]["Type of involvement of Chinese Entities"] == expected


def test_epc_of_one_company_is_not_assigned_to_another_company():
    """整篇文章中其他企业的 EPC 证据不得补足目标企业的组合角色。"""
    content = "中国电建签署纳沃伊光伏项目EPC合同；中国能建认购纳沃伊光伏项目公司股份。"
    llm = _StubLLM4({"chinese_entities": [{
        "Implementing Chinese Entity": "中国能建",
        "Type of involvement of Chinese Entities": "投融资+EPC",
        "Participation Evidence": [{
            "role": "股权投资方",
            "quote": "中国能建认购纳沃伊光伏项目公司股份。",
            "project_anchor": "纳沃伊光伏项目公司",
        }],
    }]})
    entities = run_s4("title", content, "纳沃伊光伏项目", "光伏", llm=llm)
    assert entities[0]["Type of involvement of Chinese Entities"] == "股权投资方"


def test_entity_role_evidence_for_other_project_is_rejected():
    """企业在另一项目的 EPC 证据不能绑定到当前目标项目。"""
    content = "中国能建承建布哈拉风电项目；纳沃伊光伏项目由其他企业建设。"
    llm = _StubLLM4({"chinese_entities": [{
        "Implementing Chinese Entity": "中国能建",
        "Type of involvement of Chinese Entities": "EPC总承包",
        "Participation Evidence": [{
            "role": "EPC总承包",
            "quote": "中国能建承建布哈拉风电项目",
            "project_anchor": "布哈拉风电项目",
        }],
    }]})
    entities = run_s4("title", content, "纳沃伊光伏项目", "光伏", llm=llm)
    assert entities[0]["Type of involvement of Chinese Entities"] == "Unknown"
    assert entities[0]["_participation_evidence_status"] == "missing_or_ambiguous"


def test_other_entity_role_in_adjacent_clause_is_not_reused():
    """同一引文相邻分句中另一企业的 EPC 动作不能绑定给当前企业。"""
    content = "中国电建签署纳沃伊光伏项目EPC合同；中国能建参与纳沃伊光伏项目合作。"
    llm = _StubLLM4({"chinese_entities": [{
        "Implementing Chinese Entity": "中国能建",
        "Type of involvement of Chinese Entities": "EPC总承包",
        "Participation Evidence": [{
            "role": "EPC总承包",
            "quote": content,
            "project_anchor": "纳沃伊光伏项目",
        }],
    }]})
    entities = run_s4("title", content, "纳沃伊光伏项目", "光伏", llm=llm)
    assert entities[0]["Type of involvement of Chinese Entities"] == "Unknown"


def test_explicit_project_participation_can_publish_with_unknown_role():
    """企业与目标项目关系明确但具体角色不明时保留关系并将角色写 Unknown。"""
    content = "中国能建参与开发纳沃伊光伏项目。"
    llm = _StubLLM4({"chinese_entities": [{
        "Implementing Chinese Entity": "中国能建",
        "Type of involvement of Chinese Entities": "Unknown",
        "Participation Evidence": [{
            "role": "Unknown",
            "quote": content,
            "project_anchor": "纳沃伊光伏项目",
        }],
    }]})
    entities = run_s4("title", content, "纳沃伊光伏项目", "光伏", llm=llm)
    assert entities[0]["Type of involvement of Chinese Entities"] == "Unknown"
    assert entities[0]["_participation_evidence_status"] == "verified"
