"""orchestrator S1 门控测试：S1 非 valid 时短路返回、不进后续；S1 valid 时走 S2→S4 路径。"""
import pytest
from s01b_project_extractor.stages.orchestrator import extract_staged


class _ScriptedLLM:
    """按 stage 返回 payload；每个 stage 的值可为单个 dict 或 dict 列表（按调用次序消费）。记录 stage 调用顺序。"""
    def __init__(self, by_stage):
        self.by_stage = {k: (list(v) if isinstance(v, list) else [v]) for k, v in by_stage.items()}
        self.stages = []

    def complete(self, prompt, *, stage, json_mode=True):
        self.stages.append(stage)
        seq = self.by_stage[stage]
        return seq.pop(0) if len(seq) > 1 else seq[0]


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
    assert out["analysis"]["reasoning"] == "无中企"


def test_s1_valid_continues_to_s2_s4():
    s2_payload = {"projects": [{"project_summary": "s", "common_info": {"Project Name": "测试项目", "Category": "光伏", "Project Stage": "在建"}}]}
    s4_payload = {"chinese_entities": [{"Implementing Chinese Entity": "中国能建", "Type of involvement of Chinese Entities": "EPC总承包"}]}
    s3_payload = {"Financing Model": "信贷融资"}
    llm = _ScriptedLLM({"s1": {"status": "valid_project", "reasoning": "ok", "project_subject": "P"},
                        "s2": s2_payload,
                        "s4": s4_payload,
                        "s3": s3_payload})
    out = extract_staged("t", "c", "2025-01-01", llm=llm)
    assert out["status"] == "valid_project"
    assert llm.stages == ["s1", "s2", "s4", "s3"]
    assert len(out["projects"]) == 1


def test_s1_retries_on_bad_status_then_succeeds():
    """S1 首次返回非法 status → 重试 → 第二次合法被采用。"""
    llm = _ScriptedLLM({"s1": [{"status": "GARBAGE"}, {"status": "irrelevant", "reasoning": "二次合法"}]})
    out = extract_staged("t", "c", "2025-01-01", llm=llm)
    assert out["status"] == "irrelevant"
    assert llm.stages == ["s1", "s1"]


def test_s1_all_attempts_invalid_raises():
    """S1 两次都非法 → 抛 RuntimeError（不静默丢弃）。"""
    llm = _ScriptedLLM({"s1": [{"status": "X"}, {"status": "Y"}]})
    with pytest.raises(RuntimeError, match="非法 status"):
        extract_staged("t", "c", "2025-01-01", llm=llm)
    assert llm.stages == ["s1", "s1"]
