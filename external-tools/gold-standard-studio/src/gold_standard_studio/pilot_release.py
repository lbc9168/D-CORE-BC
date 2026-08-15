"""Publish confirmed Pilot reviews as an immutable, layered Golden Standard release."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
import uuid
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterable

from .draft_store import REQUIRED_FIELDS, validate_for_confirmation
from .pilot_manifest import pilot_article_ids
from .single_article import load_article

LOGGER = logging.getLogger(__name__)
MODULE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REVIEW_DB = MODULE_ROOT / "data" / "review.db"
DEFAULT_RELEASE_ROOT = MODULE_ROOT / "data" / "releases"
RELEASE_SCHEMA = "pilot-gold-v1"
VERSION_PATTERN = re.compile(r"pilot-v\d+\.\d+\.\d+\Z")
FACT_FIELDS = tuple(sorted(REQUIRED_FIELDS - {"normalized_entities", "normalized_location"}))
DERIVED_FIELDS = ("normalized_entities", "normalized_location")
LEGACY_GENERATION_SOURCES = {"光伏", "风电", "水电", "地热及余热", "生物质", "核电", "火电"}


def _json_bytes(value: Any) -> bytes:
    """Serialize JSON deterministically for immutable artifacts and hashes."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    """Return the lowercase SHA-256 digest for a byte sequence."""
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    """Hash a file in chunks without modifying it."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _connect_review_readonly(path: Path) -> sqlite3.Connection:
    """Open the Studio review database in SQLite read-only and query-only mode."""
    if not path.is_file():
        raise FileNotFoundError(f"复核库不存在：{path}")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def check_release_readiness(
    review_db: Path = DEFAULT_REVIEW_DB,
    article_ids: Iterable[int] | None = None,
    article_loader: Callable[[int], dict[str, Any]] = load_article,
) -> dict[str, Any]:
    """Revalidate confirmed drafts and source hashes, returning actionable release blockers."""
    ids = list(article_ids if article_ids is not None else pilot_article_ids())
    articles: list[dict[str, Any]] = []
    with _connect_review_readonly(review_db) as connection:
        for article_id in ids:
            blockers: list[str] = []
            draft = connection.execute(
                "SELECT revision,confirmed,payload_json FROM drafts WHERE article_id=?", (article_id,)
            ).fetchone()
            snapshot = connection.execute(
                "SELECT article_sha256,payload_json FROM article_snapshots WHERE article_id=?", (article_id,)
            ).fetchone()
            if draft is None:
                blockers.append("缺少复核草稿")
            elif not draft["confirmed"]:
                blockers.append("文章尚未确认")
            if snapshot is None:
                blockers.append("缺少复核时文章快照")

            current: dict[str, Any] | None = None
            try:
                current = article_loader(article_id)
            except (FileNotFoundError, LookupError, sqlite3.Error, ValueError) as error:
                blockers.append(f"无法读取当前只读来源：{error}")

            if draft is not None and snapshot is not None:
                try:
                    payload = json.loads(draft["payload_json"])
                    snapshot_payload = json.loads(snapshot["payload_json"])
                except json.JSONDecodeError as error:
                    blockers.append(f"本地复核 JSON 损坏：{error}")
                else:
                    snapshot_article = snapshot_payload.get("article", {})
                    article_sources = [
                        str(snapshot_article.get("title") or ""),
                        str(snapshot_article.get("content") or ""),
                    ]
                    blockers.extend(validate_for_confirmation(payload, article_sources))
                    current_hash = (current or {}).get("article", {}).get("article_sha256")
                    if current_hash and current_hash != snapshot["article_sha256"]:
                        blockers.append("当前原文 hash 与复核快照不一致")
            articles.append(
                {
                    "article_id": article_id,
                    "ready": not blockers,
                    "confirmed": bool(draft and draft["confirmed"]),
                    "revision": draft["revision"] if draft else None,
                    "blockers": blockers,
                }
            )
    return {
        "schema_version": RELEASE_SCHEMA,
        "ready": all(item["ready"] for item in articles),
        "article_count": len(ids),
        "ready_count": sum(item["ready"] for item in articles),
        "articles": articles,
    }


def _load_confirmed_records(
    review_db: Path,
    article_ids: list[int],
    article_loader: Callable[[int], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Load confirmed Gold payloads, article metadata, candidates, and stored Pipeline context."""
    records: list[dict[str, Any]] = []
    with _connect_review_readonly(review_db) as connection:
        for article_id in article_ids:
            draft = connection.execute(
                "SELECT revision,payload_json,updated_at FROM drafts WHERE article_id=?", (article_id,)
            ).fetchone()
            snapshot = connection.execute(
                "SELECT article_sha256,payload_json FROM article_snapshots WHERE article_id=?", (article_id,)
            ).fetchone()
            candidates = connection.execute(
                "SELECT candidate_id,source,model,parent_candidate_id FROM candidates "
                "WHERE article_id=? ORDER BY source,candidate_id",
                (article_id,),
            ).fetchall()
            context = article_loader(article_id)
            snapshot_payload = json.loads(snapshot["payload_json"])
            article = snapshot_payload["article"]
            records.append(
                {
                    "article_id": article_id,
                    "article": {
                        "title": article.get("title"),
                        "source_link": article.get("url") or article.get("source_link") or "",
                        "publish_date": article.get("publish_date") or article.get("published_at"),
                        "content_sha256": snapshot["article_sha256"],
                    },
                    "review": {
                        "revision": draft["revision"],
                        "updated_at": draft["updated_at"],
                        "payload": json.loads(draft["payload_json"]),
                    },
                    "candidates": [dict(row) for row in candidates],
                    "context": context,
                }
            )
    return records


def _field(project: dict[str, Any], name: str) -> dict[str, Any]:
    """Return one reviewed field envelope or a stable not-mentioned fallback."""
    return (project.get("fields") or {}).get(name) or {
        "value": None, "value_state": "not_mentioned", "review_action": "confirmed", "evidence": ""
    }


def _value(project: dict[str, Any], name: str) -> Any:
    """Return only the accepted value from a reviewed field envelope."""
    return _field(project, name).get("value")


def _rich_record(record: dict[str, Any]) -> dict[str, Any]:
    """Build the evidence-preserving canonical Gold record for one article."""
    payload = record["review"]["payload"]
    return {
        "schema_version": RELEASE_SCHEMA,
        "article_id": record["article_id"],
        "article": record["article"],
        "article_status": payload.get("article_status"),
        "non_project_reason": payload.get("non_project_reason"),
        "projects": payload.get("projects") or [],
        "review": {"revision": record["review"]["revision"], "updated_at": record["review"]["updated_at"]},
        "candidate_provenance": record["candidates"],
    }


def _s01b_record(record: dict[str, Any]) -> dict[str, Any]:
    """Project one rich record to article-backed S01B facts while retaining field envelopes."""
    payload = record["review"]["payload"]
    return {
        "article_id": record["article_id"],
        "source_link": record["article"]["source_link"],
        "article_status": payload.get("article_status"),
        "non_project_reason": payload.get("non_project_reason"),
        "projects": [
            {
                "gold_project_id": project.get("gold_project_id"),
                "relation_note": project.get("relation_note"),
                "fields": {name: _field(project, name) for name in FACT_FIELDS},
            }
            for project in payload.get("projects") or []
        ],
    }


def _investment_usd(project: dict[str, Any]) -> str | None:
    """Select the current legacy evaluator's single investment value from the richer Gold field."""
    value = _value(project, "investment")
    if isinstance(value, dict):
        value = value.get("total_project_investment_million_usd") or value.get("total_project_investment_raw")
    return None if value in (None, "") else str(value)


def _number(value: Any) -> float | None:
    """Convert a scalar numeric Gold value to float without guessing from free text."""
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _legacy_s01b_record(record: dict[str, Any]) -> dict[str, Any]:
    """Build one row accepted by the repository's existing strict eval Gold loader."""
    payload = record["review"]["payload"]
    status = {"no_chinese_participation": "no_chinese"}.get(
        payload.get("article_status"), payload.get("article_status")
    )
    projects = []
    for project in payload.get("projects") or []:
        sources = _value(project, "generation_sources") or []
        entities = _value(project, "entities") or []
        storage_status = _value(project, "storage_status")
        if storage_status not in {"confirmed", "planned", "optional", "unclear", "not_mentioned"}:
            storage_status = "not_mentioned"
        projects.append(
            {
                "project_name": _value(project, "project_name") or project.get("project_name"),
                "category": _value(project, "category"),
                "project_stage": _value(project, "project_stage"),
                "financing_model": _value(project, "financing_mode"),
                "capacity_mw": None if _value(project, "generation_capacity_mw") in (None, "") else str(_value(project, "generation_capacity_mw")),
                "total_investment_usd": _investment_usd(project),
                "location": _value(project, "location"),
                "generation_sources": [item for item in sources if item in LEGACY_GENERATION_SOURCES],
                "storage_status": storage_status,
                "storage_power_mw": _number(_value(project, "storage_power_mw")),
                "storage_energy_mwh": _number(_value(project, "storage_energy_mwh")),
                "entities": [
                    {"name": item.get("name"), "involvement_type": item.get("role")}
                    for item in entities if isinstance(item, dict) and item.get("name")
                ],
            }
        )
    return {
        "source_link": record["article"]["source_link"],
        "article_id": str(record["article_id"]),
        "status": status,
        "projects": projects,
    }


def _s02a_record(record: dict[str, Any]) -> dict[str, Any]:
    """Project one article to its human-approved project split and merge structure."""
    payload = record["review"]["payload"]
    return {
        "article_id": record["article_id"],
        "source_link": record["article"]["source_link"],
        "article_status": payload.get("article_status"),
        "projects": [
            {
                "gold_project_id": project.get("gold_project_id"),
                "project_name": _value(project, "project_name") or project.get("project_name"),
                "relation_note": project.get("relation_note"),
            }
            for project in payload.get("projects") or []
        ],
    }


def _s02b_record(record: dict[str, Any]) -> dict[str, Any]:
    """Project one article to reviewed company-parent and location normalization truth."""
    payload = record["review"]["payload"]
    return {
        "article_id": record["article_id"],
        "source_link": record["article"]["source_link"],
        "projects": [
            {
                "gold_project_id": project.get("gold_project_id"),
                "project_name": _value(project, "project_name") or project.get("project_name"),
                "normalized_location": _field(project, "normalized_location"),
                "normalized_entities": _field(project, "normalized_entities"),
            }
            for project in payload.get("projects") or []
        ],
    }


def _end_to_end_record(record: dict[str, Any]) -> dict[str, Any]:
    """Flatten reviewed values and states for final database regression comparisons."""
    payload = record["review"]["payload"]
    return {
        "article_id": record["article_id"],
        "source_link": record["article"]["source_link"],
        "article_status": payload.get("article_status"),
        "non_project_reason": payload.get("non_project_reason"),
        "projects": [
            {
                "gold_project_id": project.get("gold_project_id"),
                "relation_note": project.get("relation_note"),
                "values": {name: _value(project, name) for name in sorted(REQUIRED_FIELDS)},
                "value_states": {name: _field(project, name).get("value_state") for name in sorted(REQUIRED_FIELDS)},
            }
            for project in payload.get("projects") or []
        ],
    }


def _project_name(project: dict[str, Any]) -> str:
    """Extract a comparable project name from Gold, Pipeline, or database shapes."""
    if "fields" in project:
        return str(_value(project, "project_name") or project.get("project_name") or "")
    return str(project.get("project_name") or "")


def _align(gold: list[dict[str, Any]], predicted: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Greedily align projects by normalized name similarity for transparent Pilot diagnostics."""
    remaining = list(predicted)
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for gold_project in gold:
        if not remaining:
            break
        name = _project_name(gold_project).casefold().strip()
        scored = [(SequenceMatcher(None, name, _project_name(item).casefold().strip()).ratio(), item) for item in remaining]
        score, best = max(scored, key=lambda item: item[0])
        if score >= 0.45:
            pairs.append((gold_project, best))
            remaining.remove(best)
    return pairs


def _prf(gold_count: int, predicted_count: int, matched_count: int) -> dict[str, float | int]:
    """Compute project-set precision, recall, and F1 with empty-set-safe semantics."""
    precision = matched_count / predicted_count if predicted_count else float(gold_count == 0)
    recall = matched_count / gold_count if gold_count else float(predicted_count == 0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"gold": gold_count, "predicted": predicted_count, "matched": matched_count,
            "precision": precision, "recall": recall, "f1": f1}


def _canonical(value: Any) -> str:
    """Canonicalize structured values for exact Pilot baseline comparisons."""
    if isinstance(value, list):
        return json.dumps(sorted((_canonical(item) for item in value)), ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps({key: _canonical(value[key]) for key in sorted(value)}, ensure_ascii=False, sort_keys=True)
    if value is None:
        return ""
    return str(value).strip().casefold()


def _database_project(project: dict[str, Any]) -> dict[str, Any]:
    """Map a current database project and its entity rows to comparable final field values."""
    entities = project.get("entities") or []
    return {
        "project_name": project.get("project_name"), "category": project.get("category"),
        "subcategory": project.get("subcategory"), "generation_sources": [],
        "storage_status": "confirmed" if project.get("storage_energy_mwh") else "not_mentioned",
        "storage_power_mw": project.get("storage_power_mw"), "storage_energy_mwh": project.get("storage_energy_mwh"),
        "generation_capacity_mw": None, "project_stage": project.get("project_stage"),
        "country": project.get("country"), "location": project.get("location"),
        "normalized_location": project.get("location_norm"), "investment": project.get("total_investment_usd_raw"),
        "financing_mode": project.get("financing_model"), "project_developer": project.get("project_developer"),
        "planned_start_date": project.get("planned_start_date"), "planned_complete_date": project.get("planned_complete_date"),
        "entities": [{"name": item.get("subsidiary_name") or item.get("implementing_entity"), "role": item.get("involvement_type")} for item in entities],
        "normalized_entities": [
            {"mentioned_name": item.get("subsidiary_name") or item.get("implementing_entity"),
             "canonical_entity": item.get("subsidiary_name") or item.get("implementing_entity"),
             "parent_company": item.get("implementing_entity"), "role": item.get("involvement_type")}
            for item in entities
        ],
    }


def _field_accuracy(pairs: list[tuple[dict[str, Any], dict[str, Any]]], fields: Iterable[str]) -> dict[str, Any]:
    """Calculate macro exact accuracy for selected fields across aligned projects."""
    by_field: dict[str, dict[str, int | float]] = {}
    total = correct = 0
    for name in fields:
        field_total = field_correct = 0
        for gold, predicted in pairs:
            gold_value = _value(gold, name)
            predicted_value = predicted.get(name)
            field_total += 1
            field_correct += int(_canonical(gold_value) == _canonical(predicted_value))
        by_field[name] = {"correct": field_correct, "total": field_total,
                          "accuracy": field_correct / field_total if field_total else None}
        total += field_total
        correct += field_correct
    return {"correct": correct, "total": total, "micro_accuracy": correct / total if total else None,
            "by_field": by_field}


def build_stored_pipeline_baseline(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare stored S01B and current database snapshots with layered Gold projections."""
    totals = {"s01b": [0, 0, 0], "s02a": [0, 0, 0], "end_to_end": [0, 0, 0]}
    fact_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    derived_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    final_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    status_correct = 0
    articles: list[dict[str, Any]] = []
    source_paths: dict[str, str] = {}
    for record in records:
        payload = record["review"]["payload"]
        gold = payload.get("projects") or []
        context = record["context"]
        historical = context.get("historical_extraction_projects") or []
        database = [_database_project(item) for item in context.get("database_projects") or []]
        source_paths.update(context.get("source_paths") or {})
        s01_pairs = _align(gold, historical)
        db_pairs = _align(gold, database)
        status_match = (payload.get("article_status") == "valid") == bool(historical)
        status_correct += int(status_match)
        fact_pairs.extend(s01_pairs)
        derived_pairs.extend(db_pairs)
        final_pairs.extend(db_pairs)
        for key, predicted, pairs in (("s01b", historical, s01_pairs), ("s02a", database, db_pairs), ("end_to_end", database, db_pairs)):
            totals[key][0] += len(gold); totals[key][1] += len(predicted); totals[key][2] += len(pairs)
        articles.append({
            "article_id": record["article_id"], "gold_status": payload.get("article_status"),
            "stored_s01b_has_projects": bool(historical), "validity_match": status_match,
            "s01b_projects": _prf(len(gold), len(historical), len(s01_pairs)),
            "database_projects": _prf(len(gold), len(database), len(db_pairs)),
        })
    source_hashes = {name: {"path": path, "sha256": sha256_file(Path(path))} for name, path in source_paths.items() if Path(path).is_file()}
    return {
        "baseline_kind": "stored_pipeline_snapshot",
        "limitations": [
            "未重新调用模型；S01B 基线来自 projects.db 中已存结果。",
            "已存项目只能评估 valid 与 non-valid，不能恢复 irrelevant 与 no_chinese_participation 的原始门控分类。",
            "S02A 首版仅评估文章内项目集合，不评估跨文章 canonical 聚类。",
        ],
        "source_databases": source_hashes,
        "article_validity": {"correct": status_correct, "total": len(records), "accuracy": status_correct / len(records) if records else None},
        "s01b": {"projects": _prf(*totals["s01b"]), "fields": _field_accuracy(fact_pairs, FACT_FIELDS)},
        "s02a": {"projects": _prf(*totals["s02a"])},
        "s02b": {"fields": _field_accuracy(derived_pairs, DERIVED_FIELDS)},
        "end_to_end": {"projects": _prf(*totals["end_to_end"]), "fields": _field_accuracy(final_pairs, sorted(REQUIRED_FIELDS))},
        "articles": articles,
    }


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """Write deterministic UTF-8 JSON Lines and reject rows that cannot round-trip."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = b"".join(_json_bytes(row) for row in rows)
    path.write_bytes(data)
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path} 第 {line_number} 行 JSON 无效：{error}") from error


def publish_release(
    version: str,
    review_db: Path = DEFAULT_REVIEW_DB,
    release_root: Path = DEFAULT_RELEASE_ROOT,
    article_ids: Iterable[int] | None = None,
    article_loader: Callable[[int], dict[str, Any]] = load_article,
) -> Path:
    """Atomically publish one never-overwritten Pilot release after strict readiness validation."""
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError("版本号必须形如 pilot-v0.1.0")
    ids = list(article_ids if article_ids is not None else pilot_article_ids())
    readiness = check_release_readiness(review_db, ids, article_loader)
    if not readiness["ready"]:
        details = [f"{item['article_id']}: {'；'.join(item['blockers'])}" for item in readiness["articles"] if item["blockers"]]
        raise ValueError("Pilot 尚未满足发布条件：" + " | ".join(details))
    final_dir = release_root / version
    if final_dir.exists():
        raise FileExistsError(f"版本已经存在，禁止覆盖：{final_dir}")
    release_root.mkdir(parents=True, exist_ok=True)
    temporary = release_root / f".{version}.tmp-{uuid.uuid4().hex}"
    records = _load_confirmed_records(review_db, ids, article_loader)
    try:
        temporary.mkdir()
        _write_jsonl(temporary / "rich-gold.jsonl", (_rich_record(item) for item in records))
        _write_jsonl(temporary / "projections" / "s01b.jsonl", (_s01b_record(item) for item in records))
        _write_jsonl(temporary / "projections" / "s01b-eval.jsonl", (_legacy_s01b_record(item) for item in records))
        _write_jsonl(temporary / "projections" / "s02a.jsonl", (_s02a_record(item) for item in records))
        _write_jsonl(temporary / "projections" / "s02b.jsonl", (_s02b_record(item) for item in records))
        _write_jsonl(temporary / "projections" / "end-to-end.jsonl", (_end_to_end_record(item) for item in records))
        baseline_path = temporary / "baseline" / "stored-pipeline-baseline.json"
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_bytes(_json_bytes(build_stored_pipeline_baseline(records)))
        artifact_paths = sorted(path for path in temporary.rglob("*") if path.is_file())
        artifacts = {path.relative_to(temporary).as_posix(): sha256_file(path) for path in artifact_paths}
        manifest = {
            "schema_version": RELEASE_SCHEMA, "release_version": version,
            "immutable": True, "article_ids": ids, "article_count": len(ids),
            "status_counts": {}, "project_count": 0, "source_review_db_sha256": sha256_file(review_db),
            "artifacts": artifacts,
        }
        for record in records:
            payload = record["review"]["payload"]
            status = payload.get("article_status")
            manifest["status_counts"][status] = manifest["status_counts"].get(status, 0) + 1
            manifest["project_count"] += len(payload.get("projects") or [])
        (temporary / "manifest.json").write_bytes(_json_bytes(manifest))
        json.loads((temporary / "manifest.json").read_text(encoding="utf-8"))
        if final_dir.exists():
            raise FileExistsError(f"版本已经存在，禁止覆盖：{final_dir}")
        os.replace(temporary, final_dir)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return final_dir


def _main(argv: list[str] | None = None) -> int:
    """Run readiness checks or immutable publication from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "publish"))
    parser.add_argument("--version", default="pilot-v0.1.0")
    parser.add_argument("--review-db", type=Path, default=DEFAULT_REVIEW_DB)
    parser.add_argument("--release-root", type=Path, default=DEFAULT_RELEASE_ROOT)
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            print(json.dumps(check_release_readiness(args.review_db), ensure_ascii=False, indent=2))
        else:
            path = publish_release(args.version, args.review_db, args.release_root)
            print(json.dumps({"published": True, "path": str(path)}, ensure_ascii=False, indent=2))
    except (FileNotFoundError, FileExistsError, ValueError, sqlite3.Error) as error:
        LOGGER.error("%s", error)
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
