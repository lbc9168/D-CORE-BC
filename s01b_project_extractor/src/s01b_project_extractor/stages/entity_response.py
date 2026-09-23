"""Explicit entity-response boundary; malformed responses are not negative evidence."""
from copy import deepcopy
from s01b_project_extractor.llm.client import LLMResponseParseError


class EntityResponseError(LLMResponseParseError):
    """Retain the rejected payload for the extraction failure audit."""

    def __init__(self, message, payload):
        super().__init__(message, deepcopy(payload))


def parse_entity_response(payload: object, *, stage: str) -> dict:
    """Accept a plain object or exactly one json_object/content object envelope."""
    original = payload
    if isinstance(payload, dict) and payload.get("type") == "json_object":
        if set(payload) != {"type", "content"} or not isinstance(payload["content"], dict):
            raise EntityResponseError(f"{stage}: invalid json_object envelope", original)
        payload = payload["content"]
    if not isinstance(payload, dict) or not isinstance(payload.get("chinese_entities"), list):
        raise EntityResponseError(f"{stage}: chinese_entities must be an explicit list", original)
    project_assessment = payload.get("Project Assessment")
    if project_assessment is not None:
        if not isinstance(project_assessment, dict):
            raise EntityResponseError(f"{stage}: Project Assessment must be an object", original)
        for key in ("is_current_event", "is_single_physical_project"):
            if key not in project_assessment or (project_assessment[key] is not None and type(project_assessment[key]) is not bool):
                raise EntityResponseError(f"{stage}: project {key} requires explicit boolean or null", original)
        if not isinstance(project_assessment.get("rationale"), str):
            raise EntityResponseError(f"{stage}: project rationale must be a string", original)
        for key in ("evidence_quotes", "separate_projects"):
            if not isinstance(project_assessment.get(key), list) or any(not isinstance(v, str) for v in project_assessment[key]):
                raise EntityResponseError(f"{stage}: project {key} must be strings", original)
    for index, entity in enumerate(payload["chinese_entities"]):
        if not isinstance(entity, dict):
            raise EntityResponseError(f"{stage}: entity {index} must be an object", original)
        name = entity.get("Implementing Chinese Entity")
        if not isinstance(name, str) or not name.strip():
            raise EntityResponseError(f"{stage}: entity {index} needs a nonempty string name", original)
        for key in ("Subsidiary Name", "Type of involvement of Chinese Entities", "equipment_role", "equipment_items", "equipment_supply_scope", "Subcontract Amount (Million USD)"):
            if isinstance(entity.get(key), (dict, list)):
                raise EntityResponseError(f"{stage}: entity {index} {key} must be scalar", original)
        evidence = entity.get("Participation Evidence", [])
        if not isinstance(evidence, list) or any(not isinstance(item, dict) for item in evidence):
            raise EntityResponseError(f"{stage}: entity {index} has invalid participation evidence", original)
        for item in evidence:
            if any(not isinstance(item.get(key), str) for key in ("quote", "project_anchor", "role")):
                raise EntityResponseError(f"{stage}: participation evidence fields must be strings", original)
        assessment = entity.get("Participation Assessment")
        if assessment is not None:
            if not isinstance(assessment, dict):
                raise EntityResponseError(f"{stage}: Participation Assessment must be an object", original)
            for key in ("participates", "is_chinese"):
                if key not in assessment or (assessment[key] is not None and type(assessment[key]) is not bool):
                    raise EntityResponseError(f"{stage}: assessment {key} must be explicit boolean or null", original)
            if assessment.get("identity_basis") not in {"article", "registry", "model_knowledge", "uncertain"}:
                raise EntityResponseError(f"{stage}: invalid identity_basis", original)
            for key in ("rationale", "role", "role_basis"):
                if not isinstance(assessment.get(key), str):
                    raise EntityResponseError(f"{stage}: assessment {key} must be a string", original)
            quotes = assessment.get("evidence_quotes")
            if not isinstance(quotes, list) or any(not isinstance(q, str) for q in quotes):
                raise EntityResponseError(f"{stage}: evidence_quotes must be strings", original)
    if stage == "s34":
        for key, value in payload.items():
            if key not in {"chinese_entities", "Financing Evidence", "Amount Evidence", "Project Assessment"} and isinstance(value, (dict, list)):
                raise EntityResponseError(f"{stage}: finance field {key} must be scalar", original)
        financing = payload.get("Financing Evidence", [])
        if not isinstance(financing, list) or any(not isinstance(item, dict) for item in financing):
            raise EntityResponseError(f"{stage}: Financing Evidence must be a list of objects", original)
    return deepcopy(payload)
