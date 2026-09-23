"""S5 仅摘要，不重新裁定结构化事实。"""
import json

S5_PROMPT_VERSION = "2026-09-23.3-summary-only"


def build_s5_prompt(structured_project: dict) -> str:
    """只传递已核实字段；候选、失败引文及原模型建议不是摘要事实来源。"""
    ci = structured_project.get("common_info", {})
    allowed = ("Project Name", "Country", "Category", "Project Stage",
               "capacity_mw_parsed", "storage_power_mw_parsed", "storage_energy_mwh_parsed",
               "voltage_kv_parsed", "line_length_km_parsed",
               "transmission_capacity_mw_parsed", "production_capacity_value_parsed",
               "production_capacity_unit_normalized")
    payload = {"common_info": {key: ci[key] for key in allowed if key in ci},
               "chinese_entities": [{key: entity.get(key) for key in
                    ("Implementing Chinese Entity", "Type of involvement of Chinese Entities")}
                    for entity in structured_project.get("chinese_entities", [])]}
    return f"""提示词版本：{S5_PROMPT_VERSION}
你是摘要编辑。仅根据下列已核实字段生成2-3句中文 project_summary。
不得修改或重新推断阶段、容量、分类、企业身份及角色；Unknown/空值保持未知或省略。
参与已确认但角色Unknown时只写参与，不推定供应商、EPC、投资方或业主。
发电装机、储能功率和储能电量分别使用MW、MW和MWh；MF 写生产产能及原单位；TX 写电压 kV、线路 km、输送 MW/MVA。禁止把 TX/MF 规格描述成发电装机。
不得借用未给出的候选证据、正文背景或其他项目数字，不输出corrections。
结构化项目：
{json.dumps(payload, ensure_ascii=False, indent=2)}
严格JSON输出：{{"project_summary": "中文摘要"}}
"""
