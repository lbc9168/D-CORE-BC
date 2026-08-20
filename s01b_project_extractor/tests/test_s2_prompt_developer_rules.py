"""S2 prompt 业主判定规则锚点：证据白名单/负面清单/留空/JV拆填/evidence 字段必须在 prompt 中。"""
from s01b_project_extractor.prompts.s2_basic import build_s2_prompt
from s01b_project_extractor.tools.project_extractor_tool import _build_prompt


def test_s2_prompt_contains_developer_rules():
    prompt = build_s2_prompt("标题", "正文", "2026-07-04")
    # 规则小节与核心原则
    assert "业主判定" in prompt
    assert "留空" in prompt                      # 无证据→留空（宁缺毋滥）
    # 负面清单必须点名抽样证实的两个新错误模式
    assert "购电方" in prompt
    assert "融资" in prompt
    assert "合格投标方" in prompt
    assert "入围名单" in prompt
    assert "短名单" in prompt
    assert "资格预审" in prompt
    # JV 拆填合资方
    assert "合资" in prompt
    # 证据字段进入输出 schema
    assert '"Project Developer Evidence"' in prompt


def test_legacy_prompt_contains_same_candidate_exclusion():
    """旧单体 prompt 入口与正式 S2 保持同一候选名单负面规则。"""
    prompt = _build_prompt("标题", "正文", "2026-08-19")
    assert "合格投标方" in prompt
    assert "入围名单" in prompt
    assert "短名单" in prompt
    assert "资格预审" in prompt
    assert '"Project Developer Evidence"' in prompt
