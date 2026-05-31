"""orchestrator 冒烟测试：mock LLM 返回固定 dict，确认 extract_staged 透传结构。"""
from s01b_project_extractor.stages.orchestrator import extract_staged


class _FakeLLM:
    def __init__(self, payload):
        self._payload = payload
        self.stages = []

    def complete(self, prompt, *, stage, json_mode=True):
        self.stages.append(stage)
        return self._payload


def test_extract_staged_passthrough_structure():
    payload = {"analysis": {"reasoning": "x", "project_subject": "y"},
               "status": "valid_project",
               "projects": [{"project_summary": "s", "common_info": {"Project Name": "测试"},
                             "chinese_entities": [{"Implementing Chinese Entity": "中国能建"}]}]}
    llm = _FakeLLM(payload)
    out = extract_staged("t", "c", "2025-01-01", llm=llm)
    assert out["status"] == "valid_project"
    assert out["projects"][0]["common_info"]["Project Name"] == "测试"
    assert llm.stages == ["legacy_single"]
