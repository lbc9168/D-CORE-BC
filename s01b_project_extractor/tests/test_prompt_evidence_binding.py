"""独立与合并模式输出相同的语义判断契约，避免关闭S34后退回旧门槛。"""

import json

from s01b_project_extractor.prompts.s3_finance import build_s3_prompt
from s01b_project_extractor.prompts.s4_entity import build_s4_prompt
from s01b_project_extractor.prompts.s34_entity_finance import build_s34_prompt


def _output_contract(prompt):
    return json.loads(prompt[prompt.index("\n{", prompt.index("### 输出")):])


def test_finance_prompts_preserve_amount_and_semantic_scope_contract():
    """金额与汇率依据单独留存；项目工具归属无需同句锚点。"""
    prompts = [
        build_s3_prompt("标题", "正文", "目标项目"),
        build_s34_prompt("标题", "正文", "目标项目", "光伏", []),
    ]
    for prompt in prompts:
        contract = _output_contract(prompt)
        assert set(contract["Financing Evidence"][0]) == {
            "tool", "quote", "project_anchor", "rationale", "scope",
        }
        assert set(contract["Amount Evidence"][0]) == {
            "field", "amount_scope", "currency", "original_amount",
            "usd_per_currency_unit", "conversion_basis", "evidence_quotes", "rationale",
        }
        for field in ("Total Project Investment", "Contract Amount", "Financing Amount"):
            assert field + " Raw" in contract
            assert field + " (Million USD)" in contract
        assert "组合层面援助仍填 Unknown" in prompt
        assert "银团属于 credit" in prompt


def test_entity_prompts_separate_participation_identity_and_role():
    """参与、中企依据与角色判断独立；保留旧证据键供既有读者审计。"""
    context = {"source_name": "地区甲", "aliases": ["甲光伏项目"], "context": "本次签约", "is_current_event": True}
    prompts = [
        build_s4_prompt("标题", "正文", "目标项目", "光伏", [], context),
        build_s34_prompt("标题", "正文", "目标项目", "光伏", [], context),
    ]
    for prompt in prompts:
        entity = _output_contract(prompt)["chinese_entities"][0]
        assert set(entity["Participation Assessment"]) == {
            "participates", "is_chinese", "identity_basis", "rationale",
            "evidence_quotes", "role", "role_basis",
        }
        assert "Participation Evidence" in entity
        assert json.dumps(context, ensure_ascii=False) in prompt
        assert "同一引文中同时出现" not in prompt
        assert "目标项目名称共享专名" not in prompt
