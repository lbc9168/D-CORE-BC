"""Test storage-taxonomy campaign selection, evidence validation, and guarded apply behavior."""

from __future__ import annotations

import hashlib
import json
import sqlite3

from scripts.storage_taxonomy_campaign import (
    CAMPAIGN_VERSION, DECISION_SCHEMA_VERSION, apply_compiled, compile_dry_run,
    enumerate_candidates, select_risk_reviews, validate_decision,
)


def _main_db(path):
    """Create a compact main database with hybrid, storage-signal, and excluded controls."""
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE canonical_projects (
          id TEXT PRIMARY KEY, project_name TEXT, category TEXT, subcategory TEXT, excluded INTEGER,
          technology_tags_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE project_events (
          id TEXT PRIMARY KEY, canonical_id TEXT, capacity_details TEXT, project_summary TEXT,
          capacity_mw REAL, capacity_mw_raw TEXT, capacity_source TEXT, pv_capacity_mw REAL,
          storage_power_mw REAL, storage_energy_mwh REAL, source_links TEXT,
          generation_sources_json TEXT, storage_status TEXT, storage_power_mw_raw TEXT,
          storage_energy_mwh_raw TEXT, energy_composition_evidence TEXT
        );
        CREATE TABLE project_classification_shadow (
          canonical_id TEXT PRIMARY KEY, generation_sources_json TEXT NOT NULL DEFAULT '[]',
          storage_status TEXT NOT NULL DEFAULT 'not_mentioned', storage_power_mw REAL,
          storage_energy_mwh REAL, proposed_category TEXT, technology_tags_json TEXT NOT NULL DEFAULT '[]',
          classification_version TEXT NOT NULL, needs_review INTEGER NOT NULL DEFAULT 0,
          review_reason TEXT, classification_source TEXT NOT NULL DEFAULT 'policy',
          manual_locked INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
        );
        INSERT INTO canonical_projects VALUES ('HB','plain','综合能源','光储',0,'[]');
        INSERT INTO canonical_projects VALUES ('PV','电池项目','光伏',NULL,0,'[]');
        INSERT INTO canonical_projects VALUES ('NO','plain','光伏',NULL,0,'[]');
        INSERT INTO canonical_projects VALUES ('EX','储能','光伏',NULL,1,'[]');
        INSERT INTO project_events VALUES ('HB-01','HB','','',NULL,NULL,NULL,NULL,NULL,NULL,'[]',NULL,NULL,NULL,NULL,NULL);
        INSERT INTO project_events VALUES ('PV-01','PV','','',NULL,NULL,NULL,NULL,NULL,NULL,'[]',NULL,NULL,NULL,NULL,NULL);
        INSERT INTO project_events VALUES ('NO-01','NO','','',NULL,NULL,NULL,NULL,NULL,NULL,'[]',NULL,NULL,NULL,NULL,NULL);
        INSERT INTO project_events VALUES ('EX-01','EX','','',NULL,NULL,NULL,NULL,1,NULL,'[]',NULL,NULL,NULL,NULL,NULL);
    """)
    connection.commit(); connection.close()


def _package_and_decision():
    """Return one internally consistent package and Terra decision fixture."""
    package = {"project_id": "HB", "events": [{"event": {"id": "HB-01"}, "articles": [{
        "url": "https://x", "title": "100MW光伏+20MW储能", "content": "项目已经投运。"
    }]}]}
    package_hash = "a" * 64
    decision = {
        "schema_version": DECISION_SCHEMA_VERSION, "campaign_version": CAMPAIGN_VERSION,
        "project_id": "HB", "package_sha256": package_hash, "role": "terra", "model": "gpt-5.6-terra",
        "resolution": "resolved", "confidence": 0.9, "review_reason": None,
        "canonical_decision": {"generation_sources": ["光伏"], "storage_status": "confirmed",
            "storage_power_mw": 20, "storage_energy_mwh": None, "proposed_category": "光伏",
            "proposed_subcategory": None, "technology_tags": ["光储"],
            "evidence": [{"url": "https://x", "surface": "title", "quote": "光伏+20MW储能"}]},
        "event_decisions": [{"event_id": "HB-01", "generation_sources": ["光伏"],
            "storage_status": "confirmed", "storage_power_mw": 20, "storage_energy_mwh": None,
            "storage_power_mw_raw": "20MW", "storage_energy_mwh_raw": None,
            "energy_composition_evidence": [{"url": "https://x", "surface": "title", "quote": "100MW光伏"}]}],
    }
    return package, package_hash, decision


def test_candidate_scope_uses_exact_approved_union(tmp_path):
    """Selection includes active hybrid and non-hybrid text/numeric signals but never excluded rows."""
    database = tmp_path / "main.db"; _main_db(database)
    assert [item["canonical"]["id"] for item in enumerate_candidates(database)] == ["HB", "PV"]


def test_title_evidence_is_valid_but_cross_surface_or_nonconfirmed_values_are_rejected():
    """Quotes may use title or body independently and planned storage cannot carry capacity."""
    package, package_hash, decision = _package_and_decision()
    assert validate_decision(decision, package, package_hash) == []
    decision["canonical_decision"]["evidence"][0]["quote"] = "储能项目已经"
    assert any(error.startswith("evidence_not_exact") for error in validate_decision(decision, package, package_hash))
    decision["canonical_decision"]["evidence"] = []
    decision["event_decisions"][0]["storage_status"] = "planned"
    assert "nonconfirmed_event_storage_value:HB-01" in validate_decision(decision, package, package_hash)
    assert "nonconfirmed_event_storage_raw:HB-01" in validate_decision(decision, package, package_hash)


def test_deterministic_policy_rejects_model_category_or_tag_override():
    """模型不得把光储事实改回综合能源，也不得遗漏确定性光储标签。"""
    package, package_hash, decision = _package_and_decision()
    decision["canonical_decision"]["proposed_category"] = "综合能源"
    decision["canonical_decision"]["technology_tags"] = []
    errors = validate_decision(decision, package, package_hash)
    assert "deterministic_category" in errors
    assert "deterministic_tags" in errors


def test_factory_capacity_and_data_storage_are_never_energy_storage_facts():
    """电池工厂年产能和数据中心的“数据存储”不能通过储能事实门禁。"""
    package, package_hash, decision = _package_and_decision()
    package["canonical"] = {"project_name": "巴西1.5GWh储能厂项目", "category": "储能"}
    package["events"][0]["articles"][0].update({
        "title": "储能工厂投产",
        "content": "该电池储能系统制造基地年产能1.5GWh。",
    })
    decision["canonical_decision"].update({
        "generation_sources": [], "storage_status": "confirmed", "storage_power_mw": None,
        "storage_energy_mwh": 1500, "proposed_category": "储能", "technology_tags": [],
        "evidence": [{"url": "https://x", "surface": "body", "quote": "电池储能系统制造基地年产能1.5GWh"}],
    })
    decision["event_decisions"][0].update({
        "generation_sources": [], "storage_status": "confirmed", "storage_power_mw": None,
        "storage_energy_mwh": 1500, "storage_power_mw_raw": None,
        "storage_energy_mwh_raw": "1.5GWh",
        "energy_composition_evidence": [
            {"url": "https://x", "surface": "body", "quote": "电池储能系统制造基地年产能1.5GWh"}
        ],
    })
    errors = validate_decision(decision, package, package_hash)
    assert "manufacturing_project_cannot_be_confirmed_storage" in errors
    assert "non_project_measurement_context:HB-01:storage_energy_mwh_raw" in errors

    package["canonical"]["project_name"] = "西班牙工商业储能项目"
    package["events"][0]["articles"][0]["content"] = "政府计划到2030年部署20GWh储能项目。"
    decision["event_decisions"][0]["storage_energy_mwh_raw"] = "20GWh"
    errors = validate_decision(decision, package, package_hash)
    assert "non_project_measurement_context:HB-01:storage_energy_mwh_raw" in errors

    package["canonical"]["project_name"] = "泰国数据中心项目"
    package["events"][0]["articles"][0]["content"] = "建成后提升区域的数据处理与存储能力。"
    decision["canonical_decision"]["storage_energy_mwh"] = None
    decision["canonical_decision"]["evidence"] = [
        {"url": "https://x", "surface": "body", "quote": "数据处理与存储能力"}
    ]
    decision["event_decisions"][0].update({
        "storage_energy_mwh": None, "storage_energy_mwh_raw": None,
        "energy_composition_evidence": [
            {"url": "https://x", "surface": "body", "quote": "数据处理与存储能力"}
        ],
    })
    errors = validate_decision(decision, package, package_hash)
    assert "data_storage_not_energy_storage" in errors


def test_apply_requires_hash_gate_then_is_idempotent(tmp_path):
    """Apply creates a backup, updates in one pass, and safely recognizes an exact repeat."""
    database = tmp_path / "main.db"; _main_db(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE project_events SET capacity_details='20MWh',capacity_mw=20,"
            "capacity_mw_raw='20',capacity_source='legacy' WHERE id='HB-01'"
        )
    compiled = tmp_path / "compiled"; compiled.mkdir()
    _package, _hash, decision = _package_and_decision()
    decision["canonical_decision"].update({"storage_power_mw": None, "storage_energy_mwh": 20})
    decision["event_decisions"][0].update({
        "storage_power_mw": None, "storage_energy_mwh": 20,
        "storage_power_mw_raw": None, "storage_energy_mwh_raw": "20MWh",
    })
    changes = [{"project_id": "HB", "decision_role": "sol", "decision_model": "gpt-5.6-sol",
        "decision_sha256": "b" * 64,
        "before": {"id": "HB", "category": "综合能源", "subcategory": "光储"},
        "after": {"category": "光伏", "subcategory": None, "technology_tags_json": '["光储"]'},
        "canonical_decision": decision["canonical_decision"], "event_decisions": decision["event_decisions"]}]
    changes_path = compiled / "changes.json"
    changes_path.write_text(json.dumps(changes, ensure_ascii=False), encoding="utf-8")
    digest = hashlib.sha256(changes_path.read_bytes()).hexdigest()
    unresolved_path = compiled / "unresolved.json"
    unresolved_path.write_text("[]", encoding="utf-8")
    unresolved_digest = hashlib.sha256(unresolved_path.read_bytes()).hexdigest()
    db_hash = hashlib.sha256(database.read_bytes()).hexdigest()
    (compiled / "apply-plan.json").write_text(json.dumps({"apply_allowed": True, "exception_count": 0,
        "blocking_exception_count": 0, "main_db_sha256": db_hash,
        "changes_sha256": digest, "unresolved_sha256": unresolved_digest}), encoding="utf-8")
    first = apply_compiled(database, compiled, CAMPAIGN_VERSION)
    second = apply_compiled(database, compiled, CAMPAIGN_VERSION)
    assert first["updated_projects"] == 1 and first["backup"]
    assert second == {
        "updated_projects": 0, "audited_unresolved": 0,
        "backup": None, "idempotent": True,
    }

    connection = sqlite3.connect(database)
    shadow = connection.execute(
        "SELECT proposed_category,technology_tags_json,classification_version,classification_source "
        "FROM project_classification_shadow WHERE canonical_id='HB'"
    ).fetchone()
    event = connection.execute(
        "SELECT capacity_mw,capacity_mw_raw,capacity_source,storage_energy_mwh "
        "FROM project_events WHERE id='HB-01'"
    ).fetchone()
    connection.close()
    assert shadow == ("光伏", '["光储"]', "energy_taxonomy_v2", f"sol:gpt-5.6-sol:{'b' * 64}")
    assert event == (None, None, None, 20.0)


def test_risk_selector_records_category_change_and_parent_hash(tmp_path):
    """Sol 风险清单必须覆盖主类别变化，并给出可绑定的 Terra 决策哈希。"""
    campaign = tmp_path / "campaign"
    (campaign / "packages").mkdir(parents=True)
    (campaign / "terra-decisions").mkdir()
    package, package_hash, decision = _package_and_decision()
    package["canonical"] = {"category": "综合能源", "subcategory": "光储"}
    (campaign / "packages" / "HB.json").write_text(
        json.dumps(package, ensure_ascii=False), encoding="utf-8"
    )
    (campaign / "terra-decisions" / "HB.json").write_text(
        json.dumps(decision, ensure_ascii=False), encoding="utf-8"
    )
    manifest = {"packages": [{
        "project_id": "HB", "path": "packages/HB.json", "sha256": package_hash,
        "category": "综合能源", "selection_reasons": ["active_hybrid_category"],
        "event_count": 1, "article_count": 1,
    }]}
    (campaign / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    result = select_risk_reviews(campaign, campaign / "sol-risk-manifest.json")
    assert result["risk_count"] == 1
    assert "main_category_change" in result["items"][0]["reasons"]
    assert len(result["items"][0]["terra_decision_sha256"]) == 64


def test_compile_allows_valid_sol_to_supersede_invalid_risk_terra(tmp_path):
    """风险项目的有效 Sol 可纠正 Terra 合同冲突，但仍须保留非阻断审计记录。"""
    campaign = tmp_path / "campaign"
    (campaign / "packages").mkdir(parents=True)
    (campaign / "terra-decisions").mkdir()
    (campaign / "sol-decisions").mkdir()
    database = tmp_path / "main.db"
    _main_db(database)
    package, package_hash, terra = _package_and_decision()
    package["canonical"] = {"category": "综合能源", "subcategory": "光储"}
    terra["canonical_decision"]["proposed_category"] = "综合能源"
    terra_bytes = (json.dumps(
        terra, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n").encode()
    terra_hash = hashlib.sha256(terra_bytes).hexdigest()
    sol = json.loads(json.dumps(terra, ensure_ascii=False))
    sol.update({"role": "sol", "model": "gpt-5.6-sol", "parent_decision_sha256": terra_hash})
    sol["canonical_decision"]["proposed_category"] = "光伏"
    (campaign / "packages" / "HB.json").write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
    (campaign / "terra-decisions" / "HB.json").write_text(json.dumps(terra, ensure_ascii=False), encoding="utf-8")
    (campaign / "sol-decisions" / "HB.json").write_text(json.dumps(sol, ensure_ascii=False), encoding="utf-8")
    manifest = {"packages": [{
        "project_id": "HB", "path": "packages/HB.json", "sha256": package_hash,
        "category": "综合能源", "selection_reasons": ["active_hybrid_category"],
        "event_count": 1, "article_count": 1,
    }]}
    (campaign / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (campaign / "sol-risk-manifest.json").write_text(json.dumps({
        "items": [{"project_id": "HB"}]
    }), encoding="utf-8")

    result = compile_dry_run(campaign, database, tmp_path / "compiled")
    assert result["apply_allowed"] is True
    assert result["change_count"] == 1
    assert result["blocking_exception_count"] == 0


def test_unresolved_apply_writes_shadow_only_and_is_idempotent(tmp_path):
    """未决项目只能进入 needs_review shadow，正式类别和事件事实保持原样。"""
    database = tmp_path / "main.db"
    _main_db(database)
    compiled = tmp_path / "compiled"
    compiled.mkdir()
    changes_path = compiled / "changes.json"
    changes_path.write_text("[]", encoding="utf-8")
    canonical = _package_and_decision()[2]["canonical_decision"]
    unresolved = [{
        "project_id": "HB", "decision_role": "sol", "decision_model": "gpt-5.6-sol",
        "decision_sha256": "c" * 64, "review_reason": "多项目无法可靠归因",
        "canonical_decision": canonical,
    }]
    unresolved_path = compiled / "unresolved.json"
    unresolved_path.write_text(json.dumps(unresolved, ensure_ascii=False), encoding="utf-8")
    plan = {
        "apply_allowed": True, "exception_count": 1, "blocking_exception_count": 0,
        "main_db_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
        "changes_sha256": hashlib.sha256(changes_path.read_bytes()).hexdigest(),
        "unresolved_sha256": hashlib.sha256(unresolved_path.read_bytes()).hexdigest(),
    }
    (compiled / "apply-plan.json").write_text(json.dumps(plan), encoding="utf-8")

    first = apply_compiled(database, compiled, CAMPAIGN_VERSION)
    second = apply_compiled(database, compiled, CAMPAIGN_VERSION)
    connection = sqlite3.connect(database)
    canonical_row = connection.execute(
        "SELECT category,subcategory FROM canonical_projects WHERE id='HB'"
    ).fetchone()
    shadow = connection.execute(
        "SELECT needs_review,review_reason FROM project_classification_shadow WHERE canonical_id='HB'"
    ).fetchone()
    connection.close()
    assert first["audited_unresolved"] == 1 and first["idempotent"] is False
    assert second["idempotent"] is True
    assert canonical_row == ("综合能源", "光储")
    assert shadow == (1, "多项目无法可靠归因")
