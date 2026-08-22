"""orchestrator S2 路径测试：valid 时走 S2→S4，不再调 legacy_single。

旧断言重写清单：
  test_valid_uses_s2_then_s4_no_legacy
    旧：S4 返回 "中国能建"，断言归一到 "中国能源建设集团有限公司"
        （旧实现：包含匹配；新实现：v2 无该简称 → 透传）
    新：S4 返回 canonical 全称 "中国能源建设集团有限公司"，断言路径2a 精确命中不变
"""
from s01b_project_extractor.stages.orchestrator import extract_staged


class _ScriptedLLM:
    def __init__(self, by_stage): self.by_stage = by_stage; self.stages = []
    def complete(self, prompt, *, stage, json_mode=True):
        self.stages.append(stage); return self.by_stage[stage]


def test_valid_uses_s2_then_s4_no_legacy(monkeypatch):
    monkeypatch.delenv("S01B_MERGE_S34", raising=False)  # 固定走 S3/S4 分开路径，使测试 hermetic
    content = "中国能源建设集团有限公司签署肯尼亚光伏项目EPC合同，肯尼亚光伏项目获得银行贷款。"
    llm = _ScriptedLLM({
        "s1": {"status": "valid_project", "reasoning": "ok"},
        "s2": {"projects": [{"common_info": {"Project Name": "肯尼亚光伏项目", "Category": "光伏", "Project Stage": "在建"}}]},
        # 使用 v2 hierarchy 中的精确 canonical 名（旧测试用非正式简称"中国能建"，v2 中不在索引，会透传）
        "s4": {"chinese_entities": [{
            "Implementing Chinese Entity": "中国能源建设集团有限公司",
            "Type of involvement of Chinese Entities": "EPC总承包",
            "Participation Evidence": [{
                "role": "EPC总承包", "quote": content, "project_anchor": "肯尼亚光伏项目",
            }],
        }]},
        "s3": {
            "Financing Model": "信贷融资",
            "Financing Evidence": [{
                "tool": "credit", "quote": content, "project_anchor": "肯尼亚光伏项目",
            }],
        },
        "s5": {"project_summary": "肯尼亚光伏项目摘要", "corrections": []},
    })
    out = extract_staged("t", content, "2025-01-01", llm=llm)
    assert out["status"] == "valid_project"
    assert "legacy_single" not in llm.stages
    assert llm.stages[:2] == ["s1", "s2"] and "s4" in llm.stages and "s3" in llm.stages and "s5" in llm.stages
    # 契约不变量：analysis 必含 reasoning 与 project_subject（eval/predict.py 依赖）
    assert "reasoning" in out["analysis"] and "project_subject" in out["analysis"]
    assert out["projects"][0]["common_info"]["Category"] == "光伏"
    # 路径2a：集团自身精确命中 → implementing 不变，sub=""
    assert out["projects"][0]["chinese_entities"][0]["Implementing Chinese Entity"] == "中国能源建设集团有限公司"
    # S3 金融字段已合并进 common_info
    assert out["projects"][0]["common_info"]["Financing Model"] == "信贷融资"
    # S5 摘要已写入
    assert out["projects"][0]["project_summary"] == "肯尼亚光伏项目摘要"
