"""Offline s02e-to-s03 handoff: isolate bad links without changing source/export databases."""

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from s03_data_exporter import external_references as reference_module
from s03_data_exporter.external_references import load_references, resolve_reference_snapshot
from s03_data_exporter.json_exporter import export_json


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def databases(tmp_path):
    source, exported = tmp_path / "source.sqlite", tmp_path / "export.sqlite"
    with sqlite3.connect(source) as conn:
        conn.executescript("CREATE TABLE canonical_projects(id TEXT PRIMARY KEY, project_name TEXT, country_iso TEXT); CREATE TABLE project_events(id TEXT PRIMARY KEY, canonical_id TEXT, project_stage TEXT, publish_date TEXT);")
        conn.executemany("INSERT INTO canonical_projects VALUES(?,?,?)", [(pid, pid + "项目", "SAU") for pid in ("PV", "TX", "MF", "EMPTY")])
        conn.executemany("INSERT INTO project_events VALUES(?,?,?,?)", [(pid + "-01", pid, "签约", "2026-08-01") for pid in ("PV", "TX", "MF", "EMPTY")])
    with sqlite3.connect(exported) as conn:
        for table, ids in (("projects_flat", ("PV", "EMPTY")), ("tx_flat", ("TX",)), ("mf_flat", ("MF",))):
            conn.execute(f"CREATE TABLE {table}(project_id TEXT, project_name TEXT, country_iso TEXT, all_entities TEXT, legacy_field TEXT)")
            conn.executemany(f"INSERT INTO {table} VALUES(?,?,?,?,?)", [(pid, pid + "项目", "SAU", '[{"name":"甲公司"}]', "preserve") for pid in ids])
        for table in ("stats_country", "stats_entity", "stats_timeline"):
            conn.execute(f"CREATE TABLE {table}(fixture TEXT)")
            conn.execute(f"INSERT INTO {table} VALUES('unchanged')")
    return source, exported


def _candidate(pid="PV", scope="event", case="good"):
    now = datetime.now(timezone.utc)
    return {"case_id": case, "project_id": pid, "event_id": pid + "-01", "target": {"project_name": pid + "项目", "country_iso": "SAU"}, "decision": scope + "_reference", "reason": "fixture evidence", "reference": {
        "reference_id": case, "url": "https://www.jinkosolar.com/" + case, "title": "沙特项目报道", "publisher": "晶科能源", "source_type": "official", "scope": scope,
        "event_id": pid + "-01" if scope == "event" else None, "stage": "签约" if scope == "event" else None, "stage_en": "Signed" if scope == "event" else None, "event_date": "2026-08-01" if scope == "event" else None,
        "checked_at": (now - timedelta(days=1)).isoformat(), "valid_until": (now + timedelta(days=20)).isoformat(), "is_reprint": False, "original_publisher": None,
        "private_raw_response": "must never be exported"}}


def _mixed():
    good = [_candidate(), _candidate("TX", "project", "tx"), _candidate("MF", "project", "mf")]
    pending = _candidate(case="pending") | {"decision": "pending", "reason": "evidence_insufficient"}
    rejected = _candidate(case="wrong-project") | {"decision": "rejected", "reason": "different_project"}
    failed = _candidate(case="format-failure")
    failed["reference"] = "bad format"
    cross = _candidate(case="cross-project") | {"event_id": "TX-01"}
    stale = _candidate(case="stale")
    stale["reference"]["checked_at"] = "2020-01-01T00:00:00+00:00"
    stale["reference"]["valid_until"] = "2020-02-01T00:00:00+00:00"
    identity = _candidate(case="identity-changed") | {"target": {"project_name": "另一项目", "country_iso": "SAU"}}
    # A good row after failures proves candidate iteration continues.
    return [good[0], pending, rejected, failed, cross, stale, identity, None, good[1], good[2]]


def test_mixed_batch_exports_three_tables_preserves_legacy_fields_and_database_bytes(tmp_path, databases):
    source, exported = databases
    before = (_hash(source), _hash(exported))
    sidecar = _write(tmp_path / "references.json", {"version": "external-references-v1", "candidates": _mixed()})
    baseline, enriched, repeated = [tmp_path / name for name in ("baseline", "enriched", "repeated")]
    export_json(exported, baseline, source)
    result = export_json(exported, enriched, source, external_references=sidecar)
    export_json(exported, repeated, source, external_references=sidecar)
    for table in ("projects_flat", "tx_flat", "mf_flat"):
        old = json.loads((baseline / (table + ".json")).read_text(encoding="utf-8"))
        new = json.loads((enriched / (table + ".json")).read_text(encoding="utf-8"))
        assert all(row["external_references"] == [] for row in old)
        assert [{k: v for k, v in row.items() if k != "external_references"} for row in old] == [{k: v for k, v in row.items() if k != "external_references"} for row in new]
        for row in new:
            assert len(row["external_references"]) == (0 if row["project_id"] == "EMPTY" else 1)
            assert "private_raw_response" not in json.dumps(row)
    for name in ("stats_country.json", "stats_entity.json", "stats_timeline.json", "metadata.json"):
        assert (baseline / name).read_bytes() == (enriched / name).read_bytes()
    assert {p.name: p.read_bytes() for p in enriched.glob("*.json")} == {p.name: p.read_bytes() for p in repeated.glob("*.json")}
    audit = {row["case_id"]: row for row in result["reference_audit"]}
    assert audit["good"]["status"] == audit["tx"]["status"] == audit["mf"]["status"] == "success"
    assert audit["pending"]["reason"] == "evidence_insufficient"
    assert audit["wrong-project"]["status"] == "excluded"
    assert audit["format-failure"]["reason"] == "reference_format_invalid"
    assert audit["cross-project"]["reason"] == "reference_event_project_mismatch"
    assert audit["stale"]["reason"] == "reference_stale_or_invalid_window"
    assert audit["identity-changed"]["reason"] == "reference_target_identity_changed"
    assert (_hash(source), _hash(exported)) == before


def test_missing_sidecar_fails_before_overwrite_and_stale_identity_hides_only_references(tmp_path, databases):
    source, exported = databases
    output = tmp_path / "missing"
    sidecar = _write(tmp_path / "references.json", {"version": "external-references-v1", "candidates": [_candidate()]})
    export_json(exported, output, source, external_references=sidecar)
    before_files = {p.name: p.read_bytes() for p in output.glob("*.json")}
    for missing in (tmp_path / "absent.json", _write(tmp_path / "invalid.json", {"version": "wrong"})):
        with pytest.raises(ValueError, match="reference_snapshot_unavailable"):
            export_json(exported, output, source, external_references=missing)
        assert {p.name: p.read_bytes() for p in output.glob("*.json")} == before_files
    with sqlite3.connect(exported) as conn:
        conn.execute("UPDATE tx_flat SET project_name='changed in flat DB'")
    before = (_hash(source), _hash(exported))
    sidecar = _write(tmp_path / "references.json", {"version": "external-references-v1", "candidates": [_candidate("TX", "project", "tx"), _candidate("MF", "project", "mf")]})
    result = export_json(exported, tmp_path / "stale-flat", source, external_references=sidecar)
    assert any(row["reason"] == "exported_project_identity_changed" for row in result["reference_audit"])
    assert json.loads((tmp_path / "stale-flat/tx_flat.json").read_text(encoding="utf-8"))[0]["external_references"] == []
    assert len(json.loads((tmp_path / "stale-flat/mf_flat.json").read_text(encoding="utf-8"))[0]["external_references"]) == 1
    assert (_hash(source), _hash(exported)) == before


def test_source_bound_pointer_defaults_override_and_language_guard(tmp_path, databases, monkeypatch):
    source, exported = databases
    english = _candidate(case="english") | {"evidence_language": "en", "private_evidence": "English body"}
    chinese = _candidate(case="chinese") | {"evidence_language": "zh"}
    sidecar = _write(tmp_path / "references.json", {"version": "external-references-v1",
                                                        "candidates": [_candidate(), english, chinese]})
    config = _write(tmp_path / "pointer.json", {"version": "external-reference-config-v1",
                  "source_db": source.name, "snapshot": sidecar.name})
    monkeypatch.setattr(reference_module, "DEFAULT_SOURCE_DB", source)
    monkeypatch.setattr(reference_module, "DEFAULT_REFERENCE_CONFIG", config)
    assert resolve_reference_snapshot(source) == sidecar
    assert resolve_reference_snapshot(tmp_path / "isolated.db") is None
    with pytest.raises(ValueError, match="source_mismatch"):
        resolve_reference_snapshot(tmp_path / "isolated.db", reference_config=config)
    with pytest.raises(ValueError, match="not both"):
        resolve_reference_snapshot(source, sidecar, config)
    assert resolve_reference_snapshot(source, sidecar) == sidecar
    for output in (tmp_path / "first", tmp_path / "repeat"):
        report = export_json(exported, output, source)
        assert report["reference_summary"]["success"] == 2
        assert report["reference_summary"]["reasons"]["reference_evidence_not_chinese"] == 1
        public = (output / "projects_flat.json").read_text(encoding="utf-8")
        assert "english" not in public and "English body" not in public
        assert len(json.loads(public)[0]["external_references"]) == 2
    assert {p.name: p.read_bytes() for p in (tmp_path / "first").glob("*.json")} == {p.name: p.read_bytes() for p in (tmp_path / "repeat").glob("*.json")}


@pytest.mark.parametrize("failure", ["config_missing", "config_format", "snapshot_missing", "snapshot_format", "source_mismatch"])
def test_configured_snapshot_failures_keep_existing_public_bytes(tmp_path, databases, failure):
    source, exported = databases
    snapshot = _write(tmp_path / "references.json", {"version": "external-references-v1", "candidates": [_candidate()]})
    output = tmp_path / "public"
    export_json(exported, output, source, external_references=snapshot)
    before = {p.name: p.read_bytes() for p in output.glob("*.json")}
    config = _write(tmp_path / "pointer.json", {"version": "external-reference-config-v1", "source_db": source.name, "snapshot": snapshot.name})
    if failure == "config_missing":
        config.unlink()
    elif failure == "config_format":
        _write(config, {"version": "wrong"})
    elif failure == "snapshot_missing":
        snapshot.unlink()
    elif failure == "snapshot_format":
        _write(snapshot, {"version": "wrong"})
    else:
        _write(config, {"version": "external-reference-config-v1", "source_db": "other.db", "snapshot": snapshot.name})
    with pytest.raises(ValueError, match="reference_"):
        export_json(exported, output, source, reference_config=config)
    assert {p.name: p.read_bytes() for p in output.glob("*.json")} == before


def test_s02e_review_handoff_runs_through_s03_without_api(tmp_path, databases, monkeypatch):
    producer = pytest.importorskip("s02e_source_finder.export_references")
    pilot = pytest.importorskip("s02e_source_finder.pilot")
    contracts = pytest.importorskip("s02e_source_finder.contracts")
    source, exported = databases
    before = (_hash(source), _hash(exported))
    private = tmp_path / "data"
    private.mkdir()
    monkeypatch.setattr(pilot, "MODULE", tmp_path)
    candidate = _candidate("MF", "project", "mf")
    context = contracts.EventContext(project_id="MF", event_id="MF-01", project={"project_name": "MF项目", "country_iso": "SAU"}, event={"project_stage": "签约"}, entities=[], source_articles=[])
    inputs = _write(private / "inputs.json", {"contexts": {"MF-01": context.model_dump()}, "source_hashes": {str(source): _hash(source)}})
    review = _write(private / "review.json", {"rows": [{"case_id": "mf", "event_id": "MF-01", "decision": "project_reference", "external_reference": candidate["reference"]}, {"event_id": "MF-01", "decision": "pending", "reason": "format_failure"}]})
    import requests
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *a, **kw: pytest.fail("external request forbidden"))
    snapshot = private / "snapshot.json"
    assert producer.export_references(inputs, review, snapshot)["external_calls"] == 0
    accepted, audit = load_references(snapshot, source)
    assert len(accepted["MF"]) == 1
    assert [row["status"] for row in audit] == ["success", "pending"]
    export_json(exported, tmp_path / "cross-module", source, external_references=snapshot)
    rows = json.loads((tmp_path / "cross-module/mf_flat.json").read_text(encoding="utf-8"))
    assert rows[0]["external_references"][0]["scope"] == "project"
    assert rows[0]["external_references"][0]["event_id"] is None
    assert (_hash(source), _hash(exported)) == before
