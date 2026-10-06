"""Constructed contract tests; none of these verdicts are real model validation."""
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline/src"))
sys.path.insert(0, str(ROOT / "s02a_duplicate_merger/src"))
from bri_pipeline.historical_reprocess import build_coordination_plan, apply_coordination_plan
from s02a_duplicate_merger.tools import duplicate_merger_tool as m, coordination


@pytest.fixture(autouse=True)
def _keep_confirmation_tests_offline(monkeypatch):
    """Keep unit tests on their stubbed Gemini route regardless of local model settings."""
    monkeypatch.setattr(m, "LLM_CONFIRM_PROVIDER", "gemini")


def source(path, normal=True):
    with sqlite3.connect(path) as c:
        c.execute("""CREATE TABLE extracted_projects (
            id INTEGER PRIMARY KEY, source_link TEXT, project_name TEXT, country TEXT,
            country_iso TEXT, category TEXT, capacity_mw REAL, project_stage TEXT,
            location TEXT, project_developer TEXT, implementing_entity TEXT,
            involvement_type TEXT, publication_status TEXT, imported_to_s02a INTEGER DEFAULT 0)""")
        c.execute("INSERT INTO extracted_projects VALUES (1,'https://example.org/oman','JBB ALI construction','阿曼','OMN','风电',123,'开工','Jalan Bani Bu Ali','EDF','中国能建','Unknown','eligible',0)")
        if normal:
            c.execute("INSERT INTO extracted_projects VALUES (2,'https://example.org/new','Clearly new solar site','巴基斯坦','PAK','光伏',NULL,'签约','New place',NULL,'原文中企名','Unknown','eligible',0)")


def seed(path):
    c = m._init_db(str(path))
    c.execute("INSERT INTO canonical_projects(id,project_name,country,country_iso,category,location) VALUES ('OMN-WD-0002','Historical 120MW farm','阿曼','OMN','风电','Jalan Bani Bu Ali')")
    c.execute("INSERT INTO project_events(id,canonical_id,project_stage,capacity_mw,source_links) VALUES ('OMN-WD-0002-01','OMN-WD-0002','签约',120,'[\"https://example.org/old\"]')")
    c.commit(); c.close()


def fake(monkeypatch, verdict="partial_overlap"):
    calls = []
    state = {"verdict": verdict}
    def generate(**kwargs):
        calls.append(kwargs)
        if state["verdict"] == "technical":
            raise RuntimeError("Injected transport failure, not a service request")
        if state["verdict"] == "malformed":
            return SimpleNamespace(text='{"reasoning":"missing relation"}')
        return SimpleNamespace(text=json.dumps({"relationship": state["verdict"], "confidence": "high", "reasoning": "Constructed 120/123MW identity conflict"}))
    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate))
    monkeypatch.setenv("GEMINI_API_KEY", "offline-test")
    monkeypatch.setattr(m.genai, "Client", lambda **kw: client)
    monkeypatch.setattr(m, "_infer_location_from_name", lambda *a: None)
    monkeypatch.setattr(m, "_apply_location_norm", lambda *a: 0)
    return calls, state


def rows(path, table):
    with sqlite3.connect(path) as c:
        return c.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()


def run(projects, main):
    tool = m.DuplicateMergerTool()
    tool._run(str(projects), str(main))
    return tool.last_result


def setup(tmp_path, monkeypatch, verdict="partial_overlap", normal=True):
    projects, main = tmp_path / "projects.db", tmp_path / "main.db"
    source(projects, normal); seed(main)
    calls, state = fake(monkeypatch, verdict)
    return projects, main, calls, state


def test_mixed_batch_hold_blocks_all_business_rows_and_is_idempotent(tmp_path, monkeypatch):
    projects, main, calls, _ = setup(tmp_path, monkeypatch)
    result = run(projects, main)
    assert result["status"] == "partial" and result["coordination_created"] == 1
    assert result["counts"]["canonical_new"] == 1 and result["imported_rows"] == 1
    assert [r[0] for r in rows(main, "canonical_projects")] == ["OMN-WD-0002", "PAK-PV-0001"]
    assert len(rows(main, "project_events")) == 2 and len(rows(main, "chinese_entities")) == 1
    assert [(r[0], r[-1]) for r in rows(projects, "extracted_projects")] == [(1, 0), (2, 1)]
    task = rows(projects, "s02a_coordination")[0]
    evidence = json.loads(task[4])[0]
    assert evidence["candidate_canonical_id"] == "OMN-WD-0002"
    assert evidence["model_result"]["relationship"] == "partial_overlap"
    assert "capacity_mw" in evidence["conflict_fields"]
    before = {t: rows(main, t) for t in ("canonical_projects", "project_events", "chinese_entities")}
    calls.clear()
    repeat = run(projects, main)
    assert repeat["coordination_pending"] == 1 and repeat["coordination_created"] == 0 and not calls
    assert rows(projects, "s02a_coordination") == [task]
    assert before == {t: rows(main, t) for t in before}


@pytest.mark.parametrize("verdict", ["exact_duplicate", "same_project_different_event", "distinct"])
def test_confirmed_and_distinct_flow_normally(tmp_path, monkeypatch, verdict):
    projects, main, _, _ = setup(tmp_path, monkeypatch, verdict, normal=False)
    result = run(projects, main)
    assert result["status"] == "completed" and result["imported_rows"] == 1
    assert result["counts"]["canonical_new"] == int(verdict == "distinct")
    assert len(rows(main, "project_events")) == 2
    assert coordination.pending_count(projects) == 0


@pytest.mark.parametrize("verdict", ["technical", "malformed"])
def test_technical_failure_rolls_back_flags_and_can_resume(tmp_path, monkeypatch, verdict):
    projects, main, calls, state = setup(tmp_path, monkeypatch, verdict)
    with pytest.raises((RuntimeError, ValueError)):
        run(projects, main)
    assert len(rows(main, "canonical_projects")) == 1 and len(rows(main, "project_events")) == 1
    assert all(r[-1] == 0 for r in rows(projects, "extracted_projects"))
    assert coordination.pending_count(projects) == 0
    state["verdict"] = "partial_overlap"
    assert run(projects, main)["coordination_created"] == 1


def decision(projects, distinct=False):
    return dict(task_id=rows(projects, "s02a_coordination")[0][0], decision="distinct" if distinct else "existing",
                canonical_id=None if distinct else "OMN-WD-0002", reviewer="synthetic-test",
                evidence_review_note="Constructed operator decision only; not an actual Oman identity ruling")


@pytest.mark.parametrize("distinct", [False, True])
def test_reviewed_targeted_recovery_and_repeat_preserve_old_ids(tmp_path, monkeypatch, distinct):
    projects, main, calls, _ = setup(tmp_path, monkeypatch)
    run(projects, main); calls.clear()
    old_event = rows(main, "project_events")[0]
    plan = build_coordination_plan(projects, main, decision(projects, distinct))
    result = apply_coordination_plan(plan, tmp_path / "backup")
    assert result["status"] == "applied" and not calls
    assert result["canonical_id"] == ("OMN-WD-0003" if distinct else "OMN-WD-0002")
    assert old_event in rows(main, "project_events")
    assert all(r[-1] == 1 for r in rows(projects, "extracted_projects"))
    assert coordination.pending_count(projects) == 0
    assert apply_coordination_plan(plan, tmp_path / "backup")["status"] == "already_applied"
    assert run(projects, main)["input_rows"] == 0 and not calls
    with sqlite3.connect(main) as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


def test_changed_source_invalidates_review_plan(tmp_path, monkeypatch):
    projects, main, _, _ = setup(tmp_path, monkeypatch)
    run(projects, main)
    plan = build_coordination_plan(projects, main, decision(projects))
    with sqlite3.connect(projects) as c:
        c.execute("UPDATE extracted_projects SET capacity_mw=999 WHERE id=1")
    with pytest.raises(ValueError, match="source changed"):
        apply_coordination_plan(plan, tmp_path / "backup")
    assert coordination.pending_count(projects) == 1


def test_import_flag_failure_rolls_back_business_and_hold(tmp_path, monkeypatch):
    projects, main, _, _ = setup(tmp_path, monkeypatch)
    with sqlite3.connect(projects) as c:
        c.execute("CREATE TRIGGER fail_import BEFORE UPDATE OF imported_to_s02a ON extracted_projects WHEN NEW.imported_to_s02a=1 BEGIN SELECT RAISE(ABORT,'injected flag failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected flag"):
        run(projects, main)
    assert len(rows(main, "canonical_projects")) == 1
    assert coordination.pending_count(projects) == 0
    assert all(r[-1] == 0 for r in rows(projects, "extracted_projects"))


def test_batch_overlap_evidence_survives_cached_retry(tmp_path, monkeypatch):
    projects, main, calls, _ = setup(tmp_path, monkeypatch)
    frame, _ = m._load_from_db(str(projects))
    logical = m._group_into_logical_projects(frame)
    cache = str(tmp_path / "pairs.db")
    client = m.genai.Client()
    verdict = m._confirm_llm_pairs(client, None, logical, [(0, 1)], cache)
    assert verdict == {(0, 1): False} and len(calls) == 1
    assert all(p["_coordination_conflicts"] for p in logical)
    calls.clear()
    fresh = m._group_into_logical_projects(frame)
    assert m._confirm_llm_pairs(client, None, fresh, [(0, 1)], cache) == verdict
    assert all(p["_coordination_conflicts"] for p in fresh) and not calls


def test_source_scope_preserves_history_reuses_imported_and_repeats_without_writes(tmp_path, monkeypatch):
    projects, main, calls, _ = setup(tmp_path, monkeypatch)
    with sqlite3.connect(projects) as c:
        c.execute("INSERT INTO extracted_projects SELECT 3,'https://example.org/pending',project_name,country,country_iso,category,capacity_mw,project_stage,location,project_developer,implementing_entity,involvement_type,'pending_review',0 FROM extracted_projects WHERE id=1")
        c.execute("INSERT INTO extracted_projects SELECT 4,'https://example.org/old',project_name,country,country_iso,category,capacity_mw,project_stage,location,project_developer,implementing_entity,involvement_type,'eligible',1 FROM extracted_projects WHERE id=1")
        coordination.init_queue(c)
        c.execute("INSERT INTO s02a_coordination VALUES ('outside-task','pending','{}','[]','[]','test',NULL,NULL)")
        c.execute("INSERT INTO s02a_coordination_sources VALUES (3,'outside-task')")
    before = rows(projects, "extracted_projects")
    old_business = {t: rows(main, t) for t in ("canonical_projects", "project_events", "chinese_entities")}
    kwargs = dict(input_db=str(projects), db_path=str(main), source_urls=["https://example.org/new", "https://example.org/old"])
    tool = m.DuplicateMergerTool()
    tool._run(**kwargs)
    result = tool.last_result
    assert result["status"] == "completed" and result["imported_rows"] == 1
    assert result["coordination_pending"] == 0 and result["coordination_pending_overall"] == 1
    assert result["counts"]["canonical_new"] == result["counts"]["event_new"] == result["counts"]["entity_new"] == 1
    after = rows(projects, "extracted_projects")
    assert [r for r in after if r[0] != 2] == [r for r in before if r[0] != 2]
    assert next(r[-1] for r in after if r[0] == 2) == 1
    for table, existing in old_business.items():
        assert all(row in rows(main, table) for row in existing)
    snapshot = {t: rows(main, t) for t in old_business}
    calls.clear()
    tool._run(**kwargs)
    assert tool.last_result["input_rows"] == tool.last_result["imported_rows"] == 0 and not calls
    assert snapshot == {t: rows(main, t) for t in snapshot}
    assert after == rows(projects, "extracted_projects")
    tool._run(input_db=str(projects), db_path=str(main), source_urls=[])
    assert tool.last_result["status"] == "completed" and tool.last_result["coordination_pending"] == 0
    assert snapshot == {t: rows(main, t) for t in snapshot} and not calls
    with sqlite3.connect(main) as c:
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("source_urls", ["https://example.org/new", [""], [" "], [None], [1]])
def test_invalid_source_scope_is_rejected_before_opening_database(tmp_path, source_urls):
    path = tmp_path / "missing.db"
    with pytest.raises(ValueError, match="source_urls"):
        m.DuplicateMergerTool()._run(input_db=str(path), source_urls=source_urls)
    assert not path.exists()
