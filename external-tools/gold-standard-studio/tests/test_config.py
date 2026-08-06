"""Tests for the Golden Standard Studio configuration boundary."""

import pytest

from gold_standard_studio.config import StudioConfig, sqlite_ro_uri


def test_config_requires_existing_input_databases(tmp_path):
    """StudioConfig rejects a missing raw-news input database."""
    with pytest.raises(ValueError, match="raw_news_db does not exist"):
        StudioConfig.from_paths(
            raw_news_db=tmp_path / "missing.db",
            projects_db=tmp_path / "projects.db",
            main_db=tmp_path / "main.db",
            data_dir=tmp_path / "studio",
        )


def test_sqlite_ro_uri_is_explicitly_read_only(tmp_path):
    """sqlite_ro_uri adds SQLite's explicit read-only query parameter."""
    db = tmp_path / "a.db"
    db.touch()

    assert sqlite_ro_uri(db).endswith("?mode=ro")
