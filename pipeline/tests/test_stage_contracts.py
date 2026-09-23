"""Stage handoffs fail closed; recoverable article errors stay resumable."""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from bri_pipeline import flow
from s01b_project_extractor import main
from s01b_project_extractor.tools import project_extractor_tool as writer


def seed_raw(path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE raw_news(id INTEGER PRIMARY KEY,url TEXT,title TEXT,content TEXT,publish_date TEXT,batch_id TEXT,passed_keyword_filter INTEGER)")
        conn.executemany("INSERT INTO raw_news VALUES(?,?,?,?,?,?,1)", [
            (1,"https://test/1","failure","风电项目","2026-09-23","b1"),
            (2,"https://test/2","success","风电项目","2026-09-23","b1"),
            (3,"https://test/3","outside","风电项目","2026-09-23","b2")])


def test_bulk_partial_resume_only_failed_url_and_preserve_source(tmp_path, monkeypatch):
    raw = tmp_path / "raw.db"
    seed_raw(raw)
    before = hashlib.sha256(raw.read_bytes()).hexdigest()
    monkeypatch.setenv("GEMINI_API_KEY", "offline-test")
    monkeypatch.setattr(main, "_setup_run_logging", lambda: tmp_path / "log")
    monkeypatch.setattr(writer.genai, "Client", lambda **kwargs: object())
    calls = []
    def extract(title, content, date, **kwargs):
        calls.append(title)
        if title == "failure":
            raise RuntimeError("recorded parse failure")
        return {"status": "irrelevant", "analysis": {"reasoning": "fixed offline negative"}}
    monkeypatch.setattr(writer, "extract_one", extract)
    kwargs = dict(raw_news_db_path=str(raw), projects_db_path=str(tmp_path / "projects.db"),
                  review_db_path=str(tmp_path / "review.db"), raw_ids=[1,2])
    first = main.run_bulk_extract(**kwargs)
    assert first["status"] == "partial"
    assert first["failed_urls"] == ["https://test/1"]
    assert {item["raw_id"] for item in first["articles"]} == {1,2}
    with sqlite3.connect(kwargs["review_db_path"]) as conn:
        assert conn.execute("SELECT source_link FROM discarded_records").fetchall() == [("https://test/2",)]
    calls.clear()
    monkeypatch.setattr(writer, "extract_one", lambda title, *args, **kw: calls.append(title) or {"status":"irrelevant", "analysis":{}})
    second = main.run_bulk_extract(**kwargs)
    assert second["status"] == "completed" and calls == ["failure"]
    assert second["batches"][0]["skipped_articles"] == 1
    assert second["report_path"] != first["report_path"]
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == before


def test_bulk_write_failure_is_fatal_and_reported(tmp_path, monkeypatch):
    raw = tmp_path / "raw.db"
    seed_raw(raw)
    monkeypatch.setattr(main, "_setup_run_logging", lambda: tmp_path / "log")
    monkeypatch.setattr(writer.ProjectExtractorTool, "_run_from_db", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("disk failure")))
    with pytest.raises(main.BulkExtractionError) as caught:
        main.run_bulk_extract(raw_news_db_path=str(raw), projects_db_path=str(tmp_path / "p.db"), review_db_path=str(tmp_path / "r.db"))
    result = json.loads(Path(caught.value.result["report_path"]).read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["failed_batches"][0]["batch_id"] == "b1"


@pytest.mark.parametrize("ids", [[],[0],[True],["1"],[999]])
def test_invalid_selection_does_not_create_output(tmp_path, ids):
    raw = tmp_path / "raw.db"
    seed_raw(raw)
    with pytest.raises(ValueError):
        main.run_bulk_extract(raw_news_db_path=str(raw), projects_db_path=str(tmp_path / "p.db"), review_db_path=str(tmp_path / "r.db"), raw_ids=ids)
    assert not (tmp_path / "p.db").exists()


def test_missing_input_never_creates_sqlite_file(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        flow._validate_database(str(missing), {"raw_news": 1}, "S01B")
    assert not missing.exists()


def test_paths_unified_or_rejected(tmp_path):
    path = str(tmp_path / "projects.db")
    assert flow.PipelineConfig(input_db=path).projects_db_path == path
    assert flow.PipelineConfig(projects_db_path=path).input_db == path
    with pytest.raises(ValueError, match="input_db"):
        flow.PipelineConfig(projects_db_path=path, input_db=str(tmp_path / "other.db"))
    with pytest.raises(ValueError, match="export"):
        flow.PipelineConfig(db_path=str(tmp_path / "main.db"), run_export=True)


def test_backup_failure_precedes_schema_mutation(tmp_path, monkeypatch):
    import pandas as pd
    existing = tmp_path / "p.db"
    existing.write_bytes(b"not a database")
    monkeypatch.setattr(writer, "_init_projects_db", lambda path: pytest.fail("schema touched before backup succeeded"))
    with pytest.raises(RuntimeError, match="备份失败"):
        writer.ProjectExtractorTool()._process_df(pd.DataFrame(), "b", "ts", object(), {}, str(existing), str(tmp_path / "r.db"))
    assert existing.read_bytes() == b"not a database"


def _flow_case(tmp_path, monkeypatch, merge_result):
    raw = tmp_path / "raw.db"
    seed_raw(raw)
    projects = tmp_path / "p.db"
    writer._init_projects_db(str(projects)).close()
    config = flow.PipelineConfig(skip_s01a=True, raw_news_db_path=str(raw),
        projects_db_path=str(projects), review_db_path=str(tmp_path / "review.db"),
        db_path=str(tmp_path / "main.db"), run_entity_intake=False,
        run_location_normalize=False, run_developer_normalize=False,
        run_en_backfill=False, run_txmf_backfill=False, run_residual_translate=False,
        run_storage_subcat_backfill=False, run_energy_taxonomy_shadow=False)
    captured = []
    def extract(**kwargs):
        captured.append(kwargs)
        return {"status":"partial", "projects_db_path": kwargs["projects_db_path"],
                "articles":[{"raw_id":1}], "failed_urls":["https://test/1"]}
    monkeypatch.setattr(flow, "_run_s01b", extract)
    class Merger:
        def _run(self, **kwargs):
            captured.append(kwargs)
            if merge_result.startswith("未发现"):
                self.last_result = {"status":"completed", "input_rows":0}
            return merge_result
    monkeypatch.setattr(flow, "DuplicateMergerTool", Merger)
    return config, captured


def test_partial_and_zero_publishable_is_valid_handoff(tmp_path, monkeypatch):
    config, captured = _flow_case(tmp_path, monkeypatch, "未发现可发布记录（待复核）")
    result = flow.run_pipeline(config)
    assert result["status"] == "partial"
    assert captured[0]["projects_db_path"] == captured[1]["input_db"]
    assert result["extraction"]["failed_urls"] == ["https://test/1"]
    assert not Path(config.db_path).exists()


def test_merger_error_string_is_fatal(tmp_path, monkeypatch):
    config, _ = _flow_case(tmp_path, monkeypatch, "错误：missing API key")
    with pytest.raises(RuntimeError, match="S02A"):
        flow.run_pipeline(config)


def test_s01b_missing_contract_does_not_advance(tmp_path, monkeypatch):
    config, captured = _flow_case(tmp_path, monkeypatch, "处理完成")
    monkeypatch.setattr(flow, "_run_s01b", lambda **kw: None)
    with pytest.raises(RuntimeError, match="S01B"):
        flow.run_pipeline(config)
    assert captured == []


def test_field_warning_keeps_eligible_record_with_project_review_audit(tmp_path, monkeypatch):
    import pandas as pd
    project = {"project_summary":"已核实项目", "publication_status":"eligible", "needs_review":True,
        "field_review_warnings":["未披露容量保持空值"], "summary_warning":{"kind":"llm_failure","message":"offline"},
        "common_info":{"Project Name":"肯尼亚晨光光伏项目", "Country":"肯尼亚", "Category":"光伏", "Project Stage":"在建"},
        "chinese_entities":[{"Implementing Chinese Entity":"中国能建", "Type of involvement of Chinese Entities":"Unknown"}]}
    monkeypatch.setattr(writer, "extract_one", lambda *a, **kw: {"status":"valid_project", "analysis":{},"projects":[project]})
    monkeypatch.setattr(writer, "_resolve_entity_en", lambda *a, **kw: "")
    monkeypatch.setattr(writer, "_resolve_developer_en", lambda *a, **kw: "")
    frame = pd.DataFrame([{"原文链接":"https://test/fields", "文章标题":"晨光光伏", "正文内容":"中国能建参与肯尼亚晨光光伏项目", "发布日期":"2026-09-23"}])
    writer.ProjectExtractorTool()._process_df(frame,"b","fields",None,{},str(tmp_path / "p.db"),str(tmp_path / "r.db"),max_workers=1)
    with sqlite3.connect(tmp_path / "p.db") as conn:
        status, evidence = conn.execute("SELECT publication_status,entity_evidence_json FROM extracted_projects").fetchone()
    assert status == "eligible"
    assert json.loads(evidence)["field_review_warnings"] == ["未披露容量保持空值"]
    assert json.loads(evidence)["summary_warning"]["kind"] == "llm_failure"
    with sqlite3.connect(tmp_path / "r.db") as conn:
        assert conn.execute("SELECT reason_type FROM review_queue").fetchall() == [("low_confidence_field",)]


@pytest.mark.parametrize("partial_stage", ["residual_translate", "export"])
def test_optional_stage_partial_is_visible(tmp_path, monkeypatch, partial_stage):
    import sys
    from types import SimpleNamespace
    from s02a_duplicate_merger.tools.duplicate_merger_tool import _init_db
    config, _ = _flow_case(tmp_path, monkeypatch, "未发现可发布记录")
    _init_db(config.db_path).close()
    monkeypatch.setattr(flow, "_run_s01b", lambda **kw: {"status":"completed", "projects_db_path":kw["projects_db_path"], "articles":[{"raw_id":1}]})
    if partial_stage == "residual_translate":
        config.run_residual_translate = True
        monkeypatch.setitem(sys.modules, "s02b_entity_normalizer.en_translate_residual", SimpleNamespace(run_residual_translate=lambda *a, **kw: {"parts_failed":1,"parts_translated":0,"rows_updated":0}))
    else:
        config.run_export = True
        config.export_dir = str(tmp_path / "export")
        config.export_web_dir = str(tmp_path / "web")
        monkeypatch.setitem(sys.modules, "s03_data_exporter.main", SimpleNamespace(run=lambda **kw: {"status":"partial","src_db":kw["src_db"]}))
    result = flow.run_pipeline(config)
    assert result["status"] == "partial"
    assert partial_stage in result["stage_reports"]
    assert Path(result["main_backup"]).is_file()


def test_default_dates_resume_after_metadata(tmp_path):
    from datetime import date
    metadata = tmp_path / "metadata.json"
    metadata.write_text('{"last_updated":"2026-09-15"}', encoding="utf-8")
    report = flow._resolve_crawl_window(flow.PipelineConfig(metadata_path=str(metadata)), today=date(2026,9,23))
    assert (report["start_date"], report["end_date"]) == ("2026-09-16","2026-09-23")
    assert report["next_start_if_complete"] == "2026-09-24"
    assert report["next_start"] == "2026-09-16" and report["coverage_confirmed"] is False


def test_explicit_dates_do_not_require_metadata(tmp_path):
    report = flow._resolve_crawl_window(flow.PipelineConfig(start_date="2025-01-01",end_date="2025-01-03",metadata_path=str(tmp_path / "absent")))
    assert report["reason"] == "explicit_range"
    assert report["end_date"] == "2025-01-03"


@pytest.mark.parametrize("kwargs", [{"start_date":"20260915"},{"end_date":"2026-02-30"},{"start_date":"2026-09-23","end_date":"2026-09-22"}])
def test_invalid_dates_rejected_before_run(kwargs):
    with pytest.raises(ValueError):
        flow.PipelineConfig(**kwargs)


def test_missing_resume_metadata_requires_explicit_start(tmp_path):
    with pytest.raises(ValueError, match="start_date"):
        flow._resolve_crawl_window(flow.PipelineConfig(metadata_path=str(tmp_path / "missing")))


def test_no_new_dates_skips_crawler_and_continues_pending(tmp_path, monkeypatch):
    from datetime import date
    config, captured = _flow_case(tmp_path, monkeypatch, "未发现可发布记录")
    config.skip_s01a = False
    config.metadata_path = str(tmp_path / "metadata.json")
    Path(config.metadata_path).write_text(json.dumps({"last_updated":date.today().isoformat()}),encoding="utf-8")
    monkeypatch.setattr(flow, "_run_s01a", lambda **kw: pytest.fail("no new dates must not crawl"))
    result = flow.run_pipeline(config)
    assert result["stage_reports"]["collection"]["reason"] == "no_new_dates"
    assert len(captured) == 2


def test_crawler_failure_does_not_advance_existing_raw_db(tmp_path, monkeypatch):
    config, captured = _flow_case(tmp_path, monkeypatch, "未发现可发布记录")
    config.skip_s01a = False
    config.start_date, config.end_date = "2026-09-16", "2026-09-23"
    config.raw_data_dir = str(tmp_path / "raw")
    monkeypatch.setattr(flow, "_run_s01a", lambda **kw: "失败：network unavailable")
    with pytest.raises(RuntimeError, match="不代表已覆盖"):
        flow.run_pipeline(config)
    assert captured == []
