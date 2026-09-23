"""Regression fixtures for the saved S34 envelope and evidence-loss failure."""
from copy import deepcopy
import pytest

from s01b_project_extractor.stages.entity_response import EntityResponseError, parse_entity_response
from s01b_project_extractor.stages.orchestrator import _run_stage_with_retry
from s01b_project_extractor.stages.s4_entity import enforce_entity_role_evidence, validate_s4


def entity(quote="", anchor="海角风电项目", role="设备供应商"):
    return {"Implementing Chinese Entity": "金风科技", "_entity_raw_name": "金风科技",
            "Type of involvement of Chinese Entities": role,
            "Participation Evidence": [{"quote": quote, "project_anchor": anchor, "role": role}]}


def test_explicit_envelope_is_copied_not_mutated():
    payload = {"type": "json_object", "content": {"chinese_entities": [entity()]}}
    original = deepcopy(payload)
    parsed = parse_entity_response(payload, stage="s34")
    parsed["chinese_entities"][0]["Implementing Chinese Entity"] = "normalized"
    assert payload == original


@pytest.mark.parametrize("payload", [None, [], {}, {"entities": []},
    {"chinese_entities": None}, {"chinese_entities": ["company"]},
    {"chinese_entities": [{}]}, {"type": "json_object", "content": "{}"},
    {"type": "json_object", "content": {"type": "json_object", "content": {"chinese_entities": []}}},
])
def test_unrecognized_shape_fails_loud(payload):
    with pytest.raises(EntityResponseError) as error:
        parse_entity_response(payload, stage="s34")
    assert error.value.payload == payload


@pytest.mark.parametrize("retry", [None, []])
def test_retry_keeps_prior_candidate_and_evidence(retry, caplog):
    candidate = entity("金风科技被背景提及。")
    outputs = iter([[candidate], retry])
    def run():
        value = next(outputs)
        if value is None:
            raise EntityResponseError("malformed", {"unexpected": "response"})
        return value
    result = _run_stage_with_retry(run, validate_s4, stage="s4")
    assert result[0]["Participation Evidence"] == candidate["Participation Evidence"]
    if retry is None:
        assert result[0]["_stage_response_errors"][0]["error"] == "malformed"
        assert "解析失败" in caplog.text


@pytest.mark.parametrize("role", ["设备供应商", "设计/施工服务方", "EPC总承包", "股权投资方"])
def test_equipment_use_proves_participation_only(role):
    quote = "海角风电项目安装了两台金风科技单机容量4.5MW机组。"
    candidate = entity(quote, role=role)
    enforce_entity_role_evidence([candidate], "", quote, "海角风电项目")
    assert candidate["_participation_evidence_status"] == "verified"
    assert candidate["Type of involvement of Chinese Entities"] == "Unknown"


@pytest.mark.parametrize("quote", [
    "海角风电项目未安装金风科技风机。",
    "海角风电项目未配备金风科技机组。",
    "海角风电项目不安装金风科技机组。",
    "海角风电项目已经开工。\n项目现场安装了明阳智能机组，另一处Gamma项目采用金风科技机组。",
    "海角风电项目使用其他企业风机，金风科技出席仪式。",
    "海角风电项目由外企开发，金风科技为另一项目供应风机。",
    "金风科技为青山风电项目供应风机，外企开发海角风电项目。",
    "海角风电项目由外企开发。背景：金风科技生产风机。",
])
def test_equipment_does_not_bypass_entity_project_negation(quote):
    candidate = entity(quote)
    enforce_entity_role_evidence([candidate], "", quote, "海角风电项目")
    assert candidate["_participation_evidence_status"] == "missing_or_ambiguous"


def test_equipment_explicit_reference_to_previous_paragraph():
    quote = "海角风电项目位于海边，总投资6000万美元。\n项目现场安装了两台金风科技单机容量4.5MW机组。"
    candidate = entity(quote)
    enforce_entity_role_evidence([candidate], "", quote, "海角风电项目")
    assert candidate["_participation_evidence_status"] == "verified"
    other = quote.replace("总投资6000万美元", "附近另有青山风电项目")
    candidate = entity(other)
    enforce_entity_role_evidence([candidate], "", other, "海角风电项目")
    assert candidate["_participation_evidence_status"] == "missing_or_ambiguous"


def test_same_name_retry_keeps_earlier_evidence():
    old = entity("背景中的金风科技")
    new = entity("海角风电项目使用金风科技风机。")
    new["_participation_evidence_status"] = "verified"
    attempts = iter([[old], [new]])
    result = _run_stage_with_retry(lambda: next(attempts), validate_s4, stage="s4")
    assert result[0]["_prior_candidate_evidence"][0]["Participation Evidence"] == old["Participation Evidence"]


def test_raw_json_failure_retains_prior_candidate():
    from s01b_project_extractor.llm.client import LLMClient, LLMConfig, LLMResponseParseError
    client = LLMClient(LLMConfig(max_retries=1), _client=object())
    client._raw_call = lambda *a: '{"type":"json_object"}\n{"chinese_entities":[]}'
    with pytest.raises(LLMResponseParseError):
        client.complete("fixture", stage="s34")
    candidate = entity("金风科技的背景信息")
    calls = []
    def run():
        calls.append(1)
        return [candidate] if len(calls) == 1 else client.complete("fixture", stage="s34")
    result = _run_stage_with_retry(run, validate_s4, stage="s4")
    assert result[0]["Participation Evidence"] == candidate["Participation Evidence"]
    assert result[0]["_stage_response_errors"][0]["response"].startswith('{"type"')


def test_only_whitespace_quote_normalization_is_allowed():
    from s01b_project_extractor.stages.evidence_binding import restore_quote_whitespace
    source = "海角风电项目已经开工。\n项目现场安装了金风科技机组。"
    quote = source.replace("\n", "")
    candidate = entity(quote)
    enforce_entity_role_evidence([candidate], "", source, "海角风电项目")
    assert candidate["_participation_evidence_status"] == "verified"
    assert candidate["Participation Evidence"][0]["quote"] == source
    assert candidate["Participation Evidence"][0]["model_quote"] == quote
    assert restore_quote_whitespace(quote, "", source.replace("\n", "另一项目已经开工。\n")) is None
    assert restore_quote_whitespace(quote, "", source + source) is None


def test_project_subject_directly_uses_named_equipment_series():
    quote = "双方将共同落地马来西亚大型光储一体化标杆项目，采用亿纬锂能 Mr.Big Family 系列储能解决方案。"
    candidate = entity(quote, anchor="马来西亚大型光储一体化标杆项目")
    candidate["Implementing Chinese Entity"] = "亿纬锂能"
    enforce_entity_role_evidence([candidate], "", quote, "马来西亚大型光储一体化标杆项目")
    assert candidate["_participation_evidence_status"] == "verified"
    assert candidate["Type of involvement of Chinese Entities"] == "Unknown"
    switched = quote.replace("，采用", "，另一项目采用")
    candidate["Participation Evidence"][0]["quote"] = switched
    enforce_entity_role_evidence([candidate], "", switched, "马来西亚大型光储一体化标杆项目")
    assert candidate["_participation_evidence_status"] == "missing_or_ambiguous"


def test_capacity_suffix_does_not_destroy_specific_latin_project_anchor():
    from s01b_project_extractor.stages.evidence_binding import is_anchor_for_target
    assert is_anchor_for_target("Baldon风储融合项目", "澳大利亚Baldon360MW风电+132MWh储能风储融合项目")
    assert not is_anchor_for_target("Baldon风储融合项目", "澳大利亚Moorabool360MW风储融合项目")


def test_s2_rejects_object_in_scalar_evidence_before_sqlite():
    from s01b_project_extractor.stages.s2_basic import run_s2
    class Broken:
        def complete(self, *a, **k):
            return {"projects": [{"common_info": {"Project Developer Evidence": {"quote": "original"}}}]}
    with pytest.raises(EntityResponseError, match="Project Developer Evidence must be a scalar"):
        run_s2("title", "original", "2026-09-23", llm=Broken())


def test_agreed_equipment_installation_with_explicit_project_reference():
    quote = "Çayırlar风电项目总装机43.5MW。项目最大的亮点是风储一体化。\n根据协议规划，项目现场将安装金风科技主力机型，具体包括多台风机。"
    candidate = entity(quote, anchor="Çayırlar风电项目")
    enforce_entity_role_evidence([candidate], "", quote, "土耳其Çayırlar风电项目")
    assert candidate["_participation_evidence_status"] == "verified"
    for bad in (quote.replace("项目现场将安装", "另一Gamma项目将安装"),
                quote.replace("将安装", "不会安装")):
        candidate = entity(bad, anchor="Çayırlar风电项目")
        enforce_entity_role_evidence([candidate], "", bad, "土耳其Çayırlar风电项目")
        assert candidate["_participation_evidence_status"] == "missing_or_ambiguous"


def test_summary_cannot_promote_unknown_participation_to_supplier():
    from s01b_project_extractor.stages.s5_summary import apply_s5
    project = {"common_info": {"Project Name": "海角风电项目", "capacity_mw_parsed": 9},
               "chinese_entities": [{"_entity_raw_name": "金风科技", "Type of involvement of Chinese Entities": "Unknown"}]}
    original = "金风科技为海角风电项目设备供应商。"
    apply_s5(project, {"project_summary": original})
    assert project["model_project_summary"] == original
    assert "设备供应商" not in project["project_summary"]
    assert "具体角色未明确" in project["project_summary"]
    assert "9MW" in project["project_summary"]


def test_valid_entity_cannot_override_unresolved_capacity_validation():
    from s01b_project_extractor.stages.orchestrator import _apply_structure_publication_gate
    verified = {"_entity_raw_name": "中国电建", "_participation_evidence_status": "verified"}
    project = {"common_info": {"Project Name": "苏伊士湾风电项目",
                               "capacity_evidence_errors": ["Capacity Evidence: 锚点不匹配"]},
               "chinese_entities": [verified], "publication_status": "eligible"}
    _apply_structure_publication_gate(project)
    assert project["publication_status"] == "pending_review"
    assert project["entity_gate_excluded"]
    assert project["chinese_entities"] == [verified]
    assert project["structure_validation_errors"]


def test_summary_conflicting_stage_preserves_model_text_for_audit():
    from s01b_project_extractor.stages.s5_summary import apply_s5
    project = {"common_info": {"Project Name": "风机制造工厂", "Project Stage": "拟开发"}}
    original = "项目登记阶段为签约，但尚处于前期谈判阶段。"
    apply_s5(project, {"project_summary": original})
    assert project["model_project_summary"] == original
    assert "签约" not in project["project_summary"]
    assert "拟开发" in project["project_summary"]


@pytest.mark.parametrize("message,field", [
    ("single-unit capacity lacks unambiguous complete project count", "capacity_mw_parsed"),
    ("Storage Power: no explicit project-bound storage measurement", "storage_power_mw_parsed"),
])
def test_safe_null_capacity_does_not_block_verified_project(message, field):
    from s01b_project_extractor.stages.orchestrator import _apply_structure_publication_gate
    from s01b_project_extractor.stages.s2_basic import validate_s2_blocking
    project = {"common_info": {"Project Name": "海角风电项目", field: None,
                               "capacity_evidence_errors": [message]},
               "publication_status": "eligible", "entity_gate_excluded": False}
    assert validate_s2_blocking([project]) == []
    _apply_structure_publication_gate(project)
    assert project["publication_status"] == "eligible"
    assert not project["entity_gate_excluded"]
    assert project["needs_review"] and project["field_review_warnings"]
    project["common_info"][field] = 4.5
    assert validate_s2_blocking([project])


def test_format_warning_does_not_reopen_entity_exclusion():
    from s01b_project_extractor.stages.orchestrator import _apply_structure_publication_gate
    project = {"common_info": {"Project Name": "Cape Wind Farm"},
               "publication_status": "excluded", "entity_gate_excluded": True}
    _apply_structure_publication_gate(project)
    assert project["publication_status"] == "excluded"
    assert project["entity_gate_excluded"]
    assert project["field_review_warnings"]


def test_missing_project_identity_is_blocking():
    from s01b_project_extractor.stages.s2_basic import validate_s2_blocking
    assert validate_s2_blocking([{"common_info": {"Project Name": ""}}])
