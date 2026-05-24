"""把 s01b 提取器原始 result 映射为评测用扁平记录（P0：多项目只取主项目）。"""

# 提取器 status → 评测规范 status 的映射
_STATUS_MAP = {
    "valid_project": "valid",
    "no_chinese_participation": "no_chinese",
    "irrelevant": "irrelevant",
}


def flat_prediction(result: dict) -> dict:
    """把原始 result dict 转成扁平记录（status + 主项目字段 + 中方实体）；缺键用默认值兜底。"""
    raw_status = result.get("status")
    mapped = _STATUS_MAP.get(raw_status, raw_status if raw_status is not None else "")

    projects = result.get("projects") or []
    if mapped != "valid" or not projects:
        return {"status": mapped, "entities": []}

    proj = projects[0]
    common = proj.get("common_info", {})
    entities = proj.get("chinese_entities", [])
    involvement = (
        entities[0].get("Type of involvement of Chinese Entities", "") if entities else ""
    )
    return {
        "status": mapped,
        "category": common.get("Category", ""),
        "capacity_mw": common.get("Capacity (MW)", ""),
        "project_stage": common.get("Project Stage", ""),
        "location": common.get("Location(State)", ""),
        "total_investment_usd": common.get("Total Project Investment (Million USD)", ""),
        "financing_model": common.get("Financing Model", ""),
        "involvement_type": involvement,
        "entities": [
            e.get("Implementing Chinese Entity", "")
            for e in entities
            if e.get("Implementing Chinese Entity")
        ],
    }
