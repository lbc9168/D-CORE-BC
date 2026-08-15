"""Persist lenient article drafts and strict final confirmation in a Studio-owned SQLite database."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = {
    "project_name", "category", "subcategory", "generation_sources", "storage_status",
    "storage_power_mw", "storage_energy_mwh", "generation_capacity_mw", "project_stage",
    "country", "location", "investment", "financing_mode", "project_developer",
    "planned_start_date", "planned_complete_date", "entities", "normalized_location",
    "normalized_entities",
}
DERIVED_FIELDS = {"normalized_location", "normalized_entities"}


def _evidence_supported(evidence: str, article_sources: str | list[str]) -> bool:
    """Validate exact title/body quotes or several delimiter-separated exact quotes."""
    sources = [article_sources] if isinstance(article_sources, str) else article_sources
    if any(evidence in source for source in sources):
        return True
    fragments = [part.strip(" 。；;，,：:\n") for part in re.split(r"[；;。\n]", evidence)]
    nonempty = [fragment for fragment in fragments if fragment]
    return bool(nonempty) and all(any(fragment in source for source in sources) for fragment in nonempty)


class DraftStore:
    """Store one article snapshot, candidate runs, and autosaved review drafts."""

    def __init__(self, path: Path):
        """Initialize the local review database schema without touching source databases."""
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS article_snapshots (
                    article_id INTEGER PRIMARY KEY, article_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS candidates (
                    candidate_id TEXT PRIMARY KEY, article_id INTEGER NOT NULL, source TEXT NOT NULL,
                    model TEXT NOT NULL, parent_candidate_id TEXT, payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS drafts (
                    article_id INTEGER PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS review_actions (
                    action_id INTEGER PRIMARY KEY AUTOINCREMENT, article_id INTEGER NOT NULL,
                    action TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        """Open a short-lived connection to the local writable review database."""
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def seed_article(self, article_id: int, article_sha256: str, payload: dict[str, Any]) -> None:
        """Insert the immutable article context once and reject later hash drift."""
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            current = connection.execute(
                "SELECT article_sha256 FROM article_snapshots WHERE article_id=?", (article_id,)
            ).fetchone()
            if current and current["article_sha256"] != article_sha256:
                raise ValueError("文章快照 hash 已变化，拒绝覆盖现有复核数据")
            connection.execute(
                "INSERT OR IGNORE INTO article_snapshots VALUES (?,?,?)",
                (article_id, article_sha256, serialized),
            )

    def import_candidate(self, candidate_id: str, article_id: int, source: str, model: str,
                         payload: dict[str, Any], parent_candidate_id: str | None = None) -> None:
        """Upsert one traceable Subagent candidate artifact in the local review database."""
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO candidates VALUES (?,?,?,?,?,?)
                   ON CONFLICT(candidate_id) DO UPDATE SET payload_json=excluded.payload_json""",
                (candidate_id, article_id, source, model, parent_candidate_id,
                 json.dumps(payload, ensure_ascii=False, sort_keys=True)),
            )

    def list_candidates(self, article_id: int) -> list[dict[str, Any]]:
        """Return all candidate payloads with their provenance metadata."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM candidates WHERE article_id=? ORDER BY source", (article_id,)
            ).fetchall()
        return [
            {**dict(row), "payload": json.loads(row["payload_json"])} for row in rows
        ]

    def get_draft(self, article_id: int) -> dict[str, Any] | None:
        """Load the latest autosaved draft and its revision."""
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM drafts WHERE article_id=?", (article_id,)).fetchone()
        if row is None:
            return None
        return {"revision": row["revision"], "confirmed": bool(row["confirmed"]),
                "payload": json.loads(row["payload_json"]), "updated_at": row["updated_at"]}

    def save_draft(self, article_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """Save any JSON draft without publication-level field or evidence validation."""
        now = datetime.now(timezone.utc).isoformat()
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            current = connection.execute(
                "SELECT revision,confirmed FROM drafts WHERE article_id=?", (article_id,)
            ).fetchone()
            if current and current["confirmed"]:
                raise ValueError("文章已经确认；如需修改，请先重新打开复核")
            revision = (current["revision"] if current else 0) + 1
            connection.execute(
                """INSERT INTO drafts VALUES (?,?,?,?,?)
                   ON CONFLICT(article_id) DO UPDATE SET revision=excluded.revision,
                   payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
                (article_id, revision, serialized, 0, now),
            )
        return {"revision": revision, "updated_at": now}

    def confirm(self, article_id: int) -> dict[str, Any]:
        """Run strict whole-article validation and mark the existing draft confirmed atomically."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM drafts WHERE article_id=?", (article_id,)).fetchone()
            if row is None:
                raise ValueError("尚无可确认的草稿")
            if row["confirmed"]:
                raise ValueError("文章已经确认；如需修改，请先重新打开复核")
            payload = json.loads(row["payload_json"])
            snapshot = connection.execute(
                "SELECT payload_json FROM article_snapshots WHERE article_id=?", (article_id,)
            ).fetchone()
            snapshot_article = json.loads(snapshot["payload_json"])["article"] if snapshot else {}
            article_sources = [
                str(snapshot_article.get("title") or ""),
                str(snapshot_article.get("content") or ""),
            ]
            errors = validate_for_confirmation(payload, article_sources)
            if errors:
                raise ValueError("；".join(errors))
            connection.execute("UPDATE drafts SET confirmed=1 WHERE article_id=?", (article_id,))
            connection.execute(
                "INSERT INTO review_actions(article_id,action,revision,created_at) VALUES (?,?,?,?)",
                (article_id, "confirmed", row["revision"], datetime.now(timezone.utc).isoformat()),
            )
        return {"confirmed": True, "revision": row["revision"]}

    def reopen(self, article_id: int) -> dict[str, Any]:
        """Explicitly unlock a confirmed working draft and retain the workflow transition history."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM drafts WHERE article_id=?", (article_id,)).fetchone()
            if row is None:
                raise ValueError("尚无可重新打开的草稿")
            if not row["confirmed"]:
                raise ValueError("文章当前未确认，无需重新打开")
            revision = row["revision"] + 1
            connection.execute(
                "UPDATE drafts SET confirmed=0,revision=?,updated_at=? WHERE article_id=?",
                (revision, now, article_id),
            )
            connection.execute(
                "INSERT INTO review_actions(article_id,action,revision,created_at) VALUES (?,?,?,?)",
                (article_id, "reopened", revision, now),
            )
        return {"confirmed": False, "revision": revision, "updated_at": now}

    def list_actions(self, article_id: int) -> list[dict[str, Any]]:
        """Return confirmation and reopen transitions in stable chronological order."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM review_actions WHERE article_id=? ORDER BY action_id", (article_id,)
            ).fetchall()
        return [dict(row) for row in rows]


def validate_for_confirmation(payload: dict[str, Any], article_sources: str | list[str] = "") -> list[str]:
    """Validate final structure, actions, and verbatim title-or-body evidence while drafts stay lenient."""
    errors: list[str] = []
    status = payload.get("article_status")
    projects = payload.get("projects") or []
    if status == "valid" and not projects:
        errors.append("有效文章至少需要一个项目")
    if status != "valid" and projects:
        errors.append("负样本或非项目文章不能保留项目")
    if status and status != "valid" and not str(payload.get("non_project_reason") or "").strip():
        errors.append("负样本或非项目文章需要填写原因")
    if not status:
        errors.append("请选择文章状态")
    for project_index, project in enumerate(projects, start=1):
        if not project.get("project_name"):
            errors.append(f"项目 {project_index} 缺少名称")
        fields = project.get("fields") or {}
        missing = sorted(REQUIRED_FIELDS - set(fields))
        if missing:
            errors.append(f"项目 {project_index} 缺少字段：{', '.join(missing)}")
        for field_name, field in fields.items():
            action = field.get("review_action")
            state = field.get("value_state")
            if action not in {"confirmed", "modified"}:
                errors.append(f"项目 {project_index} · {field_name} 尚未确认")
            if state == "needs_adjudication":
                errors.append(f"项目 {project_index} · {field_name} 仍待裁定")
            if state == "present" and field_name not in DERIVED_FIELDS:
                evidence = field.get("evidence")
                if not evidence:
                    errors.append(f"项目 {project_index} · {field_name} 缺少原文证据")
                elif article_sources and not _evidence_supported(str(evidence), article_sources):
                    errors.append(f"项目 {project_index} · {field_name} 的证据无法精确定位到原文")
    return errors
