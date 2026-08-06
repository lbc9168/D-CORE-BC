"""Test read-only Studio source repositories against representative source databases."""

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from gold_standard_studio.config import StudioConfig
from gold_standard_studio.sources import SourceRepository, article_sha256


@dataclass(frozen=True)
class DatabaseFixture:
    """Provide three populated SQLite databases and their immutable-content hashes."""

    config: StudioConfig
    paths: tuple[Path, Path, Path]

    def hashes(self) -> dict[str, str]:
        """Return SHA-256 digests for the fixture's three source database files."""
        return {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.paths
        }


def _create_raw_news_db(path: Path) -> None:
    """Create the minimum raw-news schema and one linked article."""
    with sqlite3.connect(path) as conn:
        conn.execute(
            """CREATE TABLE raw_news (
                id INTEGER PRIMARY KEY,
                url TEXT UNIQUE,
                title TEXT,
                content TEXT,
                author TEXT,
                publish_date TEXT
            )"""
        )
        conn.execute(
            "INSERT INTO raw_news(url, title, content, author, publish_date) VALUES (?, ?, ?, ?, ?)",
            ("https://example.test/a", " 肯尼亚项目 ", " 正文证据 ", "记者", " 2026-08-01 "),
        )


def _create_projects_db(path: Path) -> None:
    """Create the minimum extracted-project schema and one unimported record."""
    with sqlite3.connect(path) as conn:
        conn.execute(
            """CREATE TABLE extracted_projects (
                id INTEGER PRIMARY KEY,
                project_name TEXT,
                category TEXT,
                source_link TEXT,
                imported_to_s02a INTEGER DEFAULT 0
            )"""
        )
        conn.execute(
            "INSERT INTO extracted_projects(project_name, category, source_link, imported_to_s02a) VALUES (?, ?, ?, ?)",
            ("提取项目", " 光伏 ", "https://example.test/unimported", 0),
        )


def _create_main_db(path: Path) -> None:
    """Create the minimum canonical, event, and Chinese-entity schemas for one import."""
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """CREATE TABLE canonical_projects (
                id TEXT PRIMARY KEY,
                project_name TEXT,
                category TEXT,
                country TEXT
            );
            CREATE TABLE project_events (
                id TEXT PRIMARY KEY,
                canonical_id TEXT,
                project_stage TEXT,
                source_links TEXT,
                capacity_mw_raw TEXT,
                capacity_mw REAL
            );
            CREATE TABLE chinese_entities (
                id INTEGER PRIMARY KEY,
                event_id TEXT,
                implementing_entity TEXT,
                involvement_type TEXT
            );"""
        )
        conn.execute(
            "INSERT INTO canonical_projects(id, project_name, category, country) VALUES (?, ?, ?, ?)",
            ("KEN-MIX-0001", "肯尼亚项目", " 综合能源 ", "肯尼亚"),
        )
        conn.execute(
            """INSERT INTO project_events(
                id, canonical_id, project_stage, source_links, capacity_mw_raw, capacity_mw
            ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "KEN-MIX-0001-01",
                "KEN-MIX-0001",
                "签约",
                '["https://example.test/a"]',
                "100 MW",
                100.0,
            ),
        )
        conn.execute(
            "INSERT INTO chinese_entities(event_id, implementing_entity, involvement_type) VALUES (?, ?, ?)",
            ("KEN-MIX-0001-01", "中国电建", "承包商"),
        )


@pytest.fixture
def db_fixture(tmp_path: Path) -> DatabaseFixture:
    """Build isolated raw-news, extraction, and main databases for repository tests."""
    raw_news_db = tmp_path / "raw_news.db"
    projects_db = tmp_path / "projects.db"
    main_db = tmp_path / "main.db"
    _create_raw_news_db(raw_news_db)
    _create_projects_db(projects_db)
    _create_main_db(main_db)
    return DatabaseFixture(
        config=StudioConfig.from_paths(
            raw_news_db=raw_news_db,
            projects_db=projects_db,
            main_db=main_db,
            data_dir=tmp_path / "studio",
        ),
        paths=(raw_news_db, projects_db, main_db),
    )


def test_repository_reads_article_and_current_project_without_writes(db_fixture: DatabaseFixture):
    """Read immutable article and imported labels while preserving all source files."""
    before = db_fixture.hashes()
    repo = SourceRepository(db_fixture.config)

    article = repo.load_article("https://example.test/a")
    projects = repo.load_current_labels(article.source_link)

    assert article.title == "肯尼亚项目"
    assert article.content == "正文证据"
    assert article.article_sha256 == article_sha256("肯尼亚项目", "正文证据", "2026-08-01")
    assert projects[0].category == "综合能源"
    assert projects[0].raw_values["category"] == " 综合能源 "
    assert projects[0].display_values["category"] == "综合能源"
    assert projects[0].entities[0]["implementing_entity"] == "中国电建"
    assert db_fixture.hashes() == before


def test_repository_reads_unimported_extracted_project_without_changing_import_flag(
    db_fixture: DatabaseFixture,
):
    """Expose extraction labels without changing the pipeline import marker."""
    repo = SourceRepository(db_fixture.config)

    projects = repo.load_current_labels("https://example.test/unimported")

    assert projects[0].category == "光伏"
    assert projects[0].raw_values["imported_to_s02a"] == 0


def test_repository_connection_rejects_insert(db_fixture: DatabaseFixture):
    """Reject attempted source writes through the repository's raw-news connection."""
    repo = SourceRepository(db_fixture.config)

    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        repo._raw_conn.execute("INSERT INTO raw_news(url) VALUES ('x')")


def test_snapshot_hashes_cover_all_source_databases(db_fixture: DatabaseFixture):
    """Report stable SHA-256 hashes for each immutable source database file."""
    repo = SourceRepository(db_fixture.config)

    assert repo.snapshot_hashes() == db_fixture.hashes()
