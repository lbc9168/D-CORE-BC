"""S4 阶段：抽取某项目的中方实体并归一母子公司，校验 involvement_type 合法。"""
import bri_enums as enums
from s01b_project_extractor.stages.entity_normalize import normalize_entity


def run_s4(title: str, content: str, project_name: str, category: str, *, llm) -> list[dict]:
    """跑 S4 抽取并对每个实体做母子公司归一；返回 chinese_entities 列表。"""
    from s01b_project_extractor.prompts.s4_entity import build_s4_prompt
    from s01b_project_extractor.tools.project_extractor_tool import _get_entity_lookup_text
    from s01b_project_extractor.knowledge import main_equipment
    prompt = build_s4_prompt(title, content, project_name, category,
                             _get_entity_lookup_text(), main_equipment(category))
    out = llm.complete(prompt, stage="s4", json_mode=True)
    ents = out.get("chinese_entities", []) if isinstance(out, dict) else []
    for e in ents:
        raw = e.get("Implementing Chinese Entity", "")
        parent, sub = normalize_entity(raw)
        e["Implementing Chinese Entity"] = parent
        if not (e.get("Subsidiary Name") or "").strip() and sub:
            e["Subsidiary Name"] = sub
    return ents


def validate_s4(entities: list[dict]) -> list[str]:
    """校验：每个实体 involvement_type 合法、Implementing Chinese Entity 非空。"""
    errors = []
    valid_inv = set(enums.valid_involvement())
    for i, e in enumerate(entities):
        inv = str(e.get("Type of involvement of Chinese Entities", "")).strip()
        if inv and inv not in valid_inv:
            errors.append(f"实体#{i} 参与方式不合法: {inv}")
        if not str(e.get("Implementing Chinese Entity", "")).strip():
            errors.append(f"实体#{i} Implementing Chinese Entity 为空")
    return errors
