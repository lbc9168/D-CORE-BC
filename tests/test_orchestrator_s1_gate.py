"""orchestrator S1 门控测试：S1 非 valid 时短路返回、不进后续；S1 valid 时继续单 prompt。"""
from s01b_project_extractor.stages.orchestrator import extract_staged


class _ScriptedLLM:
    """按 stage 返回不同 payload，记录调用过的 stage 顺序。"""
    def __init__(self, by_stage): self.by_stage = by_stage; self.stages = []
    def complete(self, prompt, *, stage, json_mode=True):
        self.stages.append(stage)
        return self.by_stage[stage]


def test_s1_irrelevant_short_circuits():
    llm = _ScriptedLLM({"s1": {"status": "irrelevant", "reasoning": "油气", "project_subject": "X"}})
    out = extract_staged("t", "c", "2025-01-01", llm=llm)
    assert out["status"] == "irrelevant"
    assert out["projects"] == []
    assert llm.stages == ["s1"]
    assert out["analysis"]["reasoning"] == "油气"


def test_s1_no_chinese_short_circuits():
    llm = _ScriptedLLM({"s1": {"status": "no_chinese_participation", "reasoning": "无中企"}})
    out = extract_staged("t", "c", "2025-01-01", llm=llm)
    assert out["status"] == "no_chinese_participation"
    assert out["projects"] == []
    assert llm.stages == ["s1"]


def test_s1_valid_continues_to_legacy():
    payload_legacy = {"analysis": {"reasoning": "ok"}, "status": "valid_project",
                      "projects": [{"project_summary": "s", "common_info": {}, "chinese_entities": []}]}
    llm = _ScriptedLLM({"s1": {"status": "valid_project", "reasoning": "ok", "project_subject": "P"},
                        "legacy_single": payload_legacy})
    out = extract_staged("t", "c", "2025-01-01", llm=llm)
    assert out["status"] == "valid_project"
    assert llm.stages == ["s1", "legacy_single"]
    assert len(out["projects"]) == 1
