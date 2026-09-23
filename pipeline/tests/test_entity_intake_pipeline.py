"""Pipeline emits the intake from the configured extraction DB without changing it."""
import hashlib
import json
import sqlite3

from bri_pipeline import flow
from s01b_project_extractor.tools.project_extractor_tool import _init_projects_db


def test_default_pipeline_exports_review_queue_before_merge(tmp_path, monkeypatch):
    source = tmp_path / "projects.db"
    connection = _init_projects_db(str(source))
    connection.execute("""INSERT INTO extracted_projects
        (project_name,implementing_entity,source_link,excluded,exclude_source,
         needs_review,publication_status,imported_to_s02a)
        VALUES ('测试光伏项目','待确认公司','https://example.test/article',1,
                'entity_verification',1,'pending_review',0)""")
    connection.commit()
    connection.close()
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    raw = tmp_path / "raw.db"
    with sqlite3.connect(raw) as conn:
        conn.execute("CREATE TABLE raw_news(id INTEGER)")
        conn.execute("INSERT INTO raw_news VALUES(1)")
    from s02a_duplicate_merger.tools.duplicate_merger_tool import _init_db
    _init_db(str(tmp_path / "main.db")).close()
    monkeypatch.setattr(flow, "_run_s01b", lambda **kwargs: {
        "status":"completed", "projects_db_path": kwargs["projects_db_path"], "articles":[{"raw_id":1}]
    })
    calls = []

    class Merger:
        def _run(self, **kwargs):
            reports = list((tmp_path / "reports").glob("entity_intake_*.json"))
            assert len(reports) == 1
            report = json.loads(reports[0].read_text(encoding="utf-8"))
            assert report["entities"][0]["raw_name"] == "待确认公司"
            assert report["entities"][0]["records"][0]["action"] == "requires_reextraction"
            calls.append(kwargs)
            self.last_result = {"status":"completed", "input_rows":0}
            return "处理完成。"

    monkeypatch.setattr(flow, "DuplicateMergerTool", Merger)
    config = flow.PipelineConfig(
        skip_s01a=True, raw_news_db_path=str(raw), input_db=str(source), db_path=str(tmp_path / "main.db"),
        run_location_normalize=False, run_developer_normalize=False,
        run_en_backfill=False, run_txmf_backfill=False, run_residual_translate=False,
        run_storage_subcat_backfill=False, run_energy_taxonomy_shadow=False,
    )
    flow.run_pipeline(config)
    assert calls == [{"input_db": str(source), "db_path": str(tmp_path / "main.db")}]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    with sqlite3.connect(source) as connection:
        assert connection.execute("SELECT imported_to_s02a FROM extracted_projects").fetchone() == (0,)
