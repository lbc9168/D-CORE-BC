"""Verify the immutable Stage 4E selection, publication, and fifty-article review queue."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import gold_standard_studio.app as app_module
from gold_standard_studio.candidate_contract import (
    build_stage4f_run_manifest,
    validate_candidate_payload,
)
from gold_standard_studio.draft_store import DraftStore
from gold_standard_studio.pilot_manifest import PILOT_BY_ID
from gold_standard_studio.review_cohorts import REVIEW_BY_ID, review_article_ids
from gold_standard_studio.single_article import EXTRACTION_DB, MAIN_DB, RAW_DB, load_article
from gold_standard_studio.stage4e_manifest import (
    COHORT_VERSION,
    STAGE4E_ARTICLES,
    build_stage4e_cohort,
    stage4e_article_ids,
    validate_selection_contract,
    verify_stage4e_cohort,
)


def _sha256(path: Path) -> str:
    """Hash one database to prove Stage 4E selection remains read-only."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_stage4e_static_selection_matches_the_saved_plan() -> None:
    """Keep forty unique non-Pilot articles in exact split, stratum, family, and blind quotas."""
    contract = validate_selection_contract()
    assert contract == {
        "article_count": 40,
        "split_counts": {"development": 24, "challenge": 16},
        "stratum_counts": {
            "core_balanced": 10,
            "s01b_complex": 8,
            "s02a_family": 8,
            "s02b_s03_normalization": 8,
            "boundary_gate": 6,
        },
        "family_count": 4,
        "blind_count": 0,
    }
    assert set(stage4e_article_ids()).isdisjoint(PILOT_BY_ID)
    families = {
        item["family_id"] for item in STAGE4E_ARTICLES if item["family_id"]
    }
    assert families == {
        "UZB-PV-GIZHAK-4E", "IDN-HY-JATIGEDE-4E",
        "GBR-ES-MENDI-4E", "BIH-BOUNDARY-U-4E",
    }


def test_real_stage4e_cohort_publishes_atomically_without_source_writes(tmp_path) -> None:
    """Publish and verify the real cohort once while preserving all three source database hashes."""
    before = {path: _sha256(path) for path in (RAW_DB, EXTRACTION_DB, MAIN_DB)}
    target = build_stage4e_cohort(tmp_path)
    manifest = verify_stage4e_cohort(target)
    records = [json.loads(line) for line in (target / "articles.jsonl").read_text(encoding="utf-8").splitlines()]

    assert target.name == COHORT_VERSION
    assert len(records) == 40
    assert records[0]["article_id"] == 23627
    assert records[-1]["article_id"] == 27434
    assert all(record["selection_signal_is_gold"] is False for record in records)
    assert manifest["selection_policy"]["blind_holdout_included"] is False
    with pytest.raises(FileExistsError, match="拒绝覆盖"):
        build_stage4e_cohort(tmp_path)
    assert before == {path: _sha256(path) for path in (RAW_DB, EXTRACTION_DB, MAIN_DB)}


def test_review_queue_combines_pilot_and_stage4e_with_visible_agent_prefill(tmp_path, monkeypatch) -> None:
    """Expose fifty grouped articles and prefill Stage 4E only from validated Terra candidates."""
    local_store = DraftStore(tmp_path / "review.db")
    monkeypatch.setattr(app_module, "store", local_store)
    client = TestClient(app_module.app)

    assert len(review_article_ids()) == 50
    assert len(REVIEW_BY_ID) == 50
    queue = client.get("/queue")
    assert queue.status_code == 200
    assert "50 篇真实样本" in queue.text
    assert "development 34" in queue.text
    assert "challenge 16" in queue.text
    assert "blind 0" in queue.text

    page = client.get("/review/1380")
    state = client.get("/api/review/1380").json()
    assert page.status_code == 200
    assert state["pilot"]["primary_stratum"] == "boundary_gate"
    assert state["draft"]["payload"]["article_status"] == "no_chinese"
    assert state["draft"]["payload"]["projects"] == []
    assert "provider" in state["candidate_sources"]

    challenge = client.get("/api/review/166").json()
    assert challenge["candidates_hidden"] is False
    assert "pipeline" in challenge["candidate_sources"]
    assert challenge["historical_extraction_projects"]
    assert challenge["draft"]["payload"]["article_status"] == "valid"
    assert challenge["draft"]["payload"]["projects"]


def test_prompt_pack_accepts_stage4e_manifest_article(tmp_path, monkeypatch) -> None:
    """Render production S01B base prompts for Stage 4E instead of hard-coding Pilot IDs."""
    monkeypatch.setattr("gold_standard_studio.prompt_pack.RUNS_DIR", tmp_path)
    from gold_standard_studio.prompt_pack import prepare_base

    result = prepare_base(23627)
    assert result["cohort"] == COHORT_VERSION
    assert result["split"] == "development"
    assert result["provider_model"] == "gpt-5.6-terra"
    assert (tmp_path / "23627" / "s1.prompt.txt").is_file()


def test_all_stage4e_articles_load_from_fixed_readonly_sources() -> None:
    """Load every selected article and match its raw ID without mutating source data."""
    for article_id in stage4e_article_ids():
        context = load_article(article_id)
        assert context["article"]["id"] == article_id
        assert context["article"]["article_sha256"]


def test_candidate_contract_requires_evidence_only_for_present_facts() -> None:
    """Accept an evidence-free absence but reject an evidence-free present candidate fact."""
    article = load_article(23627)["article"]
    quote = article["title"]
    payload = {
        "candidate_id": "terra-provider-23627-contract-test",
        "execution_role": "provider",
        "model": "gpt-5.6-terra",
        "article_id": 23627,
        "article_sha256": article["article_sha256"],
        "article_status": {
            "value_state": "present", "value": "other_non_project",
            "evidence": quote, "confidence": 0.5,
        },
        "non_project_reason": {
            "value_state": "not_mentioned", "value": None,
            "evidence": "", "confidence": 0.5,
        },
        "projects": [],
        "prompt_manifest": {"renderer": "contract-test"},
        "stage_outputs": {"s1": {"status": "other_non_project"}},
    }
    assert validate_candidate_payload(payload, "provider") == []
    payload["article_status"]["evidence"] = ""
    assert any("present 事实缺少原文证据" in error
               for error in validate_candidate_payload(payload, "provider"))


def test_provider_contract_rejects_s1_final_status_drift() -> None:
    """Reject a Provider that short-circuits after S1 already selected the valid-project branch."""
    article = load_article(23627)["article"]
    payload = {
        "candidate_id": "terra-provider-23627-status-drift-test",
        "execution_role": "provider",
        "model": "gpt-5.6-terra",
        "article_id": 23627,
        "article_sha256": article["article_sha256"],
        "article_status": {"value_state": "present", "value": "other_non_project",
                           "evidence": "", "confidence": 0.5},
        "non_project_reason": {"value_state": "present", "value": "项目管线",
                               "evidence": "project pipeline", "confidence": 0.5},
        "projects": [],
        "prompt_manifest": {"renderer": "contract-test"},
        "stage_outputs": {"s1": {"status": "valid_project"}},
    }
    errors = validate_candidate_payload(payload, "provider")
    assert any("最终 article_status=other_non_project" in error for error in errors)


def test_complete_stage4f_candidate_pairs_publish_one_manifest(tmp_path) -> None:
    """Record all forty validated Terra/Sol pairs and refuse to overwrite the run manifest."""
    output = tmp_path / "stage4f-run.json"
    manifest = build_stage4f_run_manifest(output)
    assert manifest["article_count"] == 40
    assert manifest["records"][0]["provider"]["model"] == "gpt-5.6-terra"
    assert manifest["records"][0]["reviewer"]["model"] == "gpt-5.6-sol"
    with pytest.raises(FileExistsError, match="拒绝覆盖"):
        build_stage4f_run_manifest(output)
