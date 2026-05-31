"""chinese_unnamed：S1 判定后 orchestrator 应走 S2/S3 但 S4 产占位实体，status 透传。"""
from s01b_project_extractor.stages import orchestrator


class _MockLLM:
    """按 stage 返回固定 JSON：S1=chinese_unnamed；S2 一个项目；S3 空；S4 占位；S5 无纠正。"""

    def complete(self, prompt, *, stage, json_mode=True):
        if stage == "s1":
            return {"status": "chinese_unnamed_participation", "project_subject": "某光伏项目",
                    "is_power_generation_plant": True, "reasoning": "明确中企承建未点名"}
        if stage == "s2":
            return {"projects": [{"common_info": {"Project Name": "某国光伏项目", "Category": "光伏",
                                                   "Project Stage": "在建"},
                                   "project_summary": ""}]}
        if stage == "s4":
            return []  # S4 对 unnamed 不产具体实体
        if stage == "s3":
            return {}
        if stage == "s5":
            return {"corrections": []}
        return {}


def test_chinese_unnamed_routes_to_projects_with_placeholder():
    r = orchestrator.extract_staged("标题", "正文：中国一家企业中标承建", "2024-01-01", llm=_MockLLM())
    assert r["status"] == "chinese_unnamed"
    assert len(r["projects"]) == 1
    ents = r["projects"][0]["chinese_entities"]
    assert len(ents) == 1
    assert ents[0]["Implementing Chinese Entity"] == "未具名中企"
    assert ents[0].get("is_unnamed_placeholder") is True
