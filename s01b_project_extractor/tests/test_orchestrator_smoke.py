"""orchestrator 冒烟测试：mock LLM，确认 valid 路径结构正确透传。

显式控制 S01B_MERGE_S34 开关使测试 hermetic（不依赖 .env 环境）：
- 关闭时走原 S3/S4 分开路径（s1→s2→s4→s3→s5）；
- 开启时（生产默认）走合并 s34 路径（s1→s2→s34→s5）。"""
from s01b_project_extractor.stages.orchestrator import extract_staged


class _FakeLLM:
    def __init__(self, by_stage):
        self.by_stage = by_stage
        self.stages = []

    def complete(self, prompt, *, stage, json_mode=True):
        self.stages.append(stage)
        return self.by_stage[stage]


def test_extract_staged_valid_passthrough_structure(monkeypatch):
    """S34 合并关闭时：valid 路径经 S1→S2→S4→S3→S5 分开调用，结构正确透传。"""
    monkeypatch.delenv("S01B_MERGE_S34", raising=False)
    s2_payload = {"projects": [{"project_summary": "s", "common_info": {"Project Name": "测试", "Category": "光伏", "Project Stage": "在建"},
                                "chinese_entities": [{"Implementing Chinese Entity": "中国能建"}]}]}
    s4_payload = {"chinese_entities": [{"Implementing Chinese Entity": "中国能建", "Type of involvement of Chinese Entities": "EPC总承包"}]}
    s3_payload = {"Financing Model": "信贷融资"}
    s5_payload = {"project_summary": "测试摘要", "corrections": []}
    llm = _FakeLLM({"s1": {"status": "valid_project", "reasoning": "r", "project_subject": "p"},
                    "s2": s2_payload,
                    "s4": s4_payload,
                    "s3": s3_payload,
                    "s5": s5_payload})
    out = extract_staged("t", "c", "2025-01-01", llm=llm)
    assert out["status"] == "valid_project"
    assert out["projects"][0]["common_info"]["Project Name"] == "测试"
    assert llm.stages == ["s1", "s2", "s4", "s3", "s5"]


def test_extract_staged_valid_merge_s34_path(monkeypatch):
    """S34 合并开启时（生产默认）：valid 路径经 S1→S2→S34→S5（跳过单独 s4/s3），实体+金融由 s34 一次出。"""
    monkeypatch.setenv("S01B_MERGE_S34", "1")
    s2_payload = {"projects": [{"common_info": {"Project Name": "测试", "Category": "光伏", "Project Stage": "在建"}}]}
    # s34 原始 LLM 输出：实体 + 金融字段同级（run_s34 再切分/归一）
    s34_payload = {"chinese_entities": [{"Implementing Chinese Entity": "中国能建", "Type of involvement of Chinese Entities": "EPC总承包"}],
                   "Financing Model": "信贷融资"}
    s5_payload = {"project_summary": "测试摘要", "corrections": []}
    llm = _FakeLLM({"s1": {"status": "valid_project", "reasoning": "r", "project_subject": "p"},
                    "s2": s2_payload,
                    "s34": s34_payload,
                    "s5": s5_payload})
    out = extract_staged("t", "c", "2025-01-01", llm=llm)
    assert out["status"] == "valid_project"
    assert llm.stages == ["s1", "s2", "s34", "s5"]
    # s34 的金融字段已并入 common_info；实体经母子公司归一
    assert out["projects"][0]["common_info"]["Financing Model"] == "信贷融资"
    assert out["projects"][0]["chinese_entities"][0]["Implementing Chinese Entity"] == "中国能源建设集团有限公司"
