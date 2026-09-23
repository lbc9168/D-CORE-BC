"""Offline S01B -> SQLite -> S02A contract tests with fixed model responses.

The Sergeli sentence is a real source excerpt; other cases are explicit synthetic
fixtures. Models and translations are patched only at external boundaries. Actual
identity/evidence guards, writer, source loading and main-database merging run.
"""
from copy import deepcopy
import json
import sqlite3

import pandas as pd
import pytest

from s01b_project_extractor.stages.orchestrator import extract_staged
from s01b_project_extractor.tools import project_extractor_tool as writer
from s02a_duplicate_merger.tools.duplicate_merger_tool import (
    _group_into_logical_projects, _init_db, _load_from_db, _mark_imported, _write_to_db,
)


class FrozenLLM:
    def __init__(self, common, entities):
        self.stages = []
        self.responses = {
            "s1": {"status": "valid_project", "reasoning": "固定原文提取结果", "project_subject": common["Project Name"]},
            "s2": {"projects": [{"in_scope": True, "common_info": common}]},
            "s4": {"chinese_entities": entities},
            "s3": {"Financing Model": "Unknown", "Financing Evidence": []},
            "s34": {"chinese_entities": entities, "Financing Model": "Unknown", "Financing Evidence": []},
            "s5": {"project_summary": "固定测试摘要", "corrections": []},
        }

    def complete(self, prompt, *, stage, json_mode=True):
        self.stages.append(stage)
        return deepcopy(self.responses[stage])


def _entity(name, quote, project, role="EPC总承包", identity=""):
    return {
        "Implementing Chinese Entity": name,
        "Type of involvement of Chinese Entities": role,
        "Participation Evidence": [{"role": role, "quote": quote, "project_anchor": project}],
        "Chinese Identity Evidence": identity,
    }


def _common(project, **extra):
    return {
        "Record Type": "具体项目", "Project Name": project, "Project Name (EN)": "Fixture Project",
        "Country": "乌兹别克斯坦", "Category": "光伏", "Capacity (MW)": "",
        "Project Stage": "在建", "Project Developer": "", "Project Developer Evidence": "",
        **extra,
    }


def _run_flow(monkeypatch, tmp_path, content, common, entities, *, merge_s34=False,
              s1_status="valid_project"):
    if merge_s34:
        monkeypatch.setenv("S01B_MERGE_S34", "1")
    else:
        monkeypatch.delenv("S01B_MERGE_S34", raising=False)
    llm = FrozenLLM(common, entities)
    llm.responses["s1"]["status"] = s1_status
    extracted = []

    def extract(title, body, date, *, client, **kwargs):
        result = extract_staged(title, body, date, llm=llm)
        extracted.append(result)
        return result

    def forbid(*args, **kwargs):
        raise AssertionError("This integration fixture must not call an API or write translation caches")

    monkeypatch.setattr(writer, "extract_one", extract)
    monkeypatch.setattr(writer, "_resolve_entity_en", lambda name, client: "")
    monkeypatch.setattr(writer, "_resolve_developer_en", lambda name, client: name)
    monkeypatch.setattr(writer, "_save_name_cache", forbid)
    monkeypatch.setattr(writer.genai, "Client", forbid)
    source = tmp_path / "projects.db"
    review = tmp_path / "review.db"
    frame = pd.DataFrame([{
        "原文链接": "https://example.test/identity-flow", "文章标题": common["Project Name"],
        "正文内容": content, "发布日期": "2026-09-15",
    }])
    writer.ProjectExtractorTool()._process_df(
        frame, batch_id="identity-flow", ts="20260917_fixture", client=None,
        country_db={"乌兹别克斯坦": {"iso": "UZB", "bri": "Yes", "ldc": "No"}},
        projects_db_path=str(source), review_db_path=str(review), max_workers=1,
    )
    assert len(extracted) == 1
    # Invalid evidence may trigger the real stage's single bounded retry.
    ordered_stages = [stage for index, stage in enumerate(llm.stages)
                      if index == 0 or stage != llm.stages[index - 1]]
    assert ordered_stages == (["s1", "s2", "s34", "s5"] if merge_s34 else ["s1", "s2", "s4", "s3", "s5"])
    assert all(llm.stages.count(stage) <= 2 for stage in ordered_stages)
    connection = sqlite3.connect(source)
    connection.row_factory = sqlite3.Row
    source_rows = [dict(row) for row in connection.execute("SELECT * FROM extracted_projects ORDER BY id")]
    connection.close()
    loaded, import_ids = _load_from_db(str(source))
    projects = _group_into_logical_projects(loaded)
    main = _init_db(str(tmp_path / "main.db"))
    counts = _write_to_db(main, projects)
    _mark_imported(str(source), import_ids)
    return extracted[0]["projects"][0], source_rows, projects, main, counts, source, review


@pytest.mark.parametrize("merge_s34", [False, True])
def test_real_sergeli_participation_survives_unknown_role_and_replay(monkeypatch, tmp_path, merge_s34):
    project = "乌兹别克斯坦赛尔吉利储能电站项目"
    content = "近日，由中国电建承建的中乌能源合作典范工程，乌兹别克斯坦赛尔吉利储能电站项目一次并网成功。"
    result, rows, projects, main, counts, source, _ = _run_flow(
        monkeypatch, tmp_path, content,
        _common(project, Category="储能", **{"Project Stage": "投入运营", "Storage Status": "confirmed",
                                           "Energy Composition Evidence": content}),
        [_entity("中国电建", content, project)], merge_s34=merge_s34,
        s1_status="valid_project",
    )
    try:
        assert result["publication_status"] == "eligible"
        assert rows[0]["publication_status"] == "eligible" and rows[0]["excluded"] == 0
        assert rows[0]["involvement_type"] == "Unknown"  # 承建 cannot manufacture an EPC role.
        assert rows[0]["group_entity_id"]
        # 中国电建 is a group-level shorthand, not a verified particular legal person.
        assert rows[0]["entity_id"] is None
        assert rows[0]["raw_entity_name"] == "中国电建"
        evidence = json.loads(rows[0]["entity_evidence_json"])
        assert evidence["source"]["content"] == content
        assert evidence["entity"]["_participation_evidence_status"] == "verified"
        assert counts["canonical_new"] == counts["event_new"] == counts["entity_new"] == 1
        assert main.execute("SELECT group_entity_id,involvement_type FROM chinese_entities").fetchone() == (
            rows[0]["group_entity_id"], "Unknown")
        before = {table: main.execute(f"SELECT id FROM {table} ORDER BY id").fetchall()
                  for table in ("canonical_projects", "project_events", "chinese_entities")}
        repeated = _write_to_db(main, projects)
        assert repeated["canonical_new"] == repeated["event_new"] == repeated["entity_new"] == 0
        for table, ids in before.items():
            assert main.execute(f"SELECT id FROM {table} ORDER BY id").fetchall() == ids
        assert _load_from_db(str(source))[1] == []
        assert main.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert main.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        main.close()


@pytest.mark.parametrize("developer_evidence", [True, False])
def test_foreign_developer_independent_of_chinese_epc_and_pending_second_entity(monkeypatch, tmp_path, developer_evidence):
    project = "撒马尔罕晨光光伏项目"
    developer_quote = f"ACWA Power投资开发{project}。"
    epc_quote = f"中国能源建设集团有限公司签署{project}EPC总承包合同。"
    unknown_quote = f"新风设备公司为{project}供应设备。"
    content = developer_quote + epc_quote + unknown_quote
    result, rows, projects, main, counts, source, review = _run_flow(
        monkeypatch, tmp_path, content,
        _common(project, **{"Project Developer": "ACWA Power; 中国能源建设集团有限公司",
                            "Project Developer Evidence": developer_quote if developer_evidence else ""}),
        [_entity("中国能源建设集团有限公司", epc_quote, project),
         _entity("新风设备公司", unknown_quote, project, role="设备供应商")],
    )
    try:
        assert result["publication_status"] == "eligible"
        assert len(result["entity_candidates"]) == 2 and len(result["chinese_entities"]) == 1
        known, pending = rows
        assert known["entity_id"] and known["publication_status"] == "eligible"
        assert pending["publication_status"] == "pending_review" and pending["excluded"] == 1
        assert pending["raw_entity_name"] == "新风设备公司"
        pending_evidence = json.loads(pending["entity_evidence_json"])
        assert pending_evidence["source"]["content"] == content
        assert pending_evidence["entity"]["Participation Evidence"][0]["quote"] == unknown_quote
        assert len(projects) == 1 and len(projects[0]["entities"]) == 1
        assert main.execute("SELECT entity_id FROM chinese_entities").fetchone()[0] == known["entity_id"]
        assert main.execute("SELECT project_developer FROM project_events").fetchone()[0] == (
            "ACWA Power" if developer_evidence else "")
        assert main.execute("SELECT involvement_type FROM chinese_entities").fetchone()[0] == "EPC总承包"
        stable_entities = main.execute("SELECT id,event_id,entity_id FROM chinese_entities").fetchall()
        repeated = _write_to_db(main, projects)
        assert repeated["canonical_new"] == repeated["event_new"] == repeated["entity_new"] == 0
        assert main.execute("SELECT id,event_id,entity_id FROM chinese_entities").fetchall() == stable_entities
        with sqlite3.connect(source) as conn:
            assert conn.execute("SELECT imported_to_s02a FROM extracted_projects ORDER BY id").fetchall() == [(1,), (0,)]
        loaded, ids = _load_from_db(str(source))
        assert loaded.empty and ids == []  # Pending remains queued, not silently consumed.
        with sqlite3.connect(review) as conn:
            assert conn.execute("SELECT count(*) FROM review_queue").fetchone()[0] >= 1
    finally:
        main.close()


def test_source_verified_unregistered_chinese_company_is_eligible_pending_registration(monkeypatch, tmp_path):
    project = "塔什干新辰光伏项目"
    quote = f"中国企业新辰能源承建{project}。"
    result, rows, projects, main, counts, _, _ = _run_flow(
        monkeypatch, tmp_path, quote, _common(project),
        [_entity("新辰能源", quote, project, identity=quote)],
    )
    try:
        assert result["publication_status"] == "eligible" and result["needs_review"]
        assert rows[0]["publication_status"] == "eligible"
        assert rows[0]["needs_review"] == 1 and rows[0]["excluded"] == 0
        assert rows[0]["identity_status"] == "source_chinese_pending_registry"
        assert rows[0]["entity_id"] is None and rows[0]["group_entity_id"] is None
        assert counts["canonical_new"] == 1
        assert main.execute("SELECT implementing_entity,involvement_type FROM chinese_entities").fetchone() == (
            "新辰能源", "Unknown")
        assert main.execute("SELECT project_developer FROM project_events").fetchone()[0] == ""
    finally:
        main.close()



def test_two_comma_separated_projects_do_not_share_enterprise_evidence(monkeypatch, tmp_path):
    project = "青山乙光伏项目"
    quote = "由中国电建承建的青山甲光伏项目，中国能建承建青山乙光伏项目。"
    result, rows, projects, main, counts, _, _ = _run_flow(
        monkeypatch, tmp_path, quote, _common(project),
        [_entity("中国电建", quote, project), _entity("中国能建", quote, project)],
    )
    try:
        wrong, correct = rows
        assert wrong["publication_status"] == "pending_review"
        assert correct["publication_status"] == "eligible"
        assert len(projects[0]["entities"]) == 1
        assert main.execute("SELECT raw_entity_name,involvement_type FROM chinese_entities").fetchall() == [
            ("中国能建", "Unknown")]
        assert json.loads(wrong["entity_evidence_json"])["entity"]["_participation_evidence_status"] == "missing_or_ambiguous"
    finally:
        main.close()


def test_s1_registry_hint_does_not_override_explicit_negative():
    project = "青山乙光伏项目"
    quote = "中国电建承建青山甲光伏项目。ACWA Power开发青山乙光伏项目。"
    llm = FrozenLLM(_common(project), [])
    llm.responses["s1"]["status"] = "no_chinese_participation"
    result = extract_staged(project, quote, "2026-09-15", llm=llm)
    assert llm.stages == ["s1"]
    assert result["status"] == "no_chinese_participation"
    assert result["projects"] == []
    recall = result["analysis"]["identity_recall"]
    assert recall["mentions"]  # A real register hit must not invent participation.
    assert recall["original_status"] == "no_chinese_participation"
    assert recall["rescued_for_extraction"] is False


def test_accepted_s1_candidate_cannot_borrow_background_participation(monkeypatch, tmp_path):
    project = "青山乙光伏项目"
    quote = "中国电建承建青山甲光伏项目。ACWA Power开发青山乙光伏项目。"
    result, rows, projects, main, counts, _, _ = _run_flow(
        monkeypatch, tmp_path, quote, _common(project),
        [_entity("中国电建", quote, project)], s1_status="valid_project",
    )
    try:
        assert result["publication_status"] == "pending_review"
        assert rows and projects == []
        assert all(row["excluded"] == 1 for row in rows)
        assert main.execute("SELECT count(*) FROM canonical_projects").fetchone()[0] == 0
    finally:
        main.close()


def test_intake_registration_replays_same_relation_after_group_name_changes(monkeypatch, tmp_path):
    from s01b_project_extractor.stages.entity_normalize import EntityResolution
    from s01b_project_extractor.tools.entity_intake import refresh_entities

    project = "塔什干新辰光伏项目"
    quote = f"中国企业新辰能源承建{project}。"
    _, rows, _, main, _, source, _ = _run_flow(
        monkeypatch, tmp_path, quote, _common(project),
        [_entity("新辰能源", quote, project, identity=quote)],
    )
    try:
        before = {table: main.execute(f"SELECT id FROM {table} ORDER BY id").fetchall()
                  for table in ("canonical_projects", "project_events", "chinese_entities")}
        main.execute("UPDATE chinese_entities SET implementing_entity_en='Old Raw Company', "
                     "subsidiary_name_en='Old Subsidiary', entity_en_quality='manual_verified'")
        main.commit()
        # Emulate a reviewed registry addition, never modify the real hierarchy fixture.
        monkeypatch.setattr("s01b_project_extractor.stages.s4_entity.resolve_entity", lambda name: EntityResolution(
            implementing="新辰集团有限公司", subsidiary="新辰能源有限公司",
            verification_status="verified_chinese", matched_source="hierarchy",
            entity_id="fixture:energy", entity_name="新辰能源有限公司", group_id="fixture:group",
            parent_entity_id="fixture:group", raw_name=name,
        ))
        refresh = refresh_entities(source, apply=True, row_ids=[rows[0]["id"]])
        assert refresh["changed_rows"] == 1
        loaded, import_ids = _load_from_db(str(source))
        assert import_ids == [rows[0]["id"]]
        replay_projects = _group_into_logical_projects(loaded)
        written = _write_to_db(main, replay_projects)
        assert written["canonical_new"] == written["event_new"] == written["entity_new"] == 0
        _mark_imported(str(source), import_ids)
        for table, ids in before.items():
            assert main.execute(f"SELECT id FROM {table} ORDER BY id").fetchall() == ids
        entity = main.execute(
            "SELECT implementing_entity,subsidiary_name,entity_id,entity_name,group_entity_id,"
            "raw_entity_name,identity_status,implementing_entity_en,subsidiary_name_en,entity_en_quality "
            "FROM chinese_entities"
        ).fetchone()
        assert entity[:7] == ("新辰集团有限公司", "新辰能源有限公司", "fixture:energy", "新辰能源有限公司",
                              "fixture:group", "新辰能源", "verified_chinese")
        assert entity[7:] == ("", "", "ai_translated")
        evidence = json.loads(main.execute("SELECT entity_evidence_json FROM chinese_entities").fetchone()[0])
        assert evidence["entity"]["_entity_id"] == "fixture:energy"
        assert evidence["source"]["content"] == quote
        assert refresh_entities(source)["changed_rows"] == 0
        assert _load_from_db(str(source))[1] == []
        assert main.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        main.close()
