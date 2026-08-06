"""Persist Studio review drafts, human decisions, audit history, sessions, and validated backups."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator, Sequence
from uuid import uuid4

from gold_standard_studio.schemas import CandidateBundle, GoldField, GoldProject, RichGoldRecord


_TABLES = (
    "samples",
    "article_drafts",
    "candidate_bundles",
    "field_decisions",
    "structure_revisions",
    "audit_log",
    "review_sessions",
    "studio_meta",
)


@dataclass(frozen=True)
class AuditEntry:
    """Expose one immutable audit event with complete payloads and convenient display values."""

    id: int
    sample_id: str
    action: str
    field_path: str | None
    old_value: Any
    new_value: Any
    actor: str
    created_at: str
    old_payload: Any = None
    new_payload: Any = None


@dataclass(frozen=True)
class StructureRevision:
    """Expose one saved full-project structure revision and its human author."""

    revision: int
    projects: tuple[GoldProject, ...]
    actor: str
    created_at: str


@dataclass(frozen=True)
class QueueItem:
    """Describe an unconfirmed draft available for deterministic review-queue ordering."""

    sample_id: str
    review_status: str
    difficulty_score: float


class ReviewStore:
    """Provide transactional SQLite persistence for isolated Studio human-review workflows."""

    def __init__(self, path: str | Path) -> None:
        """Remember the explicit review-database path without creating or mutating it yet."""
        self.path = Path(path)

    def initialize(self) -> None:
        """Create every review table and metadata marker idempotently before store use."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS samples (
                    sample_id TEXT PRIMARY KEY,
                    article_sha256 TEXT NOT NULL,
                    source_link TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS article_drafts (
                    sample_id TEXT PRIMARY KEY REFERENCES samples(sample_id),
                    payload_json TEXT NOT NULL,
                    review_status TEXT NOT NULL,
                    difficulty_score REAL NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS candidate_bundles (
                    sample_id TEXT PRIMARY KEY REFERENCES samples(sample_id),
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS field_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sample_id TEXT NOT NULL REFERENCES samples(sample_id),
                    field_path TEXT NOT NULL,
                    field_json TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS structure_revisions (
                    sample_id TEXT NOT NULL REFERENCES samples(sample_id),
                    revision INTEGER NOT NULL,
                    projects_json TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (sample_id, revision)
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sample_id TEXT NOT NULL REFERENCES samples(sample_id),
                    action TEXT NOT NULL,
                    field_path TEXT,
                    old_json TEXT,
                    new_json TEXT,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS review_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sample_id TEXT NOT NULL REFERENCES samples(sample_id),
                    actor TEXT NOT NULL,
                    started_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS studio_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO studio_meta(key, value) VALUES (?, ?)",
                ("schema_version", "1"),
            )

    def seed_article(self, article: RichGoldRecord) -> None:
        """Insert one immutable starting draft or verify that a repeated seed is byte-equivalent."""
        payload = _canonical_json(article)
        now = _utc_now()
        with self._immediate_transaction() as conn:
            current = conn.execute(
                "SELECT payload_json FROM article_drafts WHERE sample_id = ?", (article.sample_id,)
            ).fetchone()
            if current is not None:
                if current["payload_json"] != payload:
                    raise ValueError(f"sample '{article.sample_id}' is already seeded with a different draft")
                return
            conn.execute(
                "INSERT INTO samples(sample_id, article_sha256, source_link, created_at) VALUES (?, ?, ?, ?)",
                (article.sample_id, article.article_sha256, article.source_link, now),
            )
            conn.execute(
                """INSERT INTO article_drafts
                   (sample_id, payload_json, review_status, difficulty_score, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    article.sample_id,
                    payload,
                    article.review_status,
                    _difficulty_score(article),
                    now,
                ),
            )
            conn.execute(
                """INSERT INTO structure_revisions(sample_id, revision, projects_json, actor, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (article.sample_id, 1, _canonical_json(article.projects), "system", now),
            )

    def get_article(self, sample_id: str) -> RichGoldRecord:
        """Load and validate the latest canonical Pydantic draft for one sample."""
        with self._connect() as conn:
            return self._load_article(conn, sample_id)

    def save_candidate_bundle(self, bundle: CandidateBundle) -> None:
        """Atomically replace a sample's validated candidate bundle after checking its immutable hash."""
        now = _utc_now()
        payload = _canonical_json(bundle)
        with self._immediate_transaction() as conn:
            article = self._load_article(conn, bundle.sample_id)
            if article.article_sha256 != bundle.article_sha256:
                raise ValueError("candidate bundle article hash does not match the seeded article")
            previous_row = conn.execute(
                "SELECT payload_json FROM candidate_bundles WHERE sample_id = ?", (bundle.sample_id,)
            ).fetchone()
            previous_bundle = (
                CandidateBundle.model_validate_json(previous_row["payload_json"])
                if previous_row is not None
                else None
            )
            conn.execute(
                """INSERT INTO candidate_bundles(sample_id, payload_json, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(sample_id) DO UPDATE SET payload_json = excluded.payload_json,
                   updated_at = excluded.updated_at""",
                (bundle.sample_id, payload, now),
            )
            self._append_audit(
                conn,
                bundle.sample_id,
                "candidate_bundle",
                None,
                previous_bundle,
                bundle,
                "system",
            )

    def get_candidate_bundle(self, sample_id: str) -> CandidateBundle | None:
        """Return a validated candidate bundle when it has been saved for the requested sample."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM candidate_bundles WHERE sample_id = ?", (sample_id,)
            ).fetchone()
        return CandidateBundle.model_validate_json(row["payload_json"]) if row is not None else None

    def get_risk_audit_rate(self, risk_tag: str, *, default_rate: float = 0.10) -> float:
        """Return one persisted risk-tag audit rate or its validated default when no error has escalated it."""
        _validate_audit_rate(default_rate)
        key = _risk_audit_rate_key(risk_tag)
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM studio_meta WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default_rate
        try:
            stored_rate = float(row["value"])
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid stored audit rate for risk tag '{risk_tag}'") from error
        _validate_audit_rate(stored_rate)
        return stored_rate

    def record_audit_error(self, risk_tags: Sequence[str]) -> dict[str, float]:
        """Increase every supplied risk tag's stored audit rate by ten points, capped at one."""
        tags = sorted({_validate_risk_tag(tag) for tag in risk_tags})
        if not tags:
            return {}
        updated: dict[str, float] = {}
        with self._immediate_transaction() as conn:
            for tag in tags:
                key = _risk_audit_rate_key(tag)
                row = conn.execute("SELECT value FROM studio_meta WHERE key = ?", (key,)).fetchone()
                current = 0.10 if row is None else float(row["value"])
                _validate_audit_rate(current)
                rate = min(1.0, round(current + 0.10, 10))
                conn.execute(
                    """INSERT INTO studio_meta(key, value) VALUES (?, ?)
                       ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                    (key, f"{rate:.10g}"),
                )
                updated[tag] = rate
        return updated

    def save_field_decision(
        self, sample_id: str, field_path: str, field: GoldField, *, actor: str
    ) -> RichGoldRecord:
        """Save a validated human field decision and its audit entry in one immediate transaction."""
        _require_actor(actor)
        project_id, field_name = _parse_field_path(field_path)
        now = _utc_now()
        with self._immediate_transaction() as conn:
            article = self._load_article(conn, sample_id)
            project = _find_project(article, project_id)
            if field_name not in project.fields:
                raise ValueError(f"unknown field '{field_path}'")
            old = project.fields[field_name]
            project.fields[field_name] = field
            self._save_article(conn, article, now)
            conn.execute(
                """INSERT INTO field_decisions(sample_id, field_path, field_json, actor, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (sample_id, field_path, _canonical_json(field), actor, now),
            )
            self._append_audit(conn, sample_id, "field_decision", field_path, old, field, actor, now)
            return article

    def adjudicate_field(
        self, sample_id: str, field_path: str, field: GoldField, *, actor: str
    ) -> RichGoldRecord:
        """Atomically replace only a pending field and distinguish the human adjudication in audit history."""
        _require_actor(actor)
        project_id, field_name = _parse_field_path(field_path)
        if field.state == "needs_adjudication":
            raise ValueError("adjudication must select a non-pending field state")
        now = _utc_now()
        with self._immediate_transaction() as conn:
            article = self._load_article(conn, sample_id)
            project = _find_project(article, project_id)
            if field_name not in project.fields:
                raise ValueError(f"unknown field '{field_path}'")
            old = project.fields[field_name]
            if old.state != "needs_adjudication":
                raise ValueError(f"field is not awaiting adjudication: {field_path}")
            project.fields[field_name] = field
            self._save_article(conn, article, now)
            conn.execute(
                """INSERT INTO field_decisions(sample_id, field_path, field_json, actor, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (sample_id, field_path, _canonical_json(field), actor, now),
            )
            self._append_audit(conn, sample_id, "adjudication", field_path, old, field, actor, now)
            return article

    def save_structure(
        self,
        sample_id: str,
        projects: Sequence[GoldProject | dict[str, Any]],
        *,
        base_revision: int,
        actor: str,
    ) -> RichGoldRecord:
        """Replace a draft's ordered project structure only when its supplied revision is current."""
        _require_actor(actor)
        now = _utc_now()
        with self._immediate_transaction() as conn:
            article = self._load_article(conn, sample_id)
            current_revision = self._current_structure_revision(conn, sample_id)
            if base_revision != current_revision:
                raise ValueError(
                    f"stale structure revision: expected {current_revision}, received {base_revision}"
                )
            normalized = _normalize_structure_projects(
                projects,
                current_projects=article.projects,
                historical_ids=self._historical_project_ids(conn, sample_id),
            )
            old_projects = list(article.projects)
            article.projects = normalized
            new_revision = current_revision + 1
            self._save_article(conn, article, now)
            conn.execute(
                """INSERT INTO structure_revisions(sample_id, revision, projects_json, actor, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (sample_id, new_revision, _canonical_json(normalized), actor, now),
            )
            self._append_audit(
                conn, sample_id, "structure_decision", None, old_projects, normalized, actor, now
            )
            return article

    def current_structure_revision(self, sample_id: str) -> int:
        """Return the latest saved project-structure revision for optimistic-concurrency clients."""
        with self._connect() as conn:
            self._load_article(conn, sample_id)
            return self._current_structure_revision(conn, sample_id)

    def list_structure_revisions(self, sample_id: str) -> list[StructureRevision]:
        """List every saved project structure in revision order for an article's complete history."""
        with self._connect() as conn:
            self._load_article(conn, sample_id)
            rows = conn.execute(
                """SELECT revision, projects_json, actor, created_at FROM structure_revisions
                   WHERE sample_id = ? ORDER BY revision""",
                (sample_id,),
            ).fetchall()
        return [
            StructureRevision(
                revision=row["revision"],
                projects=tuple(GoldProject.model_validate(item) for item in json.loads(row["projects_json"])),
                actor=row["actor"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def start_review(self, sample_id: str, *, actor: str) -> None:
        """Record an actor's current article in an immediate transaction so review resumes after restart."""
        _require_actor(actor)
        now = _utc_now()
        with self._immediate_transaction() as conn:
            self._load_article(conn, sample_id)
            conn.execute(
                "INSERT INTO review_sessions(sample_id, actor, started_at) VALUES (?, ?, ?)",
                (sample_id, actor, now),
            )
            self._append_audit(conn, sample_id, "review_started", None, None, sample_id, actor, now)

    def resume_target(self, actor: str) -> str | None:
        """Return the most recently started unconfirmed article for an actor, if any remains."""
        _require_actor(actor)
        with self._connect() as conn:
            row = conn.execute(
                """SELECT review_sessions.sample_id FROM review_sessions
                   JOIN article_drafts USING(sample_id)
                   WHERE review_sessions.actor = ? AND article_drafts.review_status != 'reviewed'
                   ORDER BY review_sessions.id DESC LIMIT 1""",
                (actor,),
            ).fetchone()
        return row["sample_id"] if row is not None else None

    def confirm_article(self, sample_id: str, *, actor: str) -> RichGoldRecord:
        """Mark a conflict-free, publishable draft reviewed and audit the human confirmation atomically."""
        _require_actor(actor)
        now = _utc_now()
        with self._immediate_transaction() as conn:
            article = self._load_article(conn, sample_id)
            if _has_unresolved_conflicts(article):
                raise ValueError("unresolved conflicts prevent article confirmation")
            previous_status = article.review_status
            article.article_review_status = "reviewed"
            article.assert_publishable()
            self._save_article(conn, article, now)
            self._append_audit(
                conn,
                sample_id,
                "article_confirmed",
                "article_review_status",
                previous_status,
                article.review_status,
                actor,
                now,
            )
            return article

    def list_queue(self) -> list[QueueItem]:
        """List outstanding drafts by low difficulty first with sample ID as a deterministic tie-breaker."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT sample_id, review_status, difficulty_score FROM article_drafts
                   WHERE review_status != 'reviewed'
                   ORDER BY difficulty_score, sample_id"""
            ).fetchall()
        return [
            QueueItem(
                sample_id=row["sample_id"],
                review_status=row["review_status"],
                difficulty_score=row["difficulty_score"],
            )
            for row in rows
        ]

    def list_audit(self, sample_id: str) -> list[AuditEntry]:
        """Return a complete append-only audit history for the requested article in event order."""
        with self._connect() as conn:
            self._load_article(conn, sample_id)
            rows = conn.execute(
                """SELECT id, sample_id, action, field_path, old_json, new_json, actor, created_at
                   FROM audit_log WHERE sample_id = ? ORDER BY id""",
                (sample_id,),
            ).fetchall()
        return [_decode_audit_entry(row) for row in rows]

    def latest_audit_id(self) -> int:
        """Return the audit tail ID for backup validation and external consistency checks."""
        with self._connect() as conn:
            return self._latest_audit_id(conn)

    def table_row_counts(self) -> dict[str, int]:
        """Return exact row counts for every review table to validate a copied SQLite snapshot."""
        with self._connect() as conn:
            return self._table_row_counts(conn)

    def backup(self, destination: str | Path | None = None) -> Path:
        """Create a SQLite-native snapshot and reject it unless integrity, counts, and audit tail match."""
        target = Path(destination) if destination is not None else self._next_backup_path()
        if target.resolve() == self.path.resolve():
            raise ValueError("backup destination must differ from the review database")
        if target.exists():
            raise FileExistsError(f"backup destination already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            with self._connect() as source:
                source.execute("BEGIN")
                source_counts = self._table_row_counts(source)
                source_tail = self._latest_audit_id(source)
                copied = sqlite3.connect(temporary)
                try:
                    source.backup(copied)
                    copied.commit()
                finally:
                    copied.close()
                source.rollback()
            copied_store = ReviewStore(temporary)
            with copied_store._connect() as conn:
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"backup integrity check failed: {integrity}")
            if copied_store.table_row_counts() != source_counts:
                raise RuntimeError("backup row counts do not match source database")
            if copied_store.latest_audit_id() != source_tail:
                raise RuntimeError("backup audit tail does not match source database")
            temporary.rename(target)
            return target
        except Exception:
            if temporary.exists():
                temporary.unlink()
            raise

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield and close a short-lived connection configured for foreign keys and contention tolerance."""
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def _immediate_transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection protected by BEGIN IMMEDIATE and roll back every failed human write."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except Exception:
                conn.rollback()
                raise
            else:
                conn.commit()

    def _load_article(self, conn: sqlite3.Connection, sample_id: str) -> RichGoldRecord:
        """Load one draft through Pydantic validation or raise a stable unknown-sample error."""
        row = conn.execute(
            "SELECT payload_json FROM article_drafts WHERE sample_id = ?", (sample_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown sample '{sample_id}'")
        return RichGoldRecord.model_validate_json(row["payload_json"])

    def _save_article(self, conn: sqlite3.Connection, article: RichGoldRecord, now: str) -> None:
        """Write a complete canonical Pydantic draft while keeping indexed queue fields synchronized."""
        conn.execute(
            """UPDATE article_drafts SET payload_json = ?, review_status = ?, difficulty_score = ?, updated_at = ?
               WHERE sample_id = ?""",
            (
                _canonical_json(article),
                article.review_status,
                _difficulty_score(article),
                now,
                article.sample_id,
            ),
        )

    def _append_audit(
        self,
        conn: sqlite3.Connection,
        sample_id: str,
        action: str,
        field_path: str | None,
        old_value: Any,
        new_value: Any,
        actor: str,
        created_at: str | None = None,
    ) -> None:
        """Append an immutable canonical JSON audit event within the caller's active transaction."""
        conn.execute(
            """INSERT INTO audit_log(sample_id, action, field_path, old_json, new_json, actor, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                sample_id,
                action,
                field_path,
                _canonical_json(old_value) if old_value is not None else None,
                _canonical_json(new_value) if new_value is not None else None,
                actor,
                created_at or _utc_now(),
            ),
        )

    def _current_structure_revision(self, conn: sqlite3.Connection, sample_id: str) -> int:
        """Read the latest revision number while the caller controls transaction consistency."""
        row = conn.execute(
            "SELECT COALESCE(MAX(revision), 0) AS revision FROM structure_revisions WHERE sample_id = ?",
            (sample_id,),
        ).fetchone()
        return int(row["revision"])

    def _next_backup_path(self) -> Path:
        """Choose a new timestamped backup path without overwriting any prior Studio snapshot."""
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        return self.path.with_name(f"{self.path.stem}.backup-{timestamp}{self.path.suffix}")

    def _historical_project_ids(self, conn: sqlite3.Connection, sample_id: str) -> set[str]:
        """Collect all project IDs ever persisted so deleted IDs cannot later point to new projects."""
        rows = conn.execute(
            "SELECT projects_json FROM structure_revisions WHERE sample_id = ?", (sample_id,)
        ).fetchall()
        return {
            project["gold_project_id"]
            for row in rows
            for project in json.loads(row["projects_json"])
        }

    def _latest_audit_id(self, conn: sqlite3.Connection) -> int:
        """Read the audit tail from an existing connection so a backup observes one SQLite snapshot."""
        row = conn.execute("SELECT COALESCE(MAX(id), 0) AS id FROM audit_log").fetchone()
        return int(row["id"])

    def _table_row_counts(self, conn: sqlite3.Connection) -> dict[str, int]:
        """Count every store table through one caller-controlled SQLite snapshot connection."""
        return {table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in _TABLES}


def _canonical_json(value: Any) -> str:
    """Serialize Pydantic values and nested primitives into stable compact JSON for durable comparison."""
    return json.dumps(
        _json_ready(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _json_ready(value: Any) -> Any:
    """Recursively convert nested Pydantic models to JSON-compatible primitives before canonical encoding."""
    if hasattr(value, "model_dump"):
        return _json_ready(value.model_dump(mode="json", by_alias=True, exclude_none=False))
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def _utc_now() -> str:
    """Return an explicit UTC ISO timestamp suitable for append-only ordering metadata."""
    return datetime.now(UTC).isoformat()


def _difficulty_score(article: RichGoldRecord) -> float:
    """Extract a sortable numeric difficulty score while assigning unscored drafts the lowest priority score."""
    return article.difficulty.score if article.difficulty is not None else 0.0


def _require_actor(actor: str) -> None:
    """Reject blank actors so every human review mutation remains attributable in audit history."""
    if not actor.strip():
        raise ValueError("actor must not be blank")


def _risk_audit_rate_key(risk_tag: str) -> str:
    """Build the private metadata namespace key for one explicitly validated risk tag."""
    return f"audit_rate:{_validate_risk_tag(risk_tag)}"


def _validate_risk_tag(risk_tag: str) -> str:
    """Reject blank or control-character risk tags before they can enter shared Studio metadata."""
    if not isinstance(risk_tag, str) or not risk_tag.strip() or any(character.isspace() for character in risk_tag):
        raise ValueError("risk tag must be a nonblank token without whitespace")
    return risk_tag


def _validate_audit_rate(rate: float) -> None:
    """Reject invalid persisted or caller-default audit rates before they influence review expansion."""
    if not isinstance(rate, (int, float)) or not 0 <= rate <= 1:
        raise ValueError("audit rate must be between 0 and 1")


def _parse_field_path(field_path: str) -> tuple[str, str]:
    """Validate a two-segment project-field path before it can select any nested draft value."""
    project_id, separator, field_name = field_path.partition(".")
    if not separator or not project_id or not field_name or "." in field_name:
        raise ValueError("field path must be '<gold_project_id>.<field_name>'")
    if not all(character.isalnum() or character in "_-" for character in project_id + field_name):
        raise ValueError("field path contains unsafe characters")
    return project_id, field_name


def _find_project(article: RichGoldRecord, project_id: str) -> GoldProject:
    """Find one stable project ID in a draft or reject a path that crosses the article boundary."""
    for project in article.projects:
        if project.gold_project_id == project_id:
            return project
    raise ValueError(f"unknown project '{project_id}'")


def _assert_unique_project_ids(projects: Sequence[GoldProject]) -> None:
    """Reject duplicate stable identifiers before a structure revision could create ambiguous field paths."""
    identifiers = [project.gold_project_id for project in projects]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("structure contains duplicate gold_project_id values")


def _normalize_structure_projects(
    projects: Sequence[GoldProject | dict[str, Any]],
    *,
    current_projects: Sequence[GoldProject],
    historical_ids: set[str],
) -> list[GoldProject]:
    """Validate requested projects while assigning fresh IDs for absent or deleted historical identifiers."""
    current_ids = {project.gold_project_id for project in current_projects}
    reserved_ids = set(historical_ids)
    normalized: list[GoldProject] = []
    for project in projects:
        payload = project.model_dump(mode="json") if isinstance(project, GoldProject) else dict(project)
        requested_id = payload.get("gold_project_id")
        if not requested_id or (requested_id in historical_ids and requested_id not in current_ids):
            payload["gold_project_id"] = _next_project_id(reserved_ids)
        reserved_ids.add(payload["gold_project_id"])
        normalized.append(GoldProject.model_validate(payload))
    _assert_unique_project_ids(normalized)
    return normalized


def _next_project_id(reserved_ids: set[str]) -> str:
    """Return the lowest unused PNN identifier without ever reusing an ID saved in earlier revisions."""
    number = 1
    while True:
        candidate = f"P{number:02d}"
        if candidate not in reserved_ids:
            return candidate
        number += 1


def _has_unresolved_conflicts(article: RichGoldRecord) -> bool:
    """Return whether any formal field is explicitly held for adjudication before confirmation."""
    return any(
        field.state == "needs_adjudication"
        for project in article.projects
        for field in project.fields.values()
    )


def _decode_audit_entry(row: sqlite3.Row) -> AuditEntry:
    """Build one audit entry whose rich payloads and display values describe the same persisted event."""
    old_payload = _decode_audit_payload(row["action"], row["old_json"])
    new_payload = _decode_audit_payload(row["action"], row["new_json"])
    return AuditEntry(
        id=row["id"],
        sample_id=row["sample_id"],
        action=row["action"],
        field_path=row["field_path"],
        old_payload=old_payload,
        new_payload=new_payload,
        old_value=_audit_display_value(old_payload),
        new_value=_audit_display_value(new_payload),
        actor=row["actor"],
        created_at=row["created_at"],
    )


def _decode_audit_payload(action: str, encoded: str | None) -> Any:
    """Decode stored canonical JSON into its known Pydantic shape or unchanged generic JSON data."""
    if encoded is None:
        return None
    value = json.loads(encoded)
    if action == "field_decision":
        return GoldField.model_validate(value)
    if action == "candidate_bundle":
        return CandidateBundle.model_validate(value)
    return value


def _audit_display_value(payload: Any) -> Any:
    """Expose a compact legacy-friendly value while retaining the complete payload alongside it."""
    if isinstance(payload, GoldField):
        return payload.value
    if hasattr(payload, "model_dump"):
        return payload.model_dump(mode="json")
    return payload
