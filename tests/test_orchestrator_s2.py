"""orchestrator S2 路径测试：valid 时走 S2→S4，不再调 legacy_single。"""
from s01b_project_extractor.stages.orchestrator import extract_staged


class _ScriptedLLM:
    def __init__(self, by_stage): self.by_stage = by_stage; self.stages = []
    def complete(self, prompt, *, stage, json_mode=True):
        self.stages.append(stage); return self.by_stage[stage]


def test_valid_uses_s2_then_s4_no_legacy():
    llm = _ScriptedLLM({
        "s1": {"status": "valid_project", "reasoning": "ok"},
        "s2": {"projects": [{"common_info": {"Project Name": "肯尼亚光伏项目", "Category": "光伏", "Project Stage": "在建"}}]},
        "s4": {"chinese_entities": [{"Implementing Chinese Entity": "中国能建", "Type of involvement of Chinese Entities": "EPC总承包"}]},
        "s3": {"Financing Model": "信贷融资"},
    })
    out = extract_staged("t", "c", "2025-01-01", llm=llm)
    assert out["status"] == "valid_project"
    assert "legacy_single" not in llm.stages
    assert llm.stages[:2] == ["s1", "s2"] and "s4" in llm.stages and "s3" in llm.stages
    # 契约不变量：analysis 必含 reasoning 与 project_subject（eval/predict.py 依赖）
    assert "reasoning" in out["analysis"] and "project_subject" in out["analysis"]
    assert out["projects"][0]["common_info"]["Category"] == "光伏"
    assert out["projects"][0]["chinese_entities"][0]["Implementing Chinese Entity"] == "中国能源建设集团有限公司"
    # S3 金融字段已合并进 common_info
    assert out["projects"][0]["common_info"]["Financing Model"] == "信贷融资"
