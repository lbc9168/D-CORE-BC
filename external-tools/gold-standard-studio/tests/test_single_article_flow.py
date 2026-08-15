"""Exercise only the real article 9027 source and its local draft/confirmation path."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import gold_standard_studio.app as app_module
from gold_standard_studio.draft_store import REQUIRED_FIELDS, DraftStore, validate_for_confirmation
from gold_standard_studio.pilot_manifest import PILOT_BY_ID, pilot_article_ids
from gold_standard_studio.single_article import ARTICLE_ID, MAIN_DB, RAW_DB, load_article, load_single_article


def _sha256(path: Path) -> str:
    """Hash a source database to prove the prototype leaves its file bytes unchanged."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_real_article_source_has_two_projects_and_is_read_only() -> None:
    """Load article 9027 and verify two canonical projects without changing source files."""
    before = {path: _sha256(path) for path in (RAW_DB, MAIN_DB)}
    context = load_single_article()
    assert context["article"]["id"] == ARTICLE_ID
    assert context["article"]["article_sha256"] == "664c4e4136556e3cc5b009763f6036e1506e2925eed12bd374546b3004aa700f"
    assert len(context["database_projects"]) == 2
    assert len(context["historical_extraction_projects"]) == 2
    assert all(project["cross_source_aggregate"] for project in context["database_projects"])
    assert before == {path: _sha256(path) for path in (RAW_DB, MAIN_DB)}


def test_lenient_draft_refresh_and_strict_confirmation(tmp_path, monkeypatch) -> None:
    """Autosave an incomplete real-article draft, reload it, and defer validation until confirmation."""
    local_store = DraftStore(tmp_path / "review.db")
    monkeypatch.setattr(app_module, "store", local_store)
    client = TestClient(app_module.app)

    page = client.get(f"/review/{ARTICLE_ID}")
    assert page.status_code == 200
    assert "中国在海外投资的单体规模最大电化学储能项目开工" in page.text
    assert "数据库聚合" in page.text or "bootstrap" in page.text
    assert "energy_taxonomy_v2" in page.text

    incomplete = {"article_status": "valid", "projects": [{"gold_project_id": "P01", "project_name": "临时修改", "fields": {}}]}
    saved = client.put(f"/api/review/{ARTICLE_ID}/draft", json=incomplete)
    assert saved.status_code == 200
    refreshed = client.get(f"/api/review/{ARTICLE_ID}").json()
    assert refreshed["draft"]["payload"]["projects"][0]["project_name"] == "临时修改"

    rejected = client.post(f"/api/review/{ARTICLE_ID}/confirm")
    assert rejected.status_code == 422
    assert "缺少字段" in rejected.json()["detail"]


def test_confirmed_work_copy_requires_explicit_reopen(tmp_path) -> None:
    """Lock a confirmed draft, reopen it explicitly, and preserve both workflow actions."""
    local_store = DraftStore(tmp_path / "review.db")
    local_store.seed_article(1, "fixed-hash", {"article": {"content": "真实项目"}})
    fields = {
        name: {"value_state": "not_mentioned", "value": None, "evidence": "", "review_action": "confirmed"}
        for name in REQUIRED_FIELDS
    }
    fields["project_name"] = {
        "value_state": "present", "value": "真实项目", "evidence": "真实项目", "review_action": "confirmed"
    }
    payload = {"article_status": "valid", "projects": [
        {"gold_project_id": "P01", "project_name": "真实项目", "fields": fields}
    ]}
    local_store.save_draft(1, payload)
    assert local_store.confirm(1)["confirmed"] is True
    with pytest.raises(ValueError, match="先重新打开复核"):
        local_store.save_draft(1, payload)

    reopened = local_store.reopen(1)
    assert reopened["confirmed"] is False
    payload["projects"][0]["relation_note"] = "重新检查"
    local_store.save_draft(1, payload)
    assert local_store.confirm(1)["confirmed"] is True
    assert [action["action"] for action in local_store.list_actions(1)] == [
        "confirmed", "reopened", "confirmed"
    ]


def test_title_is_a_valid_verbatim_evidence_source(tmp_path) -> None:
    """Confirm a present fact whose exact quote appears only in the immutable article title."""
    local_store = DraftStore(tmp_path / "title-evidence.db")
    local_store.seed_article(
        1, "fixed-hash", {"article": {"title": "标题中的真实项目", "content": "正文没有项目名称。"}}
    )
    fields = {
        name: {"value_state": "not_mentioned", "value": None, "evidence": "",
               "review_action": "confirmed"}
        for name in REQUIRED_FIELDS
    }
    fields["project_name"] = {
        "value_state": "present", "value": "标题中的真实项目",
        "evidence": "标题中的真实项目", "review_action": "confirmed",
    }
    payload = {"article_status": "valid", "projects": [
        {"gold_project_id": "P01", "project_name": "标题中的真实项目", "fields": fields}
    ]}
    local_store.save_draft(1, payload)
    assert local_store.confirm(1)["confirmed"] is True


def test_stage3_queue_and_negative_article_are_real_and_isolated(tmp_path, monkeypatch) -> None:
    """Render all fixed Pilot titles and initialize a true irrelevant article without projects."""
    local_store = DraftStore(tmp_path / "pilot-review.db")
    monkeypatch.setattr(app_module, "store", local_store)
    client = TestClient(app_module.app)

    assert len(pilot_article_ids()) == 10
    assert set(pilot_article_ids()) == set(PILOT_BY_ID)
    queue = client.get("/queue")
    assert queue.status_code == 200
    assert "10 篇真实样本" in queue.text
    assert "华友 2.1 亿美元全现金收购加纳首个锂矿" in queue.text
    assert "没有政策性银行撑腰" in queue.text

    article = load_article(27309)
    assert article["article"]["id"] == 27309
    page = client.get("/review/27309")
    assert page.status_code == 200
    state = client.get("/api/review/27309").json()
    assert state["draft"]["payload"]["article_status"] == "irrelevant"
    assert state["draft"]["payload"]["projects"] == []
    assert client.get("/review/999999").status_code == 404


def test_policy_does_not_treat_generation_mwp_as_storage_power(tmp_path, monkeypatch) -> None:
    """Keep 95MWp generation separate from 218MWh storage in the real complex Pilot article."""
    monkeypatch.setattr(app_module, "store", DraftStore(tmp_path / "policy-review.db"))
    context = app_module.build_review_context(1533)
    first = context["candidate_sources"]["policy"][0]
    assert first["generation_sources"]["value"] == ["光伏"]
    assert first["storage_status"]["value"] == "confirmed"
    assert first["storage_power_mw"]["value"] is None
    assert first["storage_energy_mwh"]["value"] == 218.0
    assert first["category"]["value"] == "光伏"


def test_historical_adapter_does_not_inject_baseline_article_storage_values() -> None:
    """Never reuse article 9027's 300MWh prototype fallback for unrelated stored Pipeline rows."""
    manufacturing = load_article(1586)["historical_extraction_projects"][0]
    wind = load_article(13649)["historical_extraction_projects"][0]
    assert manufacturing["storage_power_mw"] is None
    assert manufacturing["storage_energy_mwh"] is None
    assert wind["storage_power_mw"] is None
    assert wind["storage_energy_mwh"] is None
    assert wind["generation_capacity_mw"] == "300"


def test_real_entity_and_normalization_layers_are_separate(tmp_path, monkeypatch) -> None:
    """Keep S01B's 三一硅能 mention separate from S02B's 三一集团 parent mapping."""
    monkeypatch.setattr(app_module, "store", DraftStore(tmp_path / "layered-review.db"))
    context = app_module.build_review_context(1533)
    provider = context["candidate_sources"]["provider"][0]
    pipeline = context["candidate_sources"]["pipeline"][0]
    draft_fields = context["draft"]["payload"]["projects"][0]["fields"]

    assert provider["entities"]["value"] == [{"name": "三一硅能", "role": "股权投资方"}]
    assert "normalized_entities" not in provider
    assert pipeline["entities"]["value"] == [{"name": "三一硅能", "role": "股权投资方"}]
    assert pipeline["normalized_entities"]["value"] == [{
        "mentioned_name": "三一硅能", "canonical_entity": "三一硅能",
        "parent_company": "三一集团有限公司", "role": "股权投资方",
    }]
    assert draft_fields["normalized_entities"]["value"] == pipeline["normalized_entities"]["value"]


def test_normalized_location_prefers_norm_and_falls_back_to_raw_location() -> None:
    """Treat unchanged raw locations as present S02B-2 values while preferring an explicit mapping."""
    mapped = app_module._normalize_database_project({"location": "安集延", "location_norm": "安集延州"})
    unchanged = app_module._normalize_database_project({"location": "苏格兰", "location_norm": None})
    historical = app_module._normalize_historical_project({"location": "达尔汗", "normalized_location": None})

    assert mapped["normalized_location"]["value"] == "安集延州"
    assert unchanged["normalized_location"]["value"] == "苏格兰"
    assert historical["normalized_location"]["value"] == "达尔汗"
    assert all(item["normalized_location"]["value_state"] == "present" for item in (mapped, unchanged, historical))


def test_layered_upgrade_repairs_legacy_empty_location_but_preserves_manual_edit(monkeypatch) -> None:
    """Reset an old empty prefill for re-review without overwriting a manually modified standard place."""
    default_field = {
        "value_state": "present", "value": "苏格兰", "evidence": "",
        "confidence": None, "review_action": "unreviewed",
    }
    monkeypatch.setattr(
        app_module,
        "_initial_draft",
        lambda context, artifacts: {"projects": [{"fields": {
            "normalized_location": default_field,
            "normalized_entities": {"value_state": "not_mentioned", "value": None,
                                    "evidence": "", "review_action": "unreviewed"},
        }}]},
    )
    legacy = {"payload": {"projects": [{"fields": {
        "normalized_location": {"value_state": "not_mentioned", "value": None,
                                "evidence": "", "review_action": "confirmed", "accepted_source": "pipeline"},
        "normalized_entities": {"value_state": "not_mentioned", "value": None,
                                "evidence": "", "review_action": "confirmed"},
    }}]}}
    assert app_module._upgrade_layered_draft({}, [], legacy) is True
    assert legacy["payload"]["projects"][0]["fields"]["normalized_location"] == default_field

    manual = {"payload": {"projects": [{"fields": {
        "normalized_location": {"value_state": "present", "value": "Highlands",
                                "evidence": "", "review_action": "modified"},
        "normalized_entities": {"value_state": "not_mentioned", "value": None,
                                "evidence": "", "review_action": "confirmed"},
    }}]}}
    assert app_module._upgrade_layered_draft({}, [], manual) is False
    assert manual["payload"]["projects"][0]["fields"]["normalized_location"]["value"] == "Highlands"


def test_reopen_endpoint_returns_persisted_upgrade_revision(monkeypatch) -> None:
    """Persist safe layered upgrades during reopen so immediate confirmation cannot use stale DB JSON."""
    class ReopenStore:
        """Provide the minimal store behavior needed to verify reopen sequencing."""

        def reopen(self, article_id: int) -> dict:
            """Record the explicit reopen transition before context rebuilding."""
            assert article_id == 9027
            return {"confirmed": False, "revision": 31, "updated_at": "first"}

    monkeypatch.setattr(app_module, "store", ReopenStore())
    monkeypatch.setattr(
        app_module,
        "build_review_context",
        lambda article_id: {"draft": {"confirmed": False, "revision": 32, "updated_at": "upgraded"}},
    )

    assert app_module.reopen_article(9027) == {
        "ok": True, "confirmed": False, "revision": 32, "updated_at": "upgraded",
    }


def test_derived_normalization_does_not_require_article_substring_evidence() -> None:
    """Require article evidence for S01B facts but not deterministic S02B normalization outputs."""
    fields = {
        name: {"value_state": "not_mentioned", "value": None, "evidence": "", "review_action": "confirmed"}
        for name in REQUIRED_FIELDS
    }
    fields["project_name"] = {
        "value_state": "present", "value": "真实项目", "evidence": "真实项目", "review_action": "confirmed"
    }
    fields["entities"] = {
        "value_state": "present", "value": [{"name": "三一硅能", "role": "投资方"}],
        "evidence": "三一硅能", "review_action": "confirmed",
    }
    fields["normalized_entities"] = {
        "value_state": "present",
        "value": [{"canonical_entity": "三一硅能", "parent_company": "三一集团有限公司"}],
        "evidence": "", "review_action": "confirmed",
    }
    payload = {"article_status": "valid", "projects": [
        {"gold_project_id": "P01", "project_name": "真实项目", "fields": fields}
    ]}

    assert validate_for_confirmation(payload, "三一硅能参与真实项目") == []
    fields["entities"]["evidence"] = ""
    assert "entities 缺少原文证据" in "；".join(validate_for_confirmation(payload, "三一硅能参与真实项目"))


def test_multiple_entity_evidence_fragments_validate_independently() -> None:
    """Allow one entities field to cite separate exact snippets for multiple Chinese companies."""
    fields = {
        name: {"value_state": "not_mentioned", "value": None, "evidence": "", "review_action": "confirmed"}
        for name in REQUIRED_FIELDS
    }
    fields["project_name"] = {
        "value_state": "present", "value": "真实项目", "evidence": "真实项目", "review_action": "confirmed"
    }
    fields["entities"] = {
        "value_state": "present",
        "value": [{"name": "中国电建", "role": "EPC"}, {"name": "东方电气", "role": "设备供应"}],
        "evidence": "中国电建负责EPC；东方电气供应设备", "review_action": "confirmed",
    }
    payload = {"article_status": "valid", "non_project_reason": "",
               "projects": [{"project_name": "真实项目", "fields": fields}]}
    content = "中国电建负责EPC，并推进真实项目。东方电气供应设备。"
    assert validate_for_confirmation(payload, content) == []


def test_absent_and_unknown_candidate_states_do_not_require_invented_evidence() -> None:
    """Map model absence and uncertainty to non-present Gold states that need no quote."""
    content = "文章只说明项目存在。"
    project = {
        "project_name": {"value_state": "explicit", "value": "项目", "evidence": "项目"},
        "financing_mode": {"value_state": "not_mentioned", "value": None, "evidence": ""},
        "planned_start_date": {"value_state": "unknown", "value": None, "evidence": ""},
        "storage_status": {"value_state": "extracted", "value": "not_mentioned", "evidence": ""},
    }
    normalized = app_module._normalize_agent_project(project, "provider", content)
    assert normalized["financing_mode"]["value_state"] == "not_mentioned"
    assert normalized["planned_start_date"]["value_state"] == "uncertain"
    assert normalized["storage_status"]["value_state"] == "not_mentioned"


def test_agent_entity_name_envelope_preserves_name_and_evidence() -> None:
    """Accept the Reviewer-style name envelope without dropping the Chinese entity or its quote."""
    project = {
        "project_name": {"value_state": "explicit", "value": "项目", "evidence": "项目"},
        "chinese_entities": [{
            "name": {"value_state": "explicit", "value": "CHINT", "evidence": "CHINT"},
            "role": {"value_state": "extracted", "value": "共同开发方",
                     "evidence": "co-develop", "confidence": 0.9},
        }],
    }
    normalized = app_module._normalize_agent_project(project, "provider", "CHINT co-develop 项目")
    assert normalized["entities"]["value"] == [{"name": "CHINT", "role": "共同开发方"}]
    assert normalized["entities"]["evidence"] == "CHINT"
    assert normalized["storage_status"]["value"] is None
