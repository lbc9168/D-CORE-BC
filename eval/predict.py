"""flat_prediction：把 s01b 原始提取结果归一成文章级 {status, projects[]}，每项目含字段 + entities[{name,involvement_type}]。"""

_STATUS_MAP = {"valid_project": "valid", "no_chinese_participation": "no_chinese", "irrelevant": "irrelevant"}

def _flat_project(proj: dict) -> dict:
    """单个项目 → 扁平 dict（字段 + entities 名字/角色对）。"""
    common = proj.get("common_info", {}) or {}
    entities = []
    for e in proj.get("chinese_entities", []) or []:
        name = e.get("Implementing Chinese Entity", "")
        if name:
            entities.append({"name": name, "involvement_type": e.get("Type of involvement of Chinese Entities", "")})
    return {
        "project_name": common.get("Project Name", ""),
        "category": common.get("Category", ""),
        "capacity_mw": common.get("Capacity (MW)", ""),
        "project_stage": common.get("Project Stage", ""),
        "location": common.get("Location(State)", ""),
        "total_investment_usd": common.get("Total Project Investment (Million USD)", ""),
        "financing_model": common.get("Financing Model", ""),
        "entities": entities,
    }

def flat_prediction(result: dict) -> dict:
    """原始结果 → {status(归一), projects:[扁平项目,...]}；非 valid 时 projects=[]。"""
    raw_status = result.get("status")
    status = _STATUS_MAP.get(raw_status, raw_status if raw_status is not None else "")
    if status != "valid":
        return {"status": status, "projects": []}
    projects = [_flat_project(p) for p in (result.get("projects") or [])]
    return {"status": status, "projects": projects}
