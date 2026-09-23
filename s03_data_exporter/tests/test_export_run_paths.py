"""A custom pipeline database must never silently export the production database."""
import hashlib
import sqlite3

import pytest

from s03_data_exporter import main
from s03_data_exporter import province_extractor
from s02a_duplicate_merger.tools.duplicate_merger_tool import _init_db


def test_custom_source_requires_both_destinations(tmp_path):
    with pytest.raises(ValueError, match="显式指定"):
        main.run(src_db=tmp_path / "isolated.db", allow_model_calls=False)


@pytest.mark.parametrize("provider_failure", [False, True])
def test_isolated_export_without_model_calls_preserves_source(tmp_path, monkeypatch, provider_failure):
    source = tmp_path / "main.db"
    conn = _init_db(str(source))
    conn.execute("INSERT INTO canonical_projects(id,project_name,country,country_iso,category,location) "
                 "VALUES ('ARG-WD-0001','海角风电项目','阿根廷','ARG','风电','火地岛')")
    conn.execute("INSERT INTO project_events(id,canonical_id,project_stage,capacity_mw,generation_sources_json,"
                 "storage_status,publish_date,source_links) VALUES "
                 "('ARG-WD-0001-01','ARG-WD-0001','投入运营',9,'[\"风电\"]','not_mentioned','2026-09-01','[]')")
    conn.execute("INSERT INTO chinese_entities(event_id,implementing_entity,involvement_type) "
                 "VALUES ('ARG-WD-0001-01','金风科技','Unknown')")
    conn.commit()
    conn.close()
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline export must not create a provider client")
    if provider_failure:
        from unittest.mock import Mock
        client = Mock()
        client.models.generate_content.side_effect = RuntimeError("service unavailable")
        monkeypatch.setattr(province_extractor.genai, "Client", lambda **kw: client)
        monkeypatch.setattr(province_extractor.time, "sleep", lambda _: None)
        monkeypatch.setenv("GEMINI_API_KEY", "test-not-used")
        monkeypatch.delenv("SKIP_PROVINCE_API", raising=False)
    else:
        monkeypatch.setattr(province_extractor.genai, "Client", forbidden)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    report = main.run(src_db=source, dst_dir=tmp_path / "export",
                      web_data_dir=tmp_path / "web/data", allow_model_calls=provider_failure)
    assert report["status"] == ("partial" if provider_failure else "completed")
    assert report["province_diagnostics"]["failed_ids"] == (["ARG-WD-0001"] if provider_failure else [])
    assert report["project_rows"] == 1
    assert report["event_rows"] == 1
    assert (tmp_path / "web/data/events_export.csv").exists()
    assert (tmp_path / "export/projects_flat.xlsx").exists()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    with sqlite3.connect(report["output_db"]) as connection:
        assert connection.execute("SELECT project_id FROM projects_flat").fetchall() == [("ARG-WD-0001",)]
