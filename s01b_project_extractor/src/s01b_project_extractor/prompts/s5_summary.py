"""S5 摘要+阶段自检 prompt：实体角色与 category 均由确定性证据策略锁定。"""
import json
import bri_enums as enums


def build_s5_prompt(structured_project: dict) -> str:
    """输入单个已结构化项目（common_info+chinese_entities），产出摘要与可选纠偏。"""
    stage_list = enums.prompt_value_list("project_stage")
    payload = json.dumps(structured_project, ensure_ascii=False, indent=2)
    return f"""你是数据质检员。下面是已结构化的一个项目。请：
1) 生成 project_summary：2-3 句概括中企、国家、项目类型、对应领域规格和阶段，仅基于已有字段，不臆测。发电写装机 MW；独立储能写功率 MW/能量 MWh；TX 写电压 kV、线路 km、输送 MW/MVA；MF 写生产产能及原单位。禁止把 TX/MF 规格描述成发电装机。
2) 自检 project_stage 是否自洽；不一致才给纠偏（confidence≥0.8 才提）。参与方式由项目—企业—角色逐字证据门控锁定，禁止建议或修改 involvement_type。发电/储能 Category 与标签由 energy_taxonomy_v2.1 根据原子事实确定；TX/MF 保留其数据域 Category。禁止建议或修改 Category。

结构化项目：
{payload}

### 合法枚举
- project_stage: {stage_list}

### 输出（严格 JSON）
{{
  "project_summary": "2-3 句中文摘要",
  "corrections": [
    {{"field": "project_stage", "suggested_value": "合法枚举值", "confidence": 0.0}}
  ]
}}
若无需纠偏，corrections 为 []。
"""
