"""Read immutable article and current-label snapshots from the Studio's source databases."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
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
    """Decode only valid project-event source-link JSON arrays and reject malformed values."""
    if not isinstance(value, str):
        return ()
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return ()
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
        """Return SHA-256 hashes of each source database's transactional logical state."""
        return {
            self._config.raw_news_db.name: _logical_snapshot_sha256(self._raw_conn),
            self._config.projects_db.name: _logical_snapshot_sha256(self._projects_conn),
            self._config.main_db.name: _logical_snapshot_sha256(self._main_conn),
        }

    def _load_main_projects(self, source_link: str) -> list[CurrentProject]:
        """Map a source URL through event JSON links to canonical labels and entities."""
        event_rows = self._main_conn.execute(
            """SELECT pe.*, cp.project_name AS canonical_project_name,
                      cp.category AS category
               FROM project_events AS pe
               JOIN canonical_projects AS cp ON cp.id = pe.canonical_id
               ORDER BY pe.id ASC"""
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
            """SELECT * FROM extracted_projects
               WHERE source_link = ? AND COALESCE(imported_to_s02a, 0) = 0
               ORDER BY id ASC""",
            (source_link,),
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


def _logical_snapshot_sha256(conn: sqlite3.Connection) -> str:
    """Hash schemas and sorted table rows within one read transaction, including committed WAL pages."""
    owns_transaction = not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN")
    try:
        digest = hashlib.sha256()
        tables = conn.execute(
            """SELECT name, sql FROM sqlite_master
               WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
               ORDER BY name"""
        ).fetchall()
        for table in tables:
            table_name = table["name"]
            _update_hash_value(digest, "table")
            _update_hash_value(digest, table_name)
            _update_hash_value(digest, table["sql"])
            columns = [
                row["name"]
                for row in conn.execute(f"PRAGMA table_info({_quote_identifier(table_name)})")
            ]
            for column in columns:
                _update_hash_value(digest, column)
            query = f"SELECT * FROM {_quote_identifier(table_name)}"
            if columns:
                order_by = ", ".join(_quote_identifier(column) for column in columns)
                query = f"{query} ORDER BY {order_by}"
            for row in conn.execute(query):
                _update_hash_value(digest, "row")
                for value in row:
                    _update_hash_value(digest, value)
        return digest.hexdigest()
    finally:
        if owns_transaction:
            conn.execute("ROLLBACK")


def _quote_identifier(identifier: str) -> str:
    """Quote a SQLite identifier while preserving its exact schema-provided name."""
    return '"' + identifier.replace('"', '""') + '"'


def _update_hash_value(digest: Any, value: Any) -> None:
    """Write one typed SQLite value to a digest without ambiguous text concatenation."""
    if value is None:
        encoded = b"null"
    elif isinstance(value, bytes):
        encoded = b"blob:" + value
    elif isinstance(value, str):
        encoded = b"text:" + value.encode("utf-8")
    elif isinstance(value, int):
        encoded = b"int:" + str(value).encode("ascii")
    elif isinstance(value, float):
        encoded = b"float:" + struct.pack("!d", value)
    else:
        raise TypeError(f"Unsupported SQLite value for logical hashing: {type(value)!r}")
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)
