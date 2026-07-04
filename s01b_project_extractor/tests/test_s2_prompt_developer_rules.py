"""S2 prompt 业主判定规则锚点：证据白名单/负面清单/留空/JV拆填/evidence 字段必须在 prompt 中。"""
from s01b_project_extractor.prompts.s2_basic import build_s2_prompt


def test_s2_prompt_contains_developer_rules():
    prompt = build_s2_prompt("标题", "正文", "2026-07-04")
    # 规则小节与核心原则
    assert "业主判定" in prompt
    assert "留空" in prompt                      # 无证据→留空（宁缺毋滥）
    # 负面清单必须点名抽样证实的两个新错误模式
    assert "购电方" in prompt
    assert "融资" in prompt
    # JV 拆填合资方
    assert "合资" in prompt
    # 证据字段进入输出 schema
    assert '"Project Developer Evidence"' in prompt
