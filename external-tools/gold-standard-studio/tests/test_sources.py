"""Test read-only Studio source repositories against representative source databases."""

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
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
    """Create the minimum extracted-project schema with imported and pending labels."""
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
            """CREATE INDEX extracted_projects_source_project_desc
               ON extracted_projects(source_link, project_name DESC)"""
        )
        conn.executemany(
            "INSERT INTO extracted_projects(project_name, category, source_link, imported_to_s02a) VALUES (?, ?, ?, ?)",
            [
                ("已导入项目", "风电", "https://example.test/unimported", 1),
                ("提取项目", " 光伏 ", "https://example.test/unimported", 0),
                ("遗留待导入项目", "储能", "https://example.test/unimported", None),
            ],
        )
        conn.executemany(
            """INSERT INTO extracted_projects(
                id, project_name, category, source_link, imported_to_s02a
            ) VALUES (?, ?, ?, ?, ?)""",
            [
                (30, "z-order", "储能", "https://example.test/ordered-extraction", 0),
                (20, "a-order", "光伏", "https://example.test/ordered-extraction", 0),
            ],
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
        conn.execute(
            "INSERT INTO canonical_projects(id, project_name, category, country) VALUES (?, ?, ?, ?)",
            ("KEN-BAD-0001", "损坏链接项目", "风电", "肯尼亚"),
        )
        conn.execute(
            """INSERT INTO project_events(id, canonical_id, source_links)
               VALUES (?, ?, ?)""",
            ("KEN-BAD-0001-01", "KEN-BAD-0001", "https://example.test/malformed"),
        )
        conn.executemany(
            "INSERT INTO canonical_projects(id, project_name, category, country) VALUES (?, ?, ?, ?)",
            [
                ("ORDER-0001", "后插入项目", "风电", "肯尼亚"),
                ("ORDER-0002", "先插入项目", "光伏", "肯尼亚"),
            ],
        )
        conn.executemany(
            "INSERT INTO project_events(id, canonical_id, source_links) VALUES (?, ?, ?)",
            [
                ("ORDER-02", "ORDER-0002", '["https://example.test/ordered-main"]'),
                ("ORDER-01", "ORDER-0001", '["https://example.test/ordered-main"]'),
            ],
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


def test_repository_lists_sampling_rows_with_source_label_counts_in_one_read_only_pass(
    db_fixture: DatabaseFixture,
):
    """Expose stable raw articles with main-or-extraction label strata for quota-aware Pilot sampling."""
    repo = SourceRepository(db_fixture.config)
    before = db_fixture.hashes()

    rows = repo.list_sampling_rows()

    assert [row.article.source_link for row in rows] == ["https://example.test/a"]
    assert rows[0].category_tags == ("综合能源",)
    assert rows[0].label_count == 1
    assert db_fixture.hashes() == before


def test_repository_reads_unimported_extracted_project_without_changing_import_flag(
    db_fixture: DatabaseFixture,
):
    """Expose extraction labels without changing the pipeline import marker."""
    repo = SourceRepository(db_fixture.config)

    projects = repo.load_current_labels("https://example.test/unimported")

    assert [project.category for project in projects] == ["光伏", "储能"]
    assert [project.raw_values["imported_to_s02a"] for project in projects] == [0, None]


def test_repository_orders_main_labels_by_event_id(db_fixture: DatabaseFixture):
    """Return multiple main labels in deterministic ascending project-event ID order."""
    repo = SourceRepository(db_fixture.config)

    projects = repo.load_current_labels("https://example.test/ordered-main")

    assert [project.event_id for project in projects] == ["ORDER-01", "ORDER-02"]


def test_repository_orders_extraction_labels_by_id(db_fixture: DatabaseFixture):
    """Return pending extraction labels in deterministic ascending extracted-row ID order."""
    repo = SourceRepository(db_fixture.config)

    projects = repo.load_current_labels("https://example.test/ordered-extraction")

    assert [project.category for project in projects] == ["光伏", "储能"]


def test_repository_rejects_malformed_event_source_link_json(db_fixture: DatabaseFixture):
    """Ignore malformed event-link JSON rather than treating raw text as a source URL."""
    repo = SourceRepository(db_fixture.config)

    assert repo.load_current_labels("https://example.test/malformed") == []


def test_repository_connection_rejects_insert(db_fixture: DatabaseFixture):
    """Reject attempted source writes through the repository's raw-news connection."""
    repo = SourceRepository(db_fixture.config)

    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        repo._raw_conn.execute("INSERT INTO raw_news(url) VALUES ('x')")


def test_snapshot_hashes_cover_all_source_databases(db_fixture: DatabaseFixture):
    """Report stable logical hashes for every source database under unchanged data."""
    repo = SourceRepository(db_fixture.config)

    hashes = repo.snapshot_hashes()

    assert set(hashes) == {path.name for path in db_fixture.paths}
    assert all(len(value) == 64 for value in hashes.values())
    assert hashes == repo.snapshot_hashes()


def test_snapshot_hashes_include_committed_wal_content(db_fixture: DatabaseFixture):
    """Hash logical rows so commits stored only in a WAL change the source snapshot."""
    raw_path = db_fixture.config.raw_news_db
    with sqlite3.connect(raw_path) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        before_main_bytes = raw_path.read_bytes()
        before = SourceRepository(db_fixture.config).snapshot_hashes()[raw_path.name]
        writer.execute(
            "INSERT INTO raw_news(url, title, content, publish_date) VALUES (?, ?, ?, ?)",
            ("https://example.test/wal", "WAL 项目", "WAL 正文", "2026-08-02"),
        )
        writer.commit()

        after = SourceRepository(db_fixture.config).snapshot_hashes()[raw_path.name]

    assert raw_path.read_bytes() == before_main_bytes
    assert after != before


def _create_equivalent_raw_news_db(path: Path, rows: list[tuple[int, str, str]]) -> None:
    """Create a raw-news database whose identical rows may be committed in any insertion order."""
    with sqlite3.connect(path) as conn:
        conn.execute(
            """CREATE TABLE raw_news (
                id INTEGER PRIMARY KEY,
                url TEXT UNIQUE,
                title TEXT
            )"""
        )
        conn.executemany("INSERT INTO raw_news(id, url, title) VALUES (?, ?, ?)", rows)


def test_logical_snapshot_hash_ignores_equivalent_row_insertion_order(
    db_fixture: DatabaseFixture, tmp_path: Path
):
    """Produce equal hashes when semantically identical source rows were inserted in reverse order."""
    first_raw_db = tmp_path / "first" / "raw_news.db"
    second_raw_db = tmp_path / "second" / "raw_news.db"
    first_raw_db.parent.mkdir()
    second_raw_db.parent.mkdir()
    rows = [
        (1, "https://example.test/hash-a", "A"),
        (2, "https://example.test/hash-b", "B"),
    ]
    _create_equivalent_raw_news_db(first_raw_db, rows)
    _create_equivalent_raw_news_db(second_raw_db, list(reversed(rows)))
    first_config = StudioConfig.from_paths(
        raw_news_db=first_raw_db,
        projects_db=db_fixture.config.projects_db,
        main_db=db_fixture.config.main_db,
        data_dir=tmp_path / "first-studio",
    )
    second_config = StudioConfig.from_paths(
        raw_news_db=second_raw_db,
        projects_db=db_fixture.config.projects_db,
        main_db=db_fixture.config.main_db,
        data_dir=tmp_path / "second-studio",
    )

    first_hash = SourceRepository(first_config).snapshot_hashes()[first_raw_db.name]
    second_hash = SourceRepository(second_config).snapshot_hashes()[second_raw_db.name]

    assert first_hash == second_hash


def test_snapshots_expose_read_only_mappings(db_fixture: DatabaseFixture):
    """Prevent callers from mutating raw, display, and entity snapshot mappings."""
    repo = SourceRepository(db_fixture.config)
    article = repo.load_article("https://example.test/a")
    project = repo.load_current_labels(article.source_link)[0]

    with pytest.raises(TypeError):
        article.raw_values["title"] = "changed"
    with pytest.raises(TypeError):
        article.display_values["title"] = "changed"
    with pytest.raises(TypeError):
        project.raw_values["category"] = "changed"
    with pytest.raises(TypeError):
        project.display_values["category"] = "changed"
    with pytest.raises(TypeError):
        project.entities[0]["implementing_entity"] = "changed"


def test_repository_serializes_shared_connection_operations_and_close(db_fixture: DatabaseFixture):
    """Keep shared read-only connections coherent while worker threads mix loads and snapshot transactions."""
    repo = SourceRepository(db_fixture.config)

    def read_article() -> str:
        """Load the immutable article title through the shared repository."""
        return repo.load_article("https://example.test/a").title

    def read_labels() -> str:
        """Load the main project category through the shared repository."""
        return repo.load_current_labels("https://example.test/a")[0].category or ""

    def read_hash() -> str:
        """Read the transactional raw-news logical hash through the shared repository."""
        return repo.snapshot_hashes()[db_fixture.config.raw_news_db.name]

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda index: (read_article(), read_labels(), read_hash()), range(40)))

    assert all(result[0] == "肯尼亚项目" and result[1] == "综合能源" and len(result[2]) == 64 for result in results)
    repo.close()
    with pytest.raises(RuntimeError, match="closed"):
        repo.load_article("https://example.test/a")
