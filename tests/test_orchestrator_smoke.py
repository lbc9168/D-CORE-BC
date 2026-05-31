"""orchestrator 冒烟测试：mock LLM，确认 valid 路径经 S1→S2→S4 结构正确透传。"""
from s01b_project_extractor.stages.orchestrator import extract_staged


class _FakeLLM:
    def __init__(self, by_stage):
        self.by_stage = by_stage
        self.stages = []

    def complete(self, prompt, *, stage, json_mode=True):
        self.stages.append(stage)
        return self.by_stage[stage]


def test_extract_staged_valid_passthrough_structure():
    s2_payload = {"projects": [{"project_summary": "s", "common_info": {"Project Name": "测试", "Category": "光伏", "Project Stage": "在建"},
                                "chinese_entities": [{"Implementing Chinese Entity": "中国能建"}]}]}
    s4_payload = {"chinese_entities": [{"Implementing Chinese Entity": "中国能建", "Type of involvement of Chinese Entities": "EPC总承包"}]}
    s3_payload = {"Financing Model": "信贷融资"}
    llm = _FakeLLM({"s1": {"status": "valid_project", "reasoning": "r", "project_subject": "p"},
                    "s2": s2_payload,
                    "s4": s4_payload,
                    "s3": s3_payload})
    out = extract_staged("t", "c", "2025-01-01", llm=llm)
    assert out["status"] == "valid_project"
    assert out["projects"][0]["common_info"]["Project Name"] == "测试"
    assert llm.stages == ["s1", "s2", "s4", "s3"]
