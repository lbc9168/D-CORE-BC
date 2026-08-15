"""Read one fixed Pilot article and related database records through immutable SQLite connections."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

ARTICLE_ID = 9027
WORKTREE_ROOT = Path(__file__).resolve().parents[4]
STABLE_ROOT = WORKTREE_ROOT.parents[2]
RAW_DB = STABLE_ROOT / "s01a_news_crawler" / "data" / "raw_news.db"
EXTRACTION_DB = STABLE_ROOT / "s01b_project_extractor" / "data" / "projects.db"
MAIN_DB = WORKTREE_ROOT / "s02a_duplicate_merger" / "data" / "db" / "bri_re_database.db"


def _connect_readonly(path: Path) -> sqlite3.Connection:
    """Open one SQLite source in URI read-only and query-only mode."""
    if not path.is_file():
        raise FileNotFoundError(f"缺少只读数据源：{path}")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _article_hash(article: dict[str, Any]) -> str:
    """Compute the fixed article hash from the exact source body used by both Subagents."""
    return hashlib.sha256(str(article.get("content") or "").encode("utf-8")).hexdigest()


def _group_extraction_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse historical entity-expanded extraction rows into article-level projects."""
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row.get("project_name") or "", row.get("location") or "")
        project = grouped.setdefault(
            key,
            {
                "project_name": row.get("project_name"),
                "country": row.get("country"),
                "location": row.get("location"),
                "normalized_location": row.get("location_norm"),
                "category": row.get("category"),
                "generation_sources": [],
                "storage_status": "confirmed" if (
                    row.get("storage_power_mw") is not None or row.get("storage_energy_mwh") is not None
                ) else "not_mentioned",
                "storage_power_mw": (
                    row.get("storage_power_mw") if row.get("storage_power_mw") is not None
                    else row.get("capacity_mw") if row.get("category") == "储能" else None
                ),
                "storage_energy_mwh": row.get("storage_energy_mwh"),
                "generation_capacity_mw": (
                    None if row.get("category") in {"储能", "新能源制造业", "输变电建设"}
                    else row.get("capacity_mw")
                ),
                "project_stage": row.get("project_stage"),
                "project_developer": row.get("project_developer"),
                "planned_start_date": row.get("planned_start_date"),
                "planned_complete_date": row.get("planned_complete_date"),
                "investment": row.get("total_investment_usd"),
                "financing_mode": row.get("financing_model"),
                "entities": [],
                "normalized_entities": [],
                "source_kind": "historical_extraction",
            },
        )
        entity = {
            "name": row.get("subsidiary_name") or row.get("implementing_entity"),
            "role": row.get("involvement_type"),
        }
        if entity not in project["entities"]:
            project["entities"].append(entity)
        normalized_entity = {
            "mentioned_name": row.get("subsidiary_name") or row.get("implementing_entity"),
            "canonical_entity": row.get("subsidiary_name") or row.get("implementing_entity"),
            "parent_company": row.get("implementing_entity"),
            "role": row.get("involvement_type"),
        }
        if normalized_entity not in project["normalized_entities"]:
            project["normalized_entities"].append(normalized_entity)
    return list(grouped.values())


def load_article(article_id: int = ARTICLE_ID) -> dict[str, Any]:
    """Load one Pilot article plus current canonical, event, entity, and extraction context."""
    with _connect_readonly(RAW_DB) as connection:
        row = connection.execute("SELECT * FROM raw_news WHERE id=?", (article_id,)).fetchone()
        if row is None:
            raise LookupError(f"raw_news.id={article_id} 不存在")
        article = dict(row)
    article["article_sha256"] = _article_hash(article)
    source_link = article.get("url") or ""

    with _connect_readonly(MAIN_DB) as connection:
        events = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM project_events WHERE source_links LIKE ? ORDER BY canonical_id",
                (f"%{source_link}%",),
            )
        ]
        canonical_ids = [event["canonical_id"] for event in events]
        event_ids = [event["id"] for event in events]
        canonical: list[dict[str, Any]] = []
        entities: list[dict[str, Any]] = []
        if canonical_ids:
            marks = ",".join("?" for _ in canonical_ids)
            canonical = [
                dict(row)
                for row in connection.execute(
                    f"SELECT * FROM canonical_projects WHERE id IN ({marks}) ORDER BY id", canonical_ids
                )
            ]
        if event_ids:
            marks = ",".join("?" for _ in event_ids)
            entities = [
                dict(row)
                for row in connection.execute(
                    f"SELECT * FROM chinese_entities WHERE event_id IN ({marks}) ORDER BY event_id,id", event_ids
                )
            ]

    with _connect_readonly(EXTRACTION_DB) as connection:
        extraction_rows = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM extracted_projects WHERE source_link LIKE ? ORDER BY id",
                (source_link,),
            )
        ]

    canonical_by_id = {item["id"]: item for item in canonical}
    entities_by_event: dict[str, list[dict[str, Any]]] = {}
    for entity in entities:
        entities_by_event.setdefault(entity["event_id"], []).append(entity)
    database_projects = []
    for event in events:
        current = dict(canonical_by_id[event["canonical_id"]])
        current.update(event)
        current["entities"] = entities_by_event.get(event["id"], [])
        current["cross_source_aggregate"] = len(json.loads(event.get("source_links") or "[]")) > 1
        database_projects.append(current)

    return {
        "article": article,
        "database_projects": database_projects,
        "historical_extraction_projects": _group_extraction_rows(extraction_rows),
        "source_paths": {"raw": str(RAW_DB), "extraction": str(EXTRACTION_DB), "main": str(MAIN_DB)},
    }


def load_single_article() -> dict[str, Any]:
    """Retain the Stage 2 compatibility entry point for the fixed baseline article."""
    return load_article(ARTICLE_ID)
