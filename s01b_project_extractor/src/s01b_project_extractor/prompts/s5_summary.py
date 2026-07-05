"""S5 摘要+自检 prompt：基于 S1-S4 结构化结果生成 project_summary，并对 stage/category/involvement 与事实一致性自检。"""
import json
import bri_enums as enums


def build_s5_prompt(structured_project: dict) -> str:
    """输入单个已结构化项目（common_info+chinese_entities），产出摘要与可选纠偏。"""
    stage_list = enums.prompt_value_list("project_stage")
    cat_list = ", ".join(c for c in enums.valid_categories() if c != "Unknown")
    inv_list = enums.prompt_value_list("involvement_type")
    payload = json.dumps(structured_project, ensure_ascii=False, indent=2)
    return f"""你是数据质检员。下面是已结构化的一个项目。请：
1) 生成 project_summary：2-3 句概括「哪个中国企业、在哪个国家、做什么类型项目、装机多少MW、什么阶段」，仅基于已有字段，不臆测。
2) 自检三字段与摘要语义是否自洽；不一致才给纠偏（confidence≥0.8 才提）。

结构化项目：
{payload}

### 合法枚举
- project_stage: {stage_list}
- category: {cat_list}
- involvement_type: {inv_list}

### 输出（严格 JSON）
{{
  "project_summary": "2-3 句中文摘要",
  "corrections": [
    {{"field": "project_stage|category|involvement_type", "suggested_value": "合法枚举值", "confidence": 0.0}}
  ]
}}
若无需纠偏，corrections 为 []。
"""
