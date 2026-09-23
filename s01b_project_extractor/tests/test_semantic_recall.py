"""Offline behavioral contracts for semantic recall; no network or database writes."""
from copy import deepcopy

import pytest

from s01b_project_extractor.stages.capacity_evidence import apply_capacity_evidence
from s01b_project_extractor.stages.entity_normalize import EntityResolution
from s01b_project_extractor.stages.entity_response import EntityResponseError
from s01b_project_extractor.stages.orchestrator import _apply_entity_publication_gate
from s01b_project_extractor.stages.orchestrator import extract_staged
from s01b_project_extractor.stages.s2_basic import run_s2
from s01b_project_extractor.stages.s3_finance import run_s3
from s01b_project_extractor.stages.s4_entity import run_s4
from s01b_project_extractor.stages.s34_entity_finance import run_s34


class Stub:
    def __init__(self, payload):
        self.payload = payload

    def complete(self, *_args, **_kwargs):
        return deepcopy(self.payload)


def candidate(**assessment_overrides):
    return {
        "Implementing Chinese Entity": "新星储能",
        "Type of involvement of Chinese Entities": "Unknown",
        "Participation Assessment": {
            "participates": True, "is_chinese": True,
            "identity_basis": "model_knowledge",
            "rationale": "新星储能为中国储能品牌，名单对应前段的甲项目。",
            "evidence_quotes": ["甲项目今日投运。", "合作单位：新星储能、当地设计院。"],
            "role": "Unknown", "role_basis": "名单明确参与，未披露合同职责。",
            **assessment_overrides,
        },
    }


SOURCE = "甲项目今日投运。\n本期合作单位如下。\n合作单位：新星储能、当地设计院。"


@pytest.fixture
def registry(monkeypatch):
    def install(status="unmatched_needs_review", entity_id=None):
        monkeypatch.setattr(
            "s01b_project_extractor.stages.s4_entity.resolve_entity",
            lambda name: EntityResolution(name, "", status, "test_registry", entity_id=entity_id, raw_name=name),
        )
    install()
    return install


def entity_stage(stage, payload, content=SOURCE):
    runner = run_s34 if stage == "s34" else run_s4
    out = runner("甲项目", content, "甲地区储能项目", "储能", llm=Stub(payload))
    return out["chinese_entities"] if stage == "s34" else out


def publication(entities):
    project = {"chinese_entities": entities}
    _apply_entity_publication_gate(project)
    return project


@pytest.mark.parametrize("stage", ["s4", "s34"])
def test_semantic_list_and_cross_sentence_unknown_role_can_publish_without_registry(stage, registry):
    project = publication(entity_stage(stage, {"chinese_entities": [candidate()]}))
    assert project["publication_status"] == "eligible"
    entity = project["chinese_entities"][0]
    assert entity["_entity_id"] is None
    assert entity["_identity_standardization_pending"] is True
    assert entity["_identity_judgment_source"] == "model_knowledge"
    assert entity["Type of involvement of Chinese Entities"] == "Unknown"
    assert entity["_entity_raw_name"] == "新星储能"


@pytest.mark.parametrize("stage", ["s4", "s34"])
@pytest.mark.parametrize("registry_status,model_chinese", [
    ("verified_non_chinese", True), ("verified_chinese", False),
])
def test_foreign_registry_or_article_namesake_is_not_published(stage, registry, registry_status, model_chinese):
    registry(registry_status, "ent_existing")
    row = candidate(is_chinese=model_chinese, rationale="本文为当地同名企业，不能套用中国集团身份。")
    project = publication(entity_stage(stage, {"chinese_entities": [row]}))
    assert project["chinese_entities"] == []
    assert project["publication_status"] != "eligible"
    assert project["entity_candidates"][0]["Participation Assessment"]["is_chinese"] is model_chinese


@pytest.mark.parametrize("stage", ["s4", "s34"])
@pytest.mark.parametrize("participates,expected", [(False, "excluded"), (None, "pending_review")])
def test_negative_or_ambiguous_participation_cannot_become_eligible(stage, registry, participates, expected):
    row = candidate(participates=participates, rationale="该企业属于背景乙项目，并非甲项目参与方。")
    project = publication(entity_stage(stage, {"chinese_entities": [row]}))
    assert project["publication_status"] == expected
    assert not project["chinese_entities"]


@pytest.mark.parametrize("stage", ["s4", "s34"])
def test_old_response_without_assessment_does_not_receive_affirmative_defaults(stage, registry):
    old = candidate()
    old.pop("Participation Assessment")
    out = entity_stage(stage, {"chinese_entities": [old]})
    assert out[0]["_requires_reextraction"] is True
    assert "Participation Assessment" not in out[0]
    assert publication(out)["publication_status"] == "pending_review"


@pytest.mark.parametrize("stage", ["s4", "s34"])
@pytest.mark.parametrize("malformed", [
    {"participates": "true"}, {"is_chinese": 1}, {"evidence_quotes": "not a list"},
])
def test_bad_assessment_structure_is_a_technical_failure(stage, registry, malformed):
    with pytest.raises(EntityResponseError):
        entity_stage(stage, {"chinese_entities": [candidate(**malformed)]})


@pytest.mark.parametrize("stage", ["s4", "s34"])
def test_missing_entity_array_is_technical_failure_not_empty_negative(stage, registry):
    with pytest.raises(EntityResponseError):
        entity_stage(stage, {})


def capacity(text, *, scope="project", raw="100MW", **overrides):
    ci = {
        "Project Name": "甲项目", "Category": "风电", "Generation Sources": ["风电"],
        "Capacity (MW)": raw, "Storage Power": "", "Storage Energy": "",
        "storage_status_normalized": "not_mentioned", "capacity_mw_parsed": 100,
        "Capacity Evidence": {"quote": text, "project_anchor": "甲地区", "evidence_quotes": [],
                              "scope": scope, "rationale": "按上下文判断数量属于本次报道对象。"},
        **overrides,
    }
    apply_capacity_evidence(ci, "甲项目", text)
    return ci


def test_capacity_project_scope_accepts_cross_sentence_location_alias():
    ci = capacity("甲地区的工程今日投运。总装机容量100MW。")
    assert ci["capacity_mw_parsed"] == 100


@pytest.mark.parametrize("scope", ["portfolio", "unit", "unknown"])
def test_nonproject_capacity_is_cleared_locally_with_original_kept(scope):
    ci = capacity("合计容量100MW。", scope=scope)
    assert ci["capacity_mw_parsed"] is None
    assert ci["Capacity (MW)"] == ""
    assert ci["capacity_rejected_values"]["capacity_mw"]["raw"] == "100MW"


def test_explicit_project_total_beats_rounded_turbine_product():
    ci = capacity("总装机容量123MW，共安装16台单机容量7.7MW风电机组。", raw="7.7MW")
    assert ci["capacity_mw_parsed"] == 123


def test_complete_turbine_count_can_compute_project_total():
    ci = capacity("本工程安装两台单机容量4.5MW风电机组。", raw="4.5MW")
    assert ci["capacity_mw_parsed"] == 9
    assert "unit_count_product" in ci["capacity_details_note"]


def test_storage_dimensions_are_separate_and_do_not_become_generation():
    ci = capacity("本储能项目规模25MW/125MWh。", raw="125MWh", **{
        "Category": "储能", "Generation Sources": [], "Storage Power": "25MW",
        "Storage Energy": "125MWh", "storage_status_normalized": "confirmed",
    })
    assert ci["capacity_mw_parsed"] is None
    assert ci["storage_power_mw_parsed"] == 25
    assert ci["storage_energy_mwh_parsed"] == 125


@pytest.mark.parametrize("evidence", [None, {"quote": None, "project_anchor": "", "evidence_quotes": None,
                                           "scope": "unknown", "rationale": "纯储能无发电装机"},
    {"quote": None, "project_anchor": None, "evidence_quotes": [], "scope": "unknown", "rationale": None}])
def test_pure_storage_uses_energy_evidence_when_generation_capacity_evidence_is_empty(evidence):
    source = "甲项目新建储能系统规模25MW/125MWh。"
    ci = capacity(source, raw="", **{
        "Category": "储能", "Generation Sources": [], "capacity_mw_parsed": None,
        "Storage Power": "25MW", "Storage Energy": "125MWh",
        "storage_status_normalized": "confirmed", "Capacity Evidence": evidence,
        "Energy Composition Evidence": source,
        "Project Context": {"source_name": "甲项目", "aliases": [], "context": "本次储能项目", "is_current_event": True},
    })
    assert ci["capacity_mw_parsed"] is None
    assert ci["storage_power_mw_parsed"] == 25
    assert ci["storage_energy_mwh_parsed"] == 125
    assert ci["Capacity Evidence"] == evidence


def test_gas_turbine_spec_is_not_total_combined_heat_and_power_capacity():
    ci = capacity("配置一套57MW燃气轮机及热电联产高效蒸汽轮机。", raw="57MW", **{
        "Category": "火电", "Generation Sources": ["火电"],
    })
    assert ci["capacity_mw_parsed"] is None
    assert ci["capacity_rejected_values"]["capacity_mw"]["raw"] == "57MW"


def test_tx_voltage_retains_primary_side_and_does_not_become_generation_capacity():
    source = "柬埔寨甲变电站采用115/22千伏车载移动式变电站。"
    ci = {"Project Name": "柬埔寨甲变电站项目", "Country": "柬埔寨", "Category": "输变电建设",
          "Project Stage": "在建", "TX Voltage Evidence": "115/22千伏", "Capacity (MW)": ""}
    out = run_s2("甲变电站", source, "2026-09-23", llm=Stub({"projects": [{"common_info": ci}]}))[0]["common_info"]
    assert out["voltage_kv_parsed"] == 115
    assert out["voltage_kv_raw_phrase"] == "115/22千伏"
    assert out["capacity_mw_parsed"] is None


FIELD = "Total Project Investment (Million USD)"


def money_evidence(**overrides):
    return {"field": FIELD, "amount_scope": "project", "currency": "CNY",
            "original_amount": 700_000_000, "usd_per_currency_unit": None,
            "conversion_basis": "", "evidence_quotes": ["总投资7亿元人民币"],
            "rationale": "本项目总投资", **overrides}


def finance_stage(stage, evidence, *, content="总投资7亿元人民币。", extra=None):
    payload = {"chinese_entities": [], FIELD: 100, "Total Project Investment Raw": "7亿元人民币",
               "Amount Evidence": [evidence], **(extra or {})}
    if stage == "s3":
        payload.pop("chinese_entities")
        return run_s3("甲项目", content, "甲项目", llm=Stub(payload))
    return run_s34("甲项目", content, "甲项目", "光伏", llm=Stub(payload))["finance"]


@pytest.mark.parametrize("stage", ["s3", "s34"])
@pytest.mark.parametrize("scope", ["portfolio", "equipment_unit"])
def test_amount_not_allocated_from_portfolio_or_equipment_price(stage, scope):
    out = finance_stage(stage, money_evidence(amount_scope=scope, currency="USD",
        usd_per_currency_unit=1, conversion_basis="原文美元"))
    assert out[FIELD] is None
    assert out["total_investment_usd_raw_phrase"] == "7亿元人民币"


@pytest.mark.parametrize("stage", ["s3", "s34"])
def test_non_usd_amount_without_exchange_basis_stays_null(stage):
    out = finance_stage(stage, money_evidence())
    assert out[FIELD] is None


@pytest.mark.parametrize("stage", ["s3", "s34"])
def test_explicit_article_conversion_is_reproducible(stage):
    out = finance_stage(stage, money_evidence(usd_per_currency_unit=1 / 7,
        conversion_basis="原文：7亿元人民币，相当于1亿美元"), content="总投资7亿元人民币，相当于1亿美元。")
    assert out[FIELD] == 100


@pytest.mark.parametrize("stage", ["s3", "s34"])
def test_invented_exchange_basis_does_not_become_reproducible_by_saying_article(stage):
    out = finance_stage(stage, money_evidence(usd_per_currency_unit=1 / 7,
        conversion_basis="原文：7亿元人民币，相当于1亿美元"), content="总投资7亿元人民币。")
    assert out[FIELD] is None


@pytest.mark.parametrize("stage", ["s4", "s34"])
def test_top_level_subcontract_portfolio_evidence_is_not_lost(stage, registry):
    row = candidate()
    row["Subcontract Amount (Million USD)"] = 100
    payload = {"chinese_entities": [row], "Amount Evidence": [money_evidence(
        field="Subcontract Amount (Million USD)", amount_scope="portfolio",
        currency="USD", original_amount=100_000_000, usd_per_currency_unit=1,
        conversion_basis="原文美元", rationale="多个项目的设备组合订单")],
    }
    entity = entity_stage(stage, payload)[0]
    assert entity["Subcontract Amount (Million USD)"] is None


class PipelineStub:
    def __init__(self, entity_payload, *, empty_s2=False):
        self.entity_payload = entity_payload
        self.empty_s2 = empty_s2
        self.calls = []

    def complete(self, _prompt, *, stage, **_kwargs):
        self.calls.append(stage)
        if stage == "s1":
            return {"status": "valid_project", "reasoning": "候选能源工程"}
        if stage == "s2":
            projects = [] if self.empty_s2 else [{"in_scope": True, "common_info": {
                "Project Name": "甲项目", "Country": "摩洛哥", "Category": "光伏", "Project Stage": "在建",
                "Record Type": "具体项目", "Generation Sources": ["光伏"], "Storage Status": "not_mentioned",
            }}]
            return {"projects": projects}
        if stage in {"s4", "s34"}:
            return deepcopy(self.entity_payload)
        if stage == "s3":
            return {"Financing Model": "Unknown"}
        if stage == "s5":
            return {"project_summary": "甲项目在建。"}
        raise AssertionError(stage)


@pytest.mark.parametrize("merged", [False, True])
@pytest.mark.parametrize("current,single,expected", [(False, True, "excluded"), (True, False, "pending_review")])
def test_project_assessment_controls_background_and_merged_asset_publication(monkeypatch, registry, merged, current, single, expected):
    if merged:
        monkeypatch.setenv("S01B_MERGE_S34", "1")
    else:
        monkeypatch.delenv("S01B_MERGE_S34", raising=False)
    assessment = {"is_current_event": current, "is_single_physical_project": single,
                  "rationale": "历史业绩或两个独立异地电站", "evidence_quotes": ["两个场站相距220公里"],
                  "separate_projects": [] if single else ["甲站", "乙站"]}
    stub = PipelineStub({"chinese_entities": [candidate()], "Project Assessment": assessment})
    project = extract_staged("甲项目", SOURCE, "2026-09-23", llm=stub)["projects"][0]
    assert project["publication_status"] == expected
    assert project["project_assessment"] == assessment
    assert project["entity_gate_excluded"] is True
    assert "s5" not in stub.calls


def test_standalone_s4_keeps_project_assessment_even_when_entity_list_empty():
    assessment = {"is_current_event": False, "is_single_physical_project": True,
                  "rationale": "仅历史业绩", "evidence_quotes": ["此前项目已完工"], "separate_projects": []}
    result = run_s4("甲项目", "此前项目已完工", "甲项目", "光伏",
                    llm=Stub({"chinese_entities": [], "Project Assessment": assessment}))
    assert result == []
    assert result.project_assessment == assessment


@pytest.mark.parametrize("merged", [False, True])
def test_missing_project_assessment_does_not_fabricate_a_model_judgment(monkeypatch, registry, merged):
    if merged:
        monkeypatch.setenv("S01B_MERGE_S34", "1")
    else:
        monkeypatch.delenv("S01B_MERGE_S34", raising=False)
    stub = PipelineStub({"chinese_entities": [candidate()]})
    project = extract_staged("甲项目", SOURCE, "2026-09-23", llm=stub)["projects"][0]
    assert project["project_assessment"] is None
    assert project["publication_status"] == "eligible"


def test_s2_explicit_empty_projects_is_successful_scope_exclusion():
    stub = PipelineStub({}, empty_s2=True)
    result = extract_staged("矿厂投产", "矿厂参观包含光伏区域。", "2026-09-23", llm=stub)
    assert result["status"] == "irrelevant"
    assert result["projects"] == []
    assert stub.calls == ["s1", "s2"]


def test_explicit_nonparticipant_is_persisted_excluded_not_pending(tmp_path, monkeypatch, registry):
    import json
    import sqlite3
    import pandas as pd
    from s01b_project_extractor.tools import project_extractor_tool as pet

    project = publication(entity_stage("s34", {"chinese_entities": [candidate(participates=False)]}))
    project.update(in_scope=True, common_info={
        "Project Name": "摩洛哥甲项目", "Country": "摩洛哥", "Category": "光伏",
        "Project Stage": "在建", "Record Type": "具体项目", "Capacity (MW)": "",
    })
    monkeypatch.setattr(pet, "extract_one", lambda *_args, **_kwargs: {
        "analysis": {"reasoning": "否定参与"}, "status": "valid_project", "projects": [project],
    })
    monkeypatch.setattr(pet, "_resolve_entity_en", lambda *_args, **_kwargs: "Test Entity")
    monkeypatch.setattr(pet, "_resolve_is_central_soe", lambda *_args, **_kwargs: 0)
    db = tmp_path / "projects.db"
    pet.ProjectExtractorTool()._process_df(pd.DataFrame([{
        "原文链接": "https://example.test/nonparticipant", "文章标题": "摩洛哥项目",
        "正文内容": SOURCE, "发布日期": "2026-09-23",
    }]), batch_id="semantic-negative", ts="20260923", client=None, country_db={},
        projects_db_path=str(db), review_db_path=str(tmp_path / "review.db"))
    with sqlite3.connect(db) as connection:
        row = connection.execute("SELECT publication_status, excluded, needs_review, entity_evidence_json FROM extracted_projects").fetchone()
    assert row[:3] == ("excluded", 1, 0)
    assert json.loads(row[3])["entity"]["Participation Assessment"]["participates"] is False


class BoundaryRepairStub(PipelineStub):
    def __init__(self, *, repair_succeeds=True):
        super().__init__({})
        self.repair_succeeds = repair_succeeds
        self.s2_prompts = []

    def complete(self, prompt, *, stage, **kwargs):
        if stage == "s2":
            self.calls.append(stage)
            self.s2_prompts.append(prompt)
            names = ["甲站", "乙站"] if self.repair_succeeds and len(self.s2_prompts) > 1 else ["甲乙合计项目"]
            return {"projects": [{"in_scope": True, "common_info": {
                "Project Name": name, "Country": "哥伦比亚", "Category": "光伏", "Project Stage": "竣工",
                "Record Type": "具体项目", "Generation Sources": ["光伏"], "Storage Status": "not_mentioned",
                "Capacity (MW)": "15.99MW" if len(names) == 1 else "",
            }} for name in names]}
        if stage in {"s4", "s34"}:
            self.calls.append(stage)
            combined = "目标项目：甲乙合计项目" in prompt
            return {"chinese_entities": [candidate()], "Project Assessment": {
                "is_current_event": True, "is_single_physical_project": not combined,
                "rationale": "两个独立场站相距220公里，共同合同不能证明一个物理项目" if combined else "独立场站",
                "evidence_quotes": ["甲站与乙站相距220公里，合计15.99MW"],
                "separate_projects": ["甲站", "乙站"] if combined else [],
            }, "Total Project Investment (Million USD)": 100 if combined else "",
                "Amount Evidence": []}
        return super().complete(prompt, stage=stage, **kwargs)


@pytest.mark.parametrize("merged", [False, True])
@pytest.mark.parametrize("repair_succeeds", [False, True])
def test_explicit_boundary_feedback_reextracts_only_once_without_copying_portfolio_fields(monkeypatch, registry, merged, repair_succeeds):
    if merged:
        monkeypatch.setenv("S01B_MERGE_S34", "1")
    else:
        monkeypatch.delenv("S01B_MERGE_S34", raising=False)
    stub = BoundaryRepairStub(repair_succeeds=repair_succeeds)
    result = extract_staged("两站完工", "甲站与乙站相距220公里，合计15.99MW。", "2026-09-23", llm=stub)
    assert len(stub.s2_prompts) == 2
    assert "甲站" in stub.s2_prompts[1] and "乙站" in stub.s2_prompts[1]
    assert result["analysis"]["boundary_corrections"]
    if repair_succeeds:
        assert {p["common_info"]["Project Name"] for p in result["projects"]} == {"甲站", "乙站"}
        assert all(p["publication_status"] == "eligible" for p in result["projects"])
        assert all(p["common_info"].get("capacity_mw_parsed") is None for p in result["projects"])
        assert all(p["common_info"].get(FIELD) in (None, "") for p in result["projects"])
    else:
        assert len(result["projects"]) == 1
        assert result["projects"][0]["publication_status"] == "pending_review"

@pytest.mark.parametrize("field,raw,name,retained", [
    ("capacity_mw", "15.99MW", "甲站15.99兆瓦（115.99MW备用）15.99MWh项目", "115.99MW"),
    ("storage_energy_mwh", "125MWh", "甲站0.125GWh/25MW储能项目", "25MW"),
])
def test_rejected_capacity_ledger_cleans_equivalent_display_units_without_dimension_mix(field, raw, name, retained):
    from s01b_project_extractor.stages.capacity_evidence import _clean_rejected_capacity_names
    ci = {"Project Name": name, "capacity_rejected_values": {field: {"raw": raw}}}
    _clean_rejected_capacity_names(ci)
    clean = ci["capacity_clean_names"]["Project Name"]
    assert ci["Project Name"] == name
    assert ci["capacity_original_names"]["Project Name"] == name
    assert retained in clean
    if field == "capacity_mw":
        assert "15.99兆瓦" not in clean and "15.99MWh" in clean
    else:
        assert "0.125GWh" not in clean


@pytest.mark.parametrize("merged", [False, True])
def test_rejected_portfolio_capacity_name_is_safe_before_entity_and_summary_stages(monkeypatch, registry, merged):
    if merged:
        monkeypatch.setenv("S01B_MERGE_S34", "1")
    else:
        monkeypatch.delenv("S01B_MERGE_S34", raising=False)
    source = "甲站与乙站合计15.99MW，两个场站相距220公里。"
    class SafeNameStub(PipelineStub):
        def complete(self, prompt, *, stage, **kwargs):
            if stage == "s2":
                self.calls.append(stage)
                return {"projects": [{"in_scope": True, "common_info": {
                    "Project Name": "甲站15.99MW光伏项目", "Project Name (EN)": "Alpha 15.99MW Solar Project",
                    "Country": "哥伦比亚", "Category": "光伏", "Project Stage": "竣工",
                    "Record Type": "具体项目", "Generation Sources": ["光伏"], "Storage Status": "not_mentioned",
                    "Capacity (MW)": "15.99MW", "Capacity Evidence": {"scope": "portfolio", "rationale": "两站合计，本站未披露", "quote": source, "evidence_quotes": []},
                    "Project Context": {"source_name": "甲站", "aliases": [], "context": source, "is_current_event": True},
                }}]}
            if stage in {"s3", "s4", "s34"}:
                target_line = next(line for line in prompt.splitlines() if line.startswith("目标项目："))
                assert "15.99" not in target_line
                assert "甲站" in target_line
            if stage == "s5":
                import json
                payload = json.JSONDecoder().raw_decode(prompt.split("结构化项目：", 1)[1].lstrip())[0]
                assert "15.99" not in payload["common_info"]["Project Name"]
            return super().complete(prompt, stage=stage, **kwargs)
    stub = SafeNameStub({"chinese_entities": [candidate()]})
    project = extract_staged("两站完工", source, "2026-09-23", llm=stub)["projects"][0]
    ci = project["common_info"]
    assert project["publication_status"] == "eligible"
    assert ci["capacity_mw_parsed"] is None
    assert ci["capacity_rejected_values"]["capacity_mw"]["raw"] == "15.99MW"
    assert ci["capacity_original_names"]["Project Name"] == "甲站15.99MW光伏项目"
    assert ci["Project Name"] == "甲站光伏项目"

@pytest.mark.parametrize("merged", [False, True])
@pytest.mark.parametrize("review,expected", [
    ({"Project Developer": "Borzești Energy Storage;RNV;Greenvolt", "Project Developer Evidence": "项目公司由RNV与Greenvolt持股"}, "Borzești Energy Storage;RNV;Greenvolt"),
    ({"Project Developer": None, "Project Developer Evidence": None}, ""),
    ({"Project Developer": "", "Project Developer Evidence": ""}, ""),
    ({}, "Renovatio Solar"),
])
def test_finance_stage_owner_review_corrects_or_clears_s2_but_old_responses_preserve_it(monkeypatch, registry, merged, review, expected):
    if merged:
        monkeypatch.setenv("S01B_MERGE_S34", "1")
    else:
        monkeypatch.delenv("S01B_MERGE_S34", raising=False)
    class OwnerReviewStub(PipelineStub):
        def complete(self, prompt, *, stage, **kwargs):
            value = super().complete(prompt, stage=stage, **kwargs)
            if stage == "s2":
                value["projects"][0]["common_info"].update({"Project Developer": "Renovatio Solar", "Project Developer Evidence": "完整EPC由Renovatio Solar负责"})
            if stage in {"s3", "s34"}:
                value.update(deepcopy(review))
            return value
    stub = OwnerReviewStub({"chinese_entities": [candidate()]})
    project = extract_staged("甲项目", SOURCE, "2026-09-23", llm=stub)["projects"][0]
    assert project["publication_status"] == "eligible"
    ci = project["common_info"]
    assert ci["Project Developer"] == expected
    if review:
        assert ci["project_developer_review_original"]["Project Developer"] == "Renovatio Solar"
        assert ci["project_developer_review_source"] == ("s34" if merged else "s3")
        assert ci["Project Developer Evidence"] == (review["Project Developer Evidence"] or "")
    else:
        assert "project_developer_review_original" not in ci


@pytest.mark.parametrize("stage", ["s3", "s34"])
@pytest.mark.parametrize("field,value", [("Project Developer", {"name": "Owner"}), ("Project Developer Evidence", ["quote"])])
def test_owner_review_rejects_structurally_wrong_values(stage, field, value):
    payload = {"chinese_entities": [], field: value}
    with pytest.raises(EntityResponseError):
        if stage == "s3":
            run_s3("甲项目", SOURCE, "甲项目", llm=Stub(payload))
        else:
            run_s34("甲项目", SOURCE, "甲项目", "光伏", llm=Stub(payload))

@pytest.mark.parametrize("scope", ["unknown", "project"])
def test_null_capacity_rationale_does_not_fabricate_affirmative_generation_scope(scope):
    evidence = {"quote": "甲项目容量100MW。", "scope": scope, "rationale": None, "evidence_quotes": []}
    ci = capacity("甲项目容量100MW。", **{"Capacity Evidence": deepcopy(evidence)})
    assert ci["capacity_mw_parsed"] is None
    assert ci["Capacity (MW)"] == ""
    assert ci["Capacity Evidence"] == evidence
    assert ci["capacity_rejected_values"]["capacity_mw"]["raw"] == "100MW"


def test_nonstring_nonnull_capacity_rationale_still_fails_structure_validation():
    with pytest.raises(EntityResponseError):
        capacity("甲项目容量100MW。", **{"Capacity Evidence": {"quote": None, "scope": "unknown", "rationale": []}})
