"""Source-preserving real-article fixtures through extraction, writer and merger."""
from copy import deepcopy
import importlib.util
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_three_article_offline_pipeline(monkeypatch, tmp_path):
    from s01b_project_extractor.stages.orchestrator import extract_staged
    spec = importlib.util.spec_from_file_location("recovery_validation", ROOT / "scripts/oneoff/entity_recovery_validation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("S01B_MERGE_S34", "1")
    records = []
    class Frozen:
        def __init__(self, data):
            self.data = data
        def complete(self, prompt, *, stage, json_mode=True):
            return deepcopy(self.data[stage])
    for path in sorted((ROOT / "tests/fixtures/entity_recovery_20260923").glob("*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        assert "NOT verbatim API response" in fixture["provenance"]
        row = fixture["source"]
        result = extract_staged(row["title"], row["content"], row["publish_date"], llm=Frozen(fixture["responses"]))
        records.append({"source": row, "result": result})
    by_id = {r["source"]["id"]: r["result"] for r in records}
    goldwind = by_id[27311]["projects"][0]
    assert goldwind["publication_status"] == "eligible"
    assert goldwind["common_info"]["capacity_mw_parsed"] == 9
    assert "2 × 4.5 MW = 9 MW" in goldwind["common_info"]["capacity_details_note"]
    assert goldwind["common_info"]["storage_power_mw_parsed"] is None
    assert goldwind["common_info"]["storage_energy_mwh_parsed"] is None
    assert goldwind["chinese_entities"][0]["Type of involvement of Chinese Entities"] == "Unknown"
    sergeli = by_id[27690]["projects"][0]
    assert sergeli["publication_status"] == "eligible"
    assert sergeli["common_info"]["capacity_mw_parsed"] is None
    assert sergeli["common_info"]["storage_power_mw_parsed"] == 175
    assert sergeli["common_info"]["storage_energy_mwh_parsed"] == 350
    assert by_id[27680]["status"] == "no_chinese_participation"
    verification = module.persist_isolated(records, tmp_path)
    assert verification["counts"]["canonical_new"] == 2
    assert verification["counts"]["event_new"] == 2
    assert verification["counts"]["entity_new"] == 2
    assert verification["all_rows_unchanged_on_repeat"]
    rows = json.loads((tmp_path / "extracted-rows.json").read_text(encoding="utf-8"))
    assert len(rows) == 2
    for row in rows:
        assert row["project_developer"] != "中国电建"
        assert json.loads(row["entity_evidence_json"])["source"]["content"]
    with sqlite3.connect(tmp_path / "main.db") as conn:
        events = conn.execute("SELECT capacity_mw,storage_power_mw,storage_energy_mwh,capacity_details FROM project_events ORDER BY id").fetchall()
        assert events[0][:3] == (9.0, None, None)
        assert "2 × 4.5 MW = 9 MW" in events[0][3]
        assert events[1][:3] == (None, 175.0, 350.0)
