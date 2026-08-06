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

from gold_standard_studio.config import assert_mutable_paths_isolated
from gold_standard_studio.evidence import structural_conflicts
from gold_standard_studio.field_registry import FIELD_REGISTRY, validate_entity_role, validate_field_value
from gold_standard_studio.schemas import (
    ArticleGoldStatus,
    CandidateBundle,
    DifficultyScore,
    GoldEntity,
    GoldField,
    GoldProject,
    RichGoldRecord,
)
from gold_standard_studio.sources import ArticleSnapshot, article_sha256


_TABLES = (
    "samples",
    "article_snapshots",
    "article_drafts",
    "candidate_bundles",
    "sample_scoring",
    "field_decisions",
    "structure_revisions",
    "audit_log",
    "review_sessions",
    "studio_meta",
)
_SCHEMA_VERSION = 3
_PUBLISHABLE_ARTICLE_STATUSES = {"valid", "no_chinese", "irrelevant", "chinese_unnamed"}


class ReviewedRecordError(ValueError):
    """Signal an attempted mutation of a confirmed record whose gold draft is permanently immutable."""


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


@dataclass(frozen=True)
class SampleItem:
    """Describe every persisted sample with observational scoring and the immutable draft dimensions used in reports."""

    sample_id: str
    review_status: str
    difficulty_score: float
    risk_tags: tuple[str, ...]
    dataset_split: str | None


class ReviewStore:
    """Provide transactional SQLite persistence for isolated Studio human-review workflows."""

    def __init__(self, path: str | Path, *, source_paths: Sequence[str | Path] = ()) -> None:
        """Remember the review path and immutable sources without creating or mutating anything yet."""
        self.path = Path(path)
        self._source_paths = tuple(Path(source) for source in source_paths)

    @property
    def schema_version(self) -> int:
        """Expose the latest supported ordered review schema version for diagnostics and migrations."""
        return _SCHEMA_VERSION

    @property
    def source_paths(self) -> tuple[Path, ...]:
        """Expose immutable source identities for publisher, backup, and report path rechecks."""
        return self._source_paths

    def initialize(self) -> None:
        """Create or transactionally migrate the review schema in order while rejecting future versions."""
        self._assert_review_path_isolated()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            conn.execute("BEGIN IMMEDIATE")
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            if not tables:
                for statement in _V1_SCHEMA:
                    conn.execute(statement)
                conn.execute("INSERT INTO studio_meta(key, value) VALUES ('schema_version', '1')")
                version = 1
            else:
                if "studio_meta" not in tables:
                    raise RuntimeError("review database has tables but no schema version metadata")
                row = conn.execute("SELECT value FROM studio_meta WHERE key='schema_version'").fetchone()
                if row is None:
                    raise RuntimeError("review database is missing schema version metadata")
                try:
                    version = int(row["value"])
                except (TypeError, ValueError) as error:
                    raise RuntimeError("review database has an invalid schema version") from error
            if version > _SCHEMA_VERSION:
                raise RuntimeError(
                    f"future review schema version {version} is unsupported; latest is {_SCHEMA_VERSION}"
                )
            while version < _SCHEMA_VERSION:
                migration = _MIGRATIONS.get(version)
                if migration is None:
                    raise RuntimeError(f"no ordered migration exists from review schema version {version}")
                migration(conn)
                version += 1
                conn.execute("UPDATE studio_meta SET value=? WHERE key='schema_version'", (str(version),))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def seed_article(self, article: RichGoldRecord, *, snapshot: ArticleSnapshot | None = None) -> None:
        """Atomically seed one immutable starting draft and its optional canonical article snapshot."""
        if snapshot is not None:
            _validate_snapshot_identity(article, snapshot)
        payload = _canonical_json(article)
        now = _utc_now()
        with self._immediate_transaction() as conn:
            current = conn.execute(
                "SELECT payload_json FROM article_drafts WHERE sample_id = ?", (article.sample_id,)
            ).fetchone()
            if current is not None:
                if current["payload_json"] != payload:
                    raise ValueError(f"sample '{article.sample_id}' is already seeded with a different draft")
                if snapshot is not None:
                    self._insert_or_verify_snapshot(conn, article.sample_id, snapshot, now)
                return
            conn.execute(
                "INSERT INTO samples(sample_id, article_sha256, source_link, created_at) VALUES (?, ?, ?, ?)",
                (article.sample_id, article.article_sha256, article.source_link, now),
            )
            conn.execute(
                """INSERT INTO article_drafts
                   (sample_id, payload_json, review_status, article_status, difficulty_score, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    article.sample_id,
                    payload,
                    article.review_status,
                    article.article_status,
                    _difficulty_score(article),
                    now,
                ),
            )
            if snapshot is not None:
                self._insert_or_verify_snapshot(conn, article.sample_id, snapshot, now)
            conn.execute(
                """INSERT INTO structure_revisions(sample_id, revision, projects_json, actor, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (article.sample_id, 1, _canonical_json(article.projects), "system", now),
            )

    def get_article_snapshot(self, sample_id: str) -> ArticleSnapshot | None:
        """Return the canonical article snapshot stored atomically with review state, when available."""
        with self._connect() as conn:
            article = self._load_article(conn, sample_id)
            row = conn.execute(
                """SELECT title, content, publish_date, article_sha256
                   FROM article_snapshots WHERE sample_id = ?""",
                (sample_id,),
            ).fetchone()
        if row is None:
            return None
        return ArticleSnapshot(
            source_link=article.source_link or "",
            title=row["title"],
            content=row["content"],
            publish_date=row["publish_date"],
            article_sha256=row["article_sha256"],
            raw_values={},
            display_values={},
        )

    def _insert_or_verify_snapshot(
        self, conn: sqlite3.Connection, sample_id: str, snapshot: ArticleSnapshot, now: str
    ) -> None:
        """Insert one immutable snapshot or reject a repeated seed whose canonical bytes differ."""
        current = conn.execute(
            "SELECT title, content, publish_date, article_sha256 FROM article_snapshots WHERE sample_id=?",
            (sample_id,),
        ).fetchone()
        values = (snapshot.title, snapshot.content, snapshot.publish_date, snapshot.article_sha256)
        if current is not None:
            if tuple(current) != values:
                raise ValueError(f"sample '{sample_id}' already has a different immutable article snapshot")
            return
        conn.execute(
            """INSERT INTO article_snapshots
               (sample_id, title, content, publish_date, article_sha256, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (sample_id, *values, now),
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

    def has_candidate_bundle(self, sample_id: str) -> bool:
        """Return whether one sample already has a saved candidate bundle regardless of review status."""
        with self._connect() as conn:
            self._load_article(conn, sample_id)
            return conn.execute(
                "SELECT 1 FROM candidate_bundles WHERE sample_id = ?", (sample_id,)
            ).fetchone() is not None

    def update_sample_scoring(self, sample_id: str, difficulty: DifficultyScore) -> None:
        """Persist observational candidate scoring and an audit event without modifying or reopening any gold draft."""
        now = _utc_now()
        with self._immediate_transaction() as conn:
            self._load_article(conn, sample_id)
            previous_row = conn.execute(
                """SELECT difficulty_score, reason_codes_json, risk_tags_json
                   FROM sample_scoring WHERE sample_id = ?""",
                (sample_id,),
            ).fetchone()
            previous = (
                DifficultyScore(
                    score=previous_row["difficulty_score"],
                    reason_codes=json.loads(previous_row["reason_codes_json"]),
                    risk_tags=json.loads(previous_row["risk_tags_json"]),
                )
                if previous_row is not None
                else None
            )
            conn.execute(
                """INSERT INTO sample_scoring(sample_id, difficulty_score, reason_codes_json, risk_tags_json, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(sample_id) DO UPDATE SET difficulty_score = excluded.difficulty_score,
                   reason_codes_json = excluded.reason_codes_json, risk_tags_json = excluded.risk_tags_json,
                   updated_at = excluded.updated_at""",
                (
                    sample_id,
                    difficulty.score,
                    _canonical_json(difficulty.reason_codes),
                    _canonical_json(difficulty.risk_tags),
                    now,
                ),
            )
            self._append_audit(conn, sample_id, "sample_scoring", None, previous, difficulty, "system", now)

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
            _assert_draft_mutable(article)
            project = _find_project(article, project_id)
            if field_name not in project.fields:
                raise ValueError(f"unknown field '{field_path}'")
            validate_field_value(field_name, field.state, field.value)
            old = project.fields[field_name]
            if old.state == "needs_adjudication" and field.state != "needs_adjudication":
                raise ValueError(f"field resolution requires adjudication: {field_path}")
            project.fields[field_name] = field
            self._save_article(conn, article, now)
            conn.execute(
                """INSERT INTO field_decisions(sample_id, field_path, field_json, actor, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (sample_id, field_path, _canonical_json(field), actor, now),
            )
            self._append_audit(conn, sample_id, "field_decision", field_path, old, field, actor, now)
            return article

    def save_article_status(
        self, sample_id: str, article_status: ArticleGoldStatus, *, actor: str
    ) -> RichGoldRecord:
        """Persist an explicit human article gold-status decision separately from workflow status."""
        _require_actor(actor)
        if article_status not in _PUBLISHABLE_ARTICLE_STATUSES:
            raise ValueError("article gold status must be an explicit publishable value")
        now = _utc_now()
        with self._immediate_transaction() as conn:
            article = self._load_article(conn, sample_id)
            _assert_draft_mutable(article)
            if article_status != "valid" and article.projects:
                raise ValueError("negative article status requires an empty project structure")
            old = article.article_status
            article.article_status = article_status
            self._save_article(conn, article, now)
            self._append_audit(
                conn, sample_id, "article_status_decision", "article_status", old, article_status, actor, now
            )
            return article

    def save_entities(
        self,
        sample_id: str,
        project_id: str,
        entities: Sequence[GoldEntity | dict[str, Any]],
        *,
        actor: str,
    ) -> RichGoldRecord:
        """Replace one project's Chinese entities, roles, and evidence in one audited human decision."""
        _require_actor(actor)
        normalized = [
            entity if isinstance(entity, GoldEntity) else GoldEntity.model_validate(entity)
            for entity in entities
        ]
        for entity in normalized:
            validate_entity_role(entity.role)
        now = _utc_now()
        with self._immediate_transaction() as conn:
            article = self._load_article(conn, sample_id)
            _assert_draft_mutable(article)
            project = _find_project(article, project_id)
            old = list(project.entities)
            project.entities = normalized
            self._save_article(conn, article, now)
            self._append_audit(
                conn,
                sample_id,
                "entity_decision",
                f"{project_id}.entities",
                old,
                normalized,
                actor,
                now,
            )
            return article

    def adjudicate_field(
        self, sample_id: str, field_path: str, field: GoldField, *, actor: str
    ) -> RichGoldRecord:
        """Atomically replace only a pending field and distinguish the human adjudication in audit history."""
        _require_actor(actor)
        project_id, field_name = _parse_field_path(field_path)
        now = _utc_now()
        with self._immediate_transaction() as conn:
            article = self._load_article(conn, sample_id)
            _assert_draft_mutable(article)
            if field.state == "needs_adjudication":
                raise ValueError("adjudication must select a non-pending field state")
            project = _find_project(article, project_id)
            if field_name not in project.fields:
                raise ValueError(f"unknown field '{field_path}'")
            validate_field_value(field_name, field.state, field.value)
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
            _assert_draft_mutable(article)
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
            article = self._load_article(conn, sample_id)
            _assert_draft_mutable(article)
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
            self._assert_structure_gate_resolved(conn, sample_id)
            self._assert_article_status_audited(conn, sample_id, article.article_status)
            snapshot = self._load_article_snapshot(conn, sample_id, article)
            _assert_confirmation_guards(article, snapshot)
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

    def confirm_negative(
        self, sample_id: str, article_status: ArticleGoldStatus, *, actor: str
    ) -> RichGoldRecord:
        """Atomically set one explicit negative gold status and confirm it only through all server gates."""
        _require_actor(actor)
        if article_status not in {"no_chinese", "irrelevant", "chinese_unnamed"}:
            raise ValueError("negative confirmation requires no_chinese, irrelevant, or chinese_unnamed")
        now = _utc_now()
        with self._immediate_transaction() as conn:
            article = self._load_article(conn, sample_id)
            _assert_draft_mutable(article)
            self._assert_structure_gate_resolved(conn, sample_id)
            if article.projects:
                raise ValueError("negative article confirmation requires an empty project structure")
            snapshot = self._load_article_snapshot(conn, sample_id, article)
            _validate_snapshot_identity(article, snapshot)
            previous_gold_status = article.article_status
            article.article_status = article_status
            self._append_audit(
                conn,
                sample_id,
                "article_status_decision",
                "article_status",
                previous_gold_status,
                article_status,
                actor,
                now,
            )
            previous_review_status = article.review_status
            article.article_review_status = "reviewed"
            article.assert_publishable()
            self._save_article(conn, article, now)
            self._append_audit(
                conn,
                sample_id,
                "article_confirmed",
                "article_review_status",
                previous_review_status,
                article.review_status,
                actor,
                now,
            )
            return article

    def structure_gate_status(self, sample_id: str) -> tuple[bool, bool]:
        """Return whether a current structural candidate conflict requires and has a persisted newer decision."""
        with self._connect() as conn:
            self._load_article(conn, sample_id)
            return self._structure_gate_status(conn, sample_id)

    def list_queue(self) -> list[QueueItem]:
        """List outstanding drafts by low difficulty first with sample ID as a deterministic tie-breaker."""
        return [
            QueueItem(
                sample_id=item.sample_id,
                review_status=item.review_status,
                difficulty_score=item.difficulty_score,
            )
            for item in self.list_all_samples(review_status="unreviewed")
        ]

    def list_all_samples(self, *, review_status: str | None = None) -> list[SampleItem]:
        """List every sample or one explicit review status using persisted scoring in deterministic queue order."""
        if review_status not in {None, "unreviewed", "reviewed"}:
            raise ValueError("review_status must be unreviewed, reviewed, or None")
        where = "" if review_status is None else "WHERE drafts.review_status = ?"
        params: tuple[str, ...] = () if review_status is None else (review_status,)
        with self._connect() as conn:
            rows = conn.execute(
                f"""SELECT drafts.sample_id, drafts.review_status, drafts.payload_json,
                           COALESCE(scoring.difficulty_score, drafts.difficulty_score) AS difficulty_score,
                           scoring.risk_tags_json
                    FROM article_drafts AS drafts
                    LEFT JOIN sample_scoring AS scoring USING(sample_id)
                    {where}
                    ORDER BY difficulty_score, drafts.sample_id""",
                params,
            ).fetchall()
        items: list[SampleItem] = []
        for row in rows:
            article = RichGoldRecord.model_validate_json(row["payload_json"])
            risk_tags = (
                tuple(json.loads(row["risk_tags_json"]))
                if row["risk_tags_json"] is not None
                else tuple(article.risk_tags)
            )
            items.append(
                SampleItem(
                    sample_id=row["sample_id"],
                    review_status=row["review_status"],
                    difficulty_score=row["difficulty_score"],
                    risk_tags=risk_tags,
                    dataset_split=article.dataset_split,
                )
            )
        return items

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
        if self._source_paths:
            assert_mutable_paths_isolated(self._source_paths, [target, target.parent])
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
        self._assert_review_path_isolated()
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            _assert_not_future_schema(conn)
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

    def _load_article_snapshot(
        self, conn: sqlite3.Connection, sample_id: str, article: RichGoldRecord
    ) -> ArticleSnapshot:
        """Load the required immutable source snapshot within the caller's confirmation transaction."""
        row = conn.execute(
            """SELECT title, content, publish_date, article_sha256
               FROM article_snapshots WHERE sample_id = ?""",
            (sample_id,),
        ).fetchone()
        if row is None:
            raise ValueError("immutable article snapshot is required for confirmation")
        snapshot = ArticleSnapshot(
            source_link=article.source_link or "",
            title=row["title"],
            content=row["content"],
            publish_date=row["publish_date"],
            article_sha256=row["article_sha256"],
            raw_values={},
            display_values={},
        )
        _validate_snapshot_identity(article, snapshot)
        return snapshot

    def _save_article(self, conn: sqlite3.Connection, article: RichGoldRecord, now: str) -> None:
        """Write a complete canonical Pydantic draft while keeping indexed queue fields synchronized."""
        conn.execute(
            """UPDATE article_drafts SET payload_json = ?, review_status = ?, article_status = ?,
               difficulty_score = ?, updated_at = ?
               WHERE sample_id = ?""",
            (
                _canonical_json(article),
                article.review_status,
                article.article_status,
                _difficulty_score(article),
                now,
                article.sample_id,
            ),
        )

    def _assert_review_path_isolated(self) -> None:
        """Recheck the review DB filesystem identity before every open when immutable sources are known."""
        if self._source_paths:
            assert_mutable_paths_isolated(self._source_paths, [self.path])

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

    def _structure_gate_status(self, conn: sqlite3.Connection, sample_id: str) -> tuple[bool, bool]:
        """Compare monotonic audit IDs for the latest structural candidate and human structure decision."""
        row = conn.execute("SELECT payload_json FROM candidate_bundles WHERE sample_id = ?", (sample_id,)).fetchone()
        if row is None or not structural_conflicts(CandidateBundle.model_validate_json(row["payload_json"])):
            return False, True
        candidate = conn.execute(
            "SELECT id FROM audit_log WHERE sample_id = ? AND action = 'candidate_bundle' ORDER BY id DESC LIMIT 1",
            (sample_id,),
        ).fetchone()
        structure = conn.execute(
            "SELECT id FROM audit_log WHERE sample_id = ? AND action = 'structure_decision' ORDER BY id DESC LIMIT 1",
            (sample_id,),
        ).fetchone()
        return True, candidate is not None and structure is not None and structure["id"] > candidate["id"]

    def _assert_structure_gate_resolved(self, conn: sqlite3.Connection, sample_id: str) -> None:
        """Reject confirmation before an explicit structure revision resolves the latest structural candidate conflict."""
        required, resolved = self._structure_gate_status(conn, sample_id)
        if required and not resolved:
            raise ValueError("unresolved structure conflicts prevent article confirmation")

    def _assert_article_status_audited(
        self, conn: sqlite3.Connection, sample_id: str, article_status: str
    ) -> None:
        """Require the current authoritative article status to come from an explicit audited human action."""
        row = conn.execute(
            """SELECT new_json FROM audit_log
               WHERE sample_id=? AND action='article_status_decision'
               ORDER BY id DESC LIMIT 1""",
            (sample_id,),
        ).fetchone()
        if row is None or json.loads(row["new_json"]) != article_status:
            raise ValueError("article gold status requires an audited human decision")

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
        if (
            not requested_id
            or str(requested_id).startswith("NEW")
            or (requested_id in historical_ids and requested_id not in current_ids)
        ):
            payload["gold_project_id"] = _next_project_id(reserved_ids)
        reserved_ids.add(payload["gold_project_id"])
        supplied_fields = payload.get("fields") or {}
        unknown = sorted(set(supplied_fields).difference(FIELD_REGISTRY))
        if unknown:
            raise ValueError(f"project contains unknown registry fields: {unknown}")
        payload["fields"] = {
            name: supplied_fields.get(
                name,
                GoldField(
                    state="unreviewed",
                    evidence_required=specification.evidence_required,
                ).model_dump(mode="json"),
            )
            for name, specification in FIELD_REGISTRY.items()
        }
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


def _assert_confirmation_guards(article: RichGoldRecord, snapshot: ArticleSnapshot) -> None:
    """Reject incomplete status, structure, typed values, entities, and nonexact evidence inside confirmation."""
    _assert_draft_mutable(article)
    if _has_unresolved_conflicts(article):
        raise ValueError("unresolved conflicts prevent article confirmation")
    if article.article_status not in _PUBLISHABLE_ARTICLE_STATUSES:
        raise ValueError("article gold status must be explicitly decided before confirmation")
    if article.article_status == "valid" and not article.projects:
        raise ValueError("valid article requires at least one project")
    if article.article_status != "valid" and article.projects:
        raise ValueError("negative article status requires an empty project structure")
    _validate_snapshot_identity(article, snapshot)
    for project in article.projects:
        missing = sorted(set(FIELD_REGISTRY).difference(project.fields))
        extra = sorted(set(project.fields).difference(FIELD_REGISTRY))
        if missing or extra:
            raise ValueError(f"project field registry mismatch: missing={missing}; extra={extra}")
        for field_name, field in project.fields.items():
            specification = FIELD_REGISTRY[field_name]
            if field.state in {"unreviewed", "needs_adjudication"}:
                raise ValueError(f"incomplete field state: {project.gold_project_id}.{field_name}")
            validate_field_value(field_name, field.state, field.value)
            if specification.evidence_required and field.state == "present" and not field.evidence:
                raise ValueError(f"missing required evidence: {project.gold_project_id}.{field_name}")
            for evidence in field.evidence:
                _validate_evidence_span(
                    evidence, snapshot, f"{project.gold_project_id}.{field_name}"
                )
        for index, entity in enumerate(project.entities):
            validate_entity_role(entity.role)
            if not entity.evidence:
                raise ValueError(f"missing entity/role evidence: {project.gold_project_id}.entities[{index}]")
            for evidence in entity.evidence:
                _validate_evidence_span(
                    evidence, snapshot, f"{project.gold_project_id}.entities[{index}]"
                )


def _assert_draft_mutable(article: RichGoldRecord) -> None:
    """Reject an attempted human draft mutation after the article has been explicitly confirmed."""
    if article.review_status == "reviewed":
        raise ReviewedRecordError("reviewed records are immutable")


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


def _validate_snapshot_identity(article: RichGoldRecord, snapshot: ArticleSnapshot) -> None:
    """Require one internally canonical snapshot whose hash and source identity match the seeded draft."""
    canonical_hash = article_sha256(snapshot.title, snapshot.content, snapshot.publish_date)
    if snapshot.article_sha256 != canonical_hash:
        raise ValueError("immutable article snapshot canonical hash is invalid")
    if article.article_sha256 != snapshot.article_sha256:
        raise ValueError("immutable article snapshot hash does not match the review draft")
    if article.source_link and snapshot.source_link and article.source_link != snapshot.source_link:
        raise ValueError("immutable article snapshot source link does not match the review draft")


def _validate_evidence_span(evidence: Any, snapshot: ArticleSnapshot, field_path: str) -> None:
    """Validate canonical hash, bounds, and exact quote text for one field, entity, or role evidence span."""
    if evidence.article_sha256 != snapshot.article_sha256:
        raise ValueError(f"evidence article hash mismatch: {field_path}")
    if evidence.start < 0 or evidence.end > len(snapshot.content) or evidence.end <= evidence.start:
        raise ValueError(f"evidence bounds are invalid: {field_path}")
    if snapshot.content[evidence.start : evidence.end] != evidence.quote:
        raise ValueError(f"evidence exact quote mismatch: {field_path}")


def _assert_not_future_schema(conn: sqlite3.Connection) -> None:
    """Fail closed whenever an opened review database declares a schema newer than this binary supports."""
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='studio_meta'"
    ).fetchone()
    if table is None:
        return
    row = conn.execute("SELECT value FROM studio_meta WHERE key='schema_version'").fetchone()
    if row is None:
        return
    try:
        version = int(row[0])
    except (TypeError, ValueError) as error:
        raise RuntimeError("review database has an invalid schema version") from error
    if version > _SCHEMA_VERSION:
        raise RuntimeError(f"future review schema version {version} is unsupported; latest is {_SCHEMA_VERSION}")


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Add immutable article snapshots without synthesizing evidence from mutable external sources."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS article_snapshots (
            sample_id TEXT PRIMARY KEY REFERENCES samples(sample_id),
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            publish_date TEXT NOT NULL,
            article_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """Add indexed article gold status and relocate legacy sampling provenance into the explicit rich contract."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(article_drafts)")}
    if "article_status" not in columns:
        conn.execute(
            "ALTER TABLE article_drafts ADD COLUMN article_status TEXT NOT NULL DEFAULT 'unreviewed'"
        )
    rows = conn.execute(
        "SELECT sample_id, payload_json, review_status FROM article_drafts ORDER BY sample_id"
    ).fetchall()
    for row in rows:
        payload = json.loads(row["payload_json"])
        audit_metadata = payload.get("audit_metadata")
        audit_metadata = audit_metadata if isinstance(audit_metadata, dict) else {}
        declared = audit_metadata.get("eval_status")
        if declared not in _PUBLISHABLE_ARTICLE_STATUSES:
            declared = None
        article_status = payload.get("article_status")
        if article_status not in _PUBLISHABLE_ARTICLE_STATUSES:
            article_status = declared
        if article_status is None and row["review_status"] == "reviewed":
            article_status = "valid" if payload.get("projects") else "irrelevant"
        payload["article_status"] = article_status or "unreviewed"
        legacy_sampling = audit_metadata.pop("sampling", None)
        if payload.get("sampling_provenance") is None and isinstance(legacy_sampling, dict):
            payload["sampling_provenance"] = {
                "sampling_seed": legacy_sampling.get("seed", 0),
                "inclusion_reasons": list(legacy_sampling.get("inclusion_reasons") or []),
                "original_category_strata": list(legacy_sampling.get("category_tags") or []),
                "risk_tags": list(payload.get("risk_tags") or []),
                "negative_proxy": bool(legacy_sampling.get("negative_proxy", False)),
            }
        payload["audit_metadata"] = audit_metadata
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        conn.execute(
            "UPDATE article_drafts SET payload_json=?, article_status=? WHERE sample_id=?",
            (encoded, payload["article_status"], row["sample_id"]),
        )


_V1_SCHEMA = (
    """CREATE TABLE samples (
        sample_id TEXT PRIMARY KEY, article_sha256 TEXT NOT NULL, source_link TEXT, created_at TEXT NOT NULL
    )""",
    """CREATE TABLE article_drafts (
        sample_id TEXT PRIMARY KEY REFERENCES samples(sample_id), payload_json TEXT NOT NULL,
        review_status TEXT NOT NULL, difficulty_score REAL NOT NULL, updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE candidate_bundles (
        sample_id TEXT PRIMARY KEY REFERENCES samples(sample_id), payload_json TEXT NOT NULL, updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE sample_scoring (
        sample_id TEXT PRIMARY KEY REFERENCES samples(sample_id), difficulty_score REAL NOT NULL,
        reason_codes_json TEXT NOT NULL, risk_tags_json TEXT NOT NULL, updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE field_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, sample_id TEXT NOT NULL REFERENCES samples(sample_id),
        field_path TEXT NOT NULL, field_json TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
    )""",
    """CREATE TABLE structure_revisions (
        sample_id TEXT NOT NULL REFERENCES samples(sample_id), revision INTEGER NOT NULL,
        projects_json TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY (sample_id, revision)
    )""",
    """CREATE TABLE audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, sample_id TEXT NOT NULL REFERENCES samples(sample_id),
        action TEXT NOT NULL, field_path TEXT, old_json TEXT, new_json TEXT,
        actor TEXT NOT NULL, created_at TEXT NOT NULL
    )""",
    """CREATE TABLE review_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, sample_id TEXT NOT NULL REFERENCES samples(sample_id),
        actor TEXT NOT NULL, started_at TEXT NOT NULL
    )""",
    "CREATE TABLE studio_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
)
_MIGRATIONS = {1: _migrate_v1_to_v2, 2: _migrate_v2_to_v3}
