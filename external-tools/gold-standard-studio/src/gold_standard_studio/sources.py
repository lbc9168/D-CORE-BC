"""Read immutable article and current-label snapshots from the Studio's source databases."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from gold_standard_studio.config import StudioConfig, sqlite_ro_uri


@dataclass(frozen=True)
class ArticleSnapshot:
    """Represent one normalized article view while retaining its exact raw database values."""

    source_link: str
    title: str
    content: str
    publish_date: str
    article_sha256: str
    raw_values: Mapping[str, Any]
    display_values: Mapping[str, Any]


@dataclass(frozen=True)
class CurrentProject:
    """Represent one immutable current project label from the main or extraction database."""

    source_link: str
    canonical_id: str | None
    event_id: str | None
    category: str | None
    raw_values: Mapping[str, Any]
    display_values: Mapping[str, Any]
    entities: tuple[Mapping[str, Any], ...]


def _connect_ro(path: Path) -> sqlite3.Connection:
    """Open a SQLite database in URI and pragma-enforced read-only mode."""
    conn = sqlite3.connect(sqlite_ro_uri(path), uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def article_sha256(title: str, content: str, publish_date: str) -> str:
    """Hash the normalized article identity fields into a stable UTF-8 SHA-256 digest."""
    payload = "\n".join((title.strip(), content.strip(), publish_date.strip()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _freeze_mapping(values: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return an immutable copy of a database-value mapping for snapshot consumers."""
    return MappingProxyType(dict(values))


def _display_value(value: Any) -> Any:
    """Normalize only string whitespace for display while retaining raw values separately."""
    return value.strip() if isinstance(value, str) else value


def _display_values(values: Mapping[str, Any]) -> Mapping[str, Any]:
    """Create an immutable display-oriented view of a raw database row."""
    return _freeze_mapping({key: _display_value(value) for key, value in values.items()})


def _parse_source_links(value: Any) -> tuple[str, ...]:
    """Decode a project-event source-link JSON value without treating malformed data as a match."""
    if not isinstance(value, str):
        return ()
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return (value.strip(),) if value.strip() else ()
    if not isinstance(decoded, list):
        return ()
    return tuple(item for item in decoded if isinstance(item, str))


class SourceRepository:
    """Provide read-only access to immutable articles and their present project labels."""

    def __init__(self, config: StudioConfig) -> None:
        """Open persistent read-only connections for all source databases in a Studio run."""
        self._config = config
        self._raw_conn = _connect_ro(config.raw_news_db)
        self._projects_conn = _connect_ro(config.projects_db)
        self._main_conn = _connect_ro(config.main_db)

    def close(self) -> None:
        """Close the repository's read-only source connections when no longer needed."""
        self._raw_conn.close()
        self._projects_conn.close()
        self._main_conn.close()

    def load_article(self, source_link: str) -> ArticleSnapshot:
        """Load one article by its source URL and return an immutable normalized snapshot."""
        row = self._raw_conn.execute(
            "SELECT * FROM raw_news WHERE url = ?", (source_link,)
        ).fetchone()
        if row is None:
            raise KeyError(f"No raw_news article found for source link: {source_link}")
        raw_values = _freeze_mapping(dict(row))
        display_values = _display_values(raw_values)
        title = _text_value(display_values, "title")
        content = _text_value(display_values, "content")
        publish_date = _text_value(display_values, "publish_date")
        return ArticleSnapshot(
            source_link=_text_value(display_values, "url"),
            title=title,
            content=content,
            publish_date=publish_date,
            article_sha256=article_sha256(title, content, publish_date),
            raw_values=raw_values,
            display_values=display_values,
        )

    def load_current_labels(self, source_link: str) -> list[CurrentProject]:
        """Load authoritative main labels, or unimported extraction labels when none exist."""
        main_projects = self._load_main_projects(source_link)
        return main_projects if main_projects else self._load_extracted_projects(source_link)

    def snapshot_hashes(self) -> dict[str, str]:
        """Return SHA-256 file hashes that pin every database used by the repository."""
        return {
            path.name: _file_sha256(path)
            for path in (
                self._config.raw_news_db,
                self._config.projects_db,
                self._config.main_db,
            )
        }

    def _load_main_projects(self, source_link: str) -> list[CurrentProject]:
        """Map a source URL through event JSON links to canonical labels and entities."""
        event_rows = self._main_conn.execute(
            """SELECT pe.*, cp.project_name AS canonical_project_name,
                      cp.category AS category
               FROM project_events AS pe
               JOIN canonical_projects AS cp ON cp.id = pe.canonical_id"""
        ).fetchall()
        projects: list[CurrentProject] = []
        for event_row in event_rows:
            event_values = dict(event_row)
            if source_link not in _parse_source_links(event_values.get("source_links")):
                continue
            raw_values = _freeze_mapping(event_values)
            display_values = _display_values(raw_values)
            entities = self._load_entities(_text_value(display_values, "id"))
            projects.append(
                CurrentProject(
                    source_link=source_link,
                    canonical_id=_optional_text_value(display_values, "canonical_id"),
                    event_id=_optional_text_value(display_values, "id"),
                    category=_optional_text_value(display_values, "category"),
                    raw_values=raw_values,
                    display_values=display_values,
                    entities=entities,
                )
            )
        return projects

    def _load_extracted_projects(self, source_link: str) -> list[CurrentProject]:
        """Map a source URL directly to extraction labels without changing import status."""
        rows = self._projects_conn.execute(
            "SELECT * FROM extracted_projects WHERE source_link = ?", (source_link,)
        ).fetchall()
        projects: list[CurrentProject] = []
        for row in rows:
            raw_values = _freeze_mapping(dict(row))
            display_values = _display_values(raw_values)
            projects.append(
                CurrentProject(
                    source_link=source_link,
                    canonical_id=None,
                    event_id=None,
                    category=_optional_text_value(display_values, "category"),
                    raw_values=raw_values,
                    display_values=display_values,
                    entities=(),
                )
            )
        return projects

    def _load_entities(self, event_id: str) -> tuple[Mapping[str, Any], ...]:
        """Load immutable Chinese-entity rows belonging to one project event."""
        rows = self._main_conn.execute(
            "SELECT * FROM chinese_entities WHERE event_id = ? ORDER BY id", (event_id,)
        ).fetchall()
        return tuple(_freeze_mapping(dict(row)) for row in rows)


def _text_value(values: Mapping[str, Any], key: str) -> str:
    """Return a required display value as text, treating absent database values as empty text."""
    value = values.get(key)
    return value if isinstance(value, str) else "" if value is None else str(value)


def _optional_text_value(values: Mapping[str, Any], key: str) -> str | None:
    """Return a nonempty display value as optional text for nullable identity fields."""
    value = _text_value(values, key)
    return value or None


def _file_sha256(path: Path) -> str:
    """Hash one source database file without opening it for mutation."""
    return hashlib.sha256(path.read_bytes()).hexdigest()
