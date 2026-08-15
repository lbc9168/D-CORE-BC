"""Build and safely apply an evidence-locked storage-taxonomy review campaign."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

CAMPAIGN_VERSION = "storage-taxonomy-v2-20260813"
DECISION_SCHEMA_VERSION = "storage-taxonomy-decision-v1"
STORAGE_TEXT = re.compile(r"储能|电池|储电|BESS|energy storage", re.IGNORECASE)
PHYSICAL_STORAGE_TEXT = re.compile(
    r"储能|储电|电池储能|battery\s+(?:energy\s+)?storage|\bBESS\b|energy\s+storage",
    re.IGNORECASE,
)
MANUFACTURING_CONTEXT = re.compile(
    r"储能.{0,12}(?:工厂|制造基地|产线|生产基地|制造厂|厂)|"
    r"(?:电池|BESS).{0,12}(?:工厂|制造基地|产线|生产基地|制造厂)|"
    r"(?:制造基地|产线|生产基地|制造厂).{0,20}(?:储能|电池|BESS)|"
    r"年产.{0,20}(?:GWh|吉瓦时)|(?:产能|production capacity).{0,20}(?:GWh|吉瓦时)",
    re.IGNORECASE,
)
NON_PROJECT_MEASUREMENT_CONTEXT = re.compile(
    r"年发电量|年上网电量|annual generation|年产|产能|production capacity|"
    r"累计.{0,12}(?:订单|出货|装机)|全球.{0,20}(?:订单|市场|装机)|"
    r"(?:计划|规划|目标|预计).{0,40}(?:部署|达到|需要|装机)|"
    r"(?:到|至)20\d{2}年.{0,40}(?:部署|达到|需要|装机)",
    re.IGNORECASE,
)
DATA_STORAGE_CONTEXT = re.compile(r"数据处理与存储|数据存储|data (?:processing and )?storage", re.IGNORECASE)
STORAGE_ENERGY_UNIT = re.compile(r"(?:MWh|GWh|兆瓦时|吉瓦时|千瓦时)", re.IGNORECASE)
GENERATION_SOURCES = ("光伏", "风电", "水电", "地热及余热", "生物质", "核电", "火电")
SOURCE_ORDER = {source: position for position, source in enumerate(GENERATION_SOURCES)}
SOURCE_SHORT = {
    "光伏": "光", "风电": "风", "水电": "水", "地热及余热": "地热",
    "生物质": "生物质", "核电": "核", "火电": "火",
}
TAG_ORDER = ("风电", "光伏", "水电", "地热及余热", "生物质", "核电", "火电")
SPECIAL_NON_GENERATION_CATEGORIES = {"新能源制造业", "输变电建设"}
ROLE_MODELS = {"terra": "gpt-5.6-terra", "sol": "gpt-5.6-sol"}
STORAGE_STATUSES = {"confirmed", "planned", "optional", "unclear", "not_mentioned"}
EVENT_FACT_COLUMNS = (
    "generation_sources_json", "storage_status", "storage_power_mw_raw",
    "storage_energy_mwh_raw", "energy_composition_evidence",
)


def _policy_result(
    sources: Iterable[str], storage_status: str, current_category: str | None = None,
) -> tuple[str | None, list[str]]:
    """按 energy_taxonomy_v2 从原子事实计算类别与唯一技术组合标签。"""
    normalized = sorted(set(sources), key=SOURCE_ORDER.__getitem__)
    effective = [source for source in normalized if source != "火电"] or normalized
    if not effective:
        if storage_status == "confirmed":
            return "储能", []
        if current_category in SPECIAL_NON_GENERATION_CATEGORIES:
            return current_category, []
        return None, []
    category = effective[0] if len(effective) == 1 else "综合能源"
    if len(effective) == 1 and storage_status != "confirmed":
        return category, []
    label = "".join(SOURCE_SHORT[source] for source in TAG_ORDER if source in effective)
    if storage_status == "confirmed":
        label += "储"
    return category, [label]


def _aggregate_status(statuses: Iterable[str]) -> str:
    """按 confirmed、planned、optional、unclear、not_mentioned 优先级聚合事件状态。"""
    order = {status: rank for rank, status in enumerate(
        ("not_mentioned", "unclear", "optional", "planned", "confirmed")
    )}
    values = list(statuses)
    return max(values, key=order.__getitem__) if values else "not_mentioned"


def _readonly(path: Path) -> sqlite3.Connection:
    """Open one SQLite database in immutable query-only mode."""
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _sha256(path: Path) -> str:
    """Return the SHA-256 digest of one file without changing it."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    """Serialize JSON deterministically for artifact hashing."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _write_json(path: Path, value: Any) -> str:
    """Write deterministic JSON and return its SHA-256 digest."""
    payload = _json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _source_links(raw: Any) -> list[str]:
    """Parse a project-event source link array without guessing malformed values."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(item) for item in value if isinstance(item, str) and item.strip()]


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    """Return the declared column names of one SQLite table."""
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def enumerate_candidates(main_db: Path) -> list[dict[str, Any]]:
    """Reproduce the approved 507 hybrid plus 302 storage-signal project scope."""
    with _readonly(main_db) as connection:
        canonical_rows = {
            row["id"]: dict(row)
            for row in connection.execute("SELECT * FROM canonical_projects WHERE excluded=0 ORDER BY id")
        }
        events: dict[str, list[dict[str, Any]]] = {}
        for row in connection.execute("SELECT * FROM project_events ORDER BY canonical_id,id"):
            if row["canonical_id"] in canonical_rows:
                events.setdefault(row["canonical_id"], []).append(dict(row))

    selected = []
    for project_id, canonical in canonical_rows.items():
        project_events = events.get(project_id, [])
        signal_fields = [str(canonical.get("project_name") or "")]
        for event in project_events:
            signal_fields.extend([
                str(event.get("capacity_details") or ""),
                str(event.get("project_summary") or ""),
            ])
        numeric_signal = any(
            event.get("storage_power_mw") is not None or event.get("storage_energy_mwh") is not None
            for event in project_events
        )
        text_signal = bool(STORAGE_TEXT.search("\n".join(signal_fields)))
        hybrid = canonical.get("category") == "综合能源"
        if not (hybrid or numeric_signal or text_signal):
            continue
        reasons = []
        if hybrid:
            reasons.append("active_hybrid_category")
        if numeric_signal:
            reasons.append("existing_storage_numeric")
        if text_signal:
            reasons.append("storage_text_signal")
        selected.append({"canonical": canonical, "events": project_events, "selection_reasons": reasons})
    return selected


def _articles_by_url(raw_db: Path, urls: set[str]) -> dict[str, dict[str, Any]]:
    """Load exact URL-matched source articles from raw_news without fuzzy matching."""
    result: dict[str, dict[str, Any]] = {}
    ordered = sorted(urls)
    with _readonly(raw_db) as connection:
        for offset in range(0, len(ordered), 500):
            batch = ordered[offset:offset + 500]
            marks = ",".join("?" for _ in batch)
            for row in connection.execute(
                f"SELECT id,url,title,content,author,publish_date FROM raw_news WHERE url IN ({marks})", batch
            ):
                article = dict(row)
                article["title_sha256"] = hashlib.sha256(str(article.get("title") or "").encode()).hexdigest()
                article["content_sha256"] = hashlib.sha256(str(article.get("content") or "").encode()).hexdigest()
                result[row["url"]] = article
    return result


def decision_schema() -> dict[str, Any]:
    """Return the strict Terra/Sol decision interchange contract."""
    evidence = {
        "type": "object", "additionalProperties": False,
        "required": ["url", "surface", "quote"],
        "properties": {
            "url": {"type": "string"},
            "surface": {"enum": ["title", "body"]},
            "quote": {"type": "string", "minLength": 1},
        },
    }
    event_decision = {
        "type": "object", "additionalProperties": False,
        "required": ["event_id", "generation_sources", "storage_status", "storage_power_mw",
                     "storage_energy_mwh", "storage_power_mw_raw", "storage_energy_mwh_raw",
                     "energy_composition_evidence"],
        "properties": {
            "event_id": {"type": "string"},
            "generation_sources": {"type": "array", "items": {"type": "string"}},
            "storage_status": {"enum": ["confirmed", "planned", "optional", "unclear", "not_mentioned"]},
            "storage_power_mw": {"type": ["number", "null"], "minimum": 0},
            "storage_energy_mwh": {"type": ["number", "null"], "minimum": 0},
            "storage_power_mw_raw": {"type": ["string", "null"]},
            "storage_energy_mwh_raw": {"type": ["string", "null"]},
            "energy_composition_evidence": {"type": "array", "items": evidence},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": DECISION_SCHEMA_VERSION,
        "type": "object", "additionalProperties": False,
        "required": ["schema_version", "campaign_version", "project_id", "package_sha256", "role",
                     "model", "resolution", "confidence", "review_reason", "canonical_decision",
                     "event_decisions"],
        "properties": {
            "schema_version": {"const": DECISION_SCHEMA_VERSION},
            "campaign_version": {"const": CAMPAIGN_VERSION},
            "project_id": {"type": "string"},
            "package_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "role": {"enum": ["terra", "sol"]},
            "model": {"type": "string"},
            "parent_decision_sha256": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
            "resolution": {"enum": ["resolved", "needs_manual_review"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "review_reason": {"type": ["string", "null"]},
            "canonical_decision": {
                "type": "object", "additionalProperties": False,
                "required": ["generation_sources", "storage_status", "storage_power_mw",
                             "storage_energy_mwh", "proposed_category", "proposed_subcategory",
                             "technology_tags", "evidence"],
                "properties": {
                    "generation_sources": {"type": "array", "items": {"type": "string"}},
                    "storage_status": {"enum": ["confirmed", "planned", "optional", "unclear", "not_mentioned"]},
                    "storage_power_mw": {"type": ["number", "null"], "minimum": 0},
                    "storage_energy_mwh": {"type": ["number", "null"], "minimum": 0},
                    "proposed_category": {"type": "string"},
                    "proposed_subcategory": {"type": ["string", "null"]},
                    "technology_tags": {"type": "array", "uniqueItems": True, "items": {"type": "string"}},
                    "evidence": {"type": "array", "items": evidence},
                },
            },
            "event_decisions": {"type": "array", "items": event_decision},
        },
    }


def build_campaign(main_db: Path, raw_db: Path, output_dir: Path) -> dict[str, Any]:
    """Create one immutable campaign directory with exact title/body evidence packages."""
    if output_dir.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖不可变 campaign：{output_dir}")
    candidates = enumerate_candidates(main_db)
    urls = {
        url for candidate in candidates for event in candidate["events"]
        for url in _source_links(event.get("source_links"))
    }
    articles = _articles_by_url(raw_db, urls)
    if len(candidates) != 809:
        raise RuntimeError(f"候选门禁失败：预期 809，实际 {len(candidates)}")

    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    try:
        packages: list[dict[str, Any]] = []
        missing: list[dict[str, str]] = []
        for candidate in candidates:
            canonical = candidate["canonical"]
            package_events = []
            for event in candidate["events"]:
                linked_articles = []
                for url in _source_links(event.get("source_links")):
                    article = articles.get(url)
                    if article is None:
                        missing.append({"project_id": canonical["id"], "event_id": event["id"], "url": url})
                    else:
                        linked_articles.append(article)
                package_events.append({"event": event, "articles": linked_articles})
            if not any(item["articles"] for item in package_events):
                raise RuntimeError(f"项目没有 exact-URL raw_news 证据：{canonical['id']}")
            payload = {
                "schema_version": "storage-taxonomy-evidence-package-v1",
                "campaign_version": CAMPAIGN_VERSION,
                "project_id": canonical["id"],
                "selection_reasons": candidate["selection_reasons"],
                "canonical": canonical,
                "events": package_events,
                "evidence_policy": {
                    "valid_surfaces": ["title", "body"],
                    "exact_quote_required": True,
                    "quote_must_not_cross_surfaces": True,
                },
            }
            path = temporary / "packages" / f"{canonical['id']}.json"
            package_hash = _write_json(path, payload)
            packages.append({
                "project_id": canonical["id"], "category": canonical.get("category"),
                "selection_reasons": candidate["selection_reasons"],
                "event_count": len(package_events),
                "article_count": sum(len(item["articles"]) for item in package_events),
                "path": f"packages/{canonical['id']}.json", "sha256": package_hash,
            })
        if missing:
            raise RuntimeError(f"存在 {len(missing)} 个无法 exact URL 匹配的 source_links")
        schema_hash = _write_json(temporary / "decision-schema.json", decision_schema())
        (temporary / "terra-decisions").mkdir()
        (temporary / "sol-decisions").mkdir()
        counts = Counter(item["category"] for item in packages)
        manifest = {
            "campaign_version": CAMPAIGN_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "immutable": True,
            "selection_contract": {
                "excluded": 0,
                "hybrid": "category == 综合能源",
                "other": "category != 综合能源 AND (any event storage MW/MWh non-null OR project_name + event capacity_details + event project_summary matches 储能|电池|储电|BESS|energy storage, case-insensitive)",
            },
            "candidate_count": len(packages),
            "category_counts": dict(sorted(counts.items())),
            "exact_url_coverage": len(packages),
            "main_db": {"path": str(main_db.resolve()), "sha256": _sha256(main_db)},
            "raw_db": {"path": str(raw_db.resolve()), "sha256": _sha256(raw_db)},
            "decision_schema": {"path": "decision-schema.json", "sha256": schema_hash},
            "packages": packages,
        }
        _write_json(temporary / "manifest.json", manifest)
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        temporary.replace(output_dir)
        return manifest
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _iter_decisions(directory: Path) -> Iterable[tuple[Path, dict[str, Any]]]:
    """Yield JSON decisions from one model-output directory in stable order."""
    if not directory.exists():
        return
    for path in sorted(directory.glob("*.json")):
        yield path, json.loads(path.read_text(encoding="utf-8"))


def _validate_evidence(decision: dict[str, Any], package: dict[str, Any]) -> list[str]:
    """Validate every quote against a single exact title or body evidence surface."""
    surfaces: dict[tuple[str, str], str] = {}
    for item in package["events"]:
        for article in item["articles"]:
            surfaces[(article["url"], "title")] = str(article.get("title") or "")
            surfaces[(article["url"], "body")] = str(article.get("content") or "")
    errors = []
    evidence = list(decision.get("canonical_decision", {}).get("evidence") or [])
    for event in decision.get("event_decisions") or []:
        evidence.extend(event.get("energy_composition_evidence") or [])
    for quote in evidence:
        key = (str(quote.get("url") or ""), str(quote.get("surface") or ""))
        if key not in surfaces or str(quote.get("quote") or "") not in surfaces[key]:
            errors.append(f"evidence_not_exact:{key[0]}:{key[1]}")
    return errors


def _raw_phrase_is_exact(raw: str, event_id: str, package: dict[str, Any]) -> bool:
    """确认事件容量原文短语完整出现于该事件任一标题或正文，禁止跨 surface 拼接。"""
    for item in package.get("events") or []:
        if item.get("event", {}).get("id") != event_id:
            continue
        return any(
            raw in str(article.get(surface) or "")
            for article in item.get("articles") or []
            for surface in ("title", "content")
        )
    return False


def _event_surface_texts(event_id: str, package: dict[str, Any]) -> list[str]:
    """返回指定事件的标题和正文独立 surface 文本，供语义归因门禁使用。"""
    for item in package.get("events") or []:
        if item.get("event", {}).get("id") == event_id:
            return [
                str(article.get(surface) or "")
                for article in item.get("articles") or []
                for surface in ("title", "content")
            ]
    return []


def _raw_has_forbidden_context(raw: str, event_id: str, package: dict[str, Any]) -> bool:
    """识别只出现在制造产能、年发电量或组合订单语境中的容量短语。"""
    occurrences = []
    for text in _event_surface_texts(event_id, package):
        start = 0
        while True:
            position = text.find(raw, start)
            if position < 0:
                break
            occurrences.append(text[max(0, position - 100):position + len(raw) + 100])
            start = position + max(len(raw), 1)
    return bool(occurrences) and all(
        MANUFACTURING_CONTEXT.search(window) or NON_PROJECT_MEASUREMENT_CONTEXT.search(window)
        for window in occurrences
    )


def _package_text(package: dict[str, Any]) -> str:
    """连接项目名及各 article surface，仅用于保守语义风险检测。"""
    parts = [str(package.get("canonical", {}).get("project_name") or "")]
    for item in package.get("events") or []:
        for article in item.get("articles") or []:
            parts.extend([str(article.get("title") or ""), str(article.get("content") or "")])
    return "\n".join(parts)


def validate_decision(decision: dict[str, Any], package: dict[str, Any], package_hash: str) -> list[str]:
    """Validate identity, evidence, aggregation, deterministic policy, and storage invariants."""
    errors = []
    required = set(decision_schema()["required"])
    errors.extend(f"missing:{key}" for key in sorted(required - set(decision)))
    if decision.get("schema_version") != DECISION_SCHEMA_VERSION:
        errors.append("schema_version")
    if decision.get("campaign_version") != CAMPAIGN_VERSION:
        errors.append("campaign_version")
    if decision.get("project_id") != package.get("project_id"):
        errors.append("project_id")
    if decision.get("package_sha256") != package_hash:
        errors.append("package_sha256")
    if decision.get("role") not in {"terra", "sol"}:
        errors.append("role")
    elif decision.get("model") != ROLE_MODELS[decision["role"]]:
        errors.append("model")
    confidence = decision.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        errors.append("confidence")
    canonical = decision.get("canonical_decision") or {}
    status = canonical.get("storage_status")
    if status not in STORAGE_STATUSES:
        errors.append("storage_status")
    sources = canonical.get("generation_sources") or []
    invalid_sources = sorted(set(sources).difference(GENERATION_SOURCES))
    if invalid_sources or len(sources) != len(set(sources)):
        errors.append("generation_sources")
    if status != "confirmed":
        if canonical.get("storage_power_mw") is not None or canonical.get("storage_energy_mwh") is not None:
            errors.append("nonconfirmed_storage_value")
        if any(str(tag).endswith("储") for tag in canonical.get("technology_tags") or []):
            errors.append("nonconfirmed_storage_tag")
    package_text = _package_text(package)
    canonical_evidence_text = "\n".join(
        str(item.get("quote") or "") for item in canonical.get("evidence") or []
    )
    if status == "confirmed":
        if MANUFACTURING_CONTEXT.search(package_text):
            project_name = str(package.get("canonical", {}).get("project_name") or "")
            if MANUFACTURING_CONTEXT.search(project_name):
                errors.append("manufacturing_project_cannot_be_confirmed_storage")
        if not PHYSICAL_STORAGE_TEXT.search(canonical_evidence_text):
            errors.append("confirmed_storage_without_physical_storage_evidence")
        if DATA_STORAGE_CONTEXT.search(canonical_evidence_text) and not PHYSICAL_STORAGE_TEXT.search(
            DATA_STORAGE_CONTEXT.sub("", canonical_evidence_text)
        ):
            errors.append("data_storage_not_energy_storage")
    package_event_ids = {item["event"]["id"] for item in package.get("events") or []}
    decision_event_ids = {item.get("event_id") for item in decision.get("event_decisions") or []}
    if package_event_ids != decision_event_ids:
        errors.append("event_coverage")
    for event in decision.get("event_decisions") or []:
        event_id = str(event.get("event_id") or "")
        event_sources = event.get("generation_sources") or []
        if set(event_sources).difference(GENERATION_SOURCES) or len(event_sources) != len(set(event_sources)):
            errors.append(f"generation_sources:{event_id}")
        if event.get("storage_status") not in STORAGE_STATUSES:
            errors.append(f"storage_status:{event_id}")
        if event.get("storage_status") != "confirmed" and (
            event.get("storage_power_mw") is not None or event.get("storage_energy_mwh") is not None
        ):
            errors.append(f"nonconfirmed_event_storage_value:{event_id}")
        if event.get("storage_status") != "confirmed" and (
            event.get("storage_power_mw_raw") is not None
            or event.get("storage_energy_mwh_raw") is not None
        ):
            errors.append(f"nonconfirmed_event_storage_raw:{event_id}")
        for field in ("storage_power_mw_raw", "storage_energy_mwh_raw"):
            raw = event.get(field)
            if raw is not None and not _raw_phrase_is_exact(str(raw), event_id, package):
                errors.append(f"raw_phrase_not_exact:{event_id}:{field}")
            if raw is not None and _raw_has_forbidden_context(str(raw), event_id, package):
                errors.append(f"non_project_measurement_context:{event_id}:{field}")

    if status in STORAGE_STATUSES and not invalid_sources and all(
        event.get("storage_status") in STORAGE_STATUSES
        and not set(event.get("generation_sources") or []).difference(GENERATION_SOURCES)
        for event in decision.get("event_decisions") or []
    ):
        aggregate_sources = sorted(
            {source for event in decision.get("event_decisions") or []
             for source in event.get("generation_sources") or []},
            key=SOURCE_ORDER.__getitem__,
        )
        aggregate_status = _aggregate_status(
            event.get("storage_status", "not_mentioned")
            for event in decision.get("event_decisions") or []
        )
        if set(sources) != set(aggregate_sources):
            errors.append("canonical_event_source_aggregation")
        if status != aggregate_status:
            errors.append("canonical_event_status_aggregation")
        expected_category, expected_tags = _policy_result(
            aggregate_sources, aggregate_status, package.get("canonical", {}).get("category")
        )
        if expected_category is None:
            if decision.get("resolution") == "resolved":
                errors.append("unresolved_atomic_facts")
        elif canonical.get("proposed_category") != expected_category:
            errors.append("deterministic_category")
        if canonical.get("technology_tags") != expected_tags:
            errors.append("deterministic_tags")
    subcategory = canonical.get("proposed_subcategory")
    if subcategory not in {None, "分布式"}:
        errors.append("subcategory")
    if subcategory == "分布式" and canonical.get("proposed_category") != "光伏":
        errors.append("subcategory_category")
    errors.extend(_validate_evidence(decision, package))
    return errors


def _decision_digest(decision: dict[str, Any]) -> str:
    """Hash one parsed decision in canonical JSON form."""
    return hashlib.sha256(_json_bytes(decision)).hexdigest()


def select_risk_reviews(campaign_dir: Path, output_path: Path) -> dict[str, Any]:
    """按批准的冲突、类别变化、多源、容量与低置信条件生成 Sol 风险复核清单。"""
    if output_path.exists():
        raise FileExistsError(output_path)
    manifest = json.loads((campaign_dir / "manifest.json").read_text(encoding="utf-8"))
    items = []
    reason_counts: Counter[str] = Counter()
    for meta in manifest["packages"]:
        project_id = meta["project_id"]
        package = json.loads((campaign_dir / meta["path"]).read_text(encoding="utf-8"))
        decision_path = campaign_dir / "terra-decisions" / f"{project_id}.json"
        if not decision_path.is_file():
            reasons = ["missing_terra_decision"]
            decision = None
            terra_hash = None
        else:
            decision = json.loads(decision_path.read_text(encoding="utf-8"))
            terra_hash = _decision_digest(decision)
            reasons = []
            errors = validate_decision(decision, package, meta["sha256"])
            if errors:
                reasons.append("invalid_or_conflicting_decision")
            canonical = decision.get("canonical_decision") or {}
            sources = canonical.get("generation_sources") or []
            effective_renewables = {
                source for source in sources
                if source in {"光伏", "风电", "水电", "地热及余热", "生物质"}
            }
            if decision.get("resolution") != "resolved":
                reasons.append("terra_needs_manual_review")
            if float(decision.get("confidence") or 0) < 0.85:
                reasons.append("confidence_below_0_85")
            if canonical.get("proposed_category") != meta.get("category"):
                reasons.append("main_category_change")
            if len(effective_renewables) >= 2:
                reasons.append("multiple_renewable_sources")
            if "火电" in sources:
                reasons.append("thermal_or_backup_source")
            if meta.get("article_count", 0) > 1 or meta.get("event_count", 0) > 1:
                reasons.append("multiple_articles_or_events")
            if meta.get("category") == "新能源制造业":
                reasons.append("manufacturing_capacity_confusion")
            if "existing_storage_numeric" in meta.get("selection_reasons", []):
                reasons.append("mw_mwh_existing_value")
            if (
                package.get("canonical", {}).get("subcategory") == "光储"
                and canonical.get("storage_status") != "confirmed"
            ):
                reasons.append("legacy_light_storage_without_confirmed_evidence")
        reasons = list(dict.fromkeys(reasons))
        if not reasons:
            continue
        reason_counts.update(reasons)
        items.append({
            "project_id": project_id,
            "package_path": meta["path"],
            "package_sha256": meta["sha256"],
            "terra_decision_path": f"terra-decisions/{project_id}.json",
            "terra_decision_sha256": terra_hash,
            "reasons": reasons,
        })
    payload = {
        "campaign_version": CAMPAIGN_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_candidate_count": len(manifest["packages"]),
        "risk_count": len(items),
        "reason_counts": dict(sorted(reason_counts.items())),
        "items": items,
    }
    _write_json(output_path, payload)
    return payload


def compile_dry_run(campaign_dir: Path, main_db: Path, output_dir: Path) -> dict[str, Any]:
    """Compile decisions into before/after and exception reports without writing the database."""
    if output_dir.exists():
        raise FileExistsError(output_dir)
    manifest = json.loads((campaign_dir / "manifest.json").read_text(encoding="utf-8"))
    index = {item["project_id"]: item for item in manifest["packages"]}
    packages = {
        project_id: json.loads((campaign_dir / meta["path"]).read_text(encoding="utf-8"))
        for project_id, meta in index.items()
    }
    risk_path = campaign_dir / "sol-risk-manifest.json"
    risk_ids = set()
    if risk_path.is_file():
        risk_manifest = json.loads(risk_path.read_text(encoding="utf-8"))
        risk_ids = {item["project_id"] for item in risk_manifest.get("items") or []}
    decisions: dict[str, dict[str, tuple[dict[str, Any], str]]] = {}
    terra_digests: dict[str, str] = {}
    exceptions = []
    submitted_projects: set[str] = set()
    invalid_roles: set[tuple[str, str]] = set()
    for role, directory in (("terra", campaign_dir / "terra-decisions"), ("sol", campaign_dir / "sol-decisions")):
        for path, decision in _iter_decisions(directory):
            project_id = str(decision.get("project_id") or "")
            if project_id not in index:
                exceptions.append({"project_id": project_id, "reason": "unknown_project", "path": str(path), "blocking": 1})
                continue
            submitted_projects.add(project_id)
            decision_digest = _decision_digest(decision)
            if role == "terra":
                terra_digests[project_id] = decision_digest
            errors = validate_decision(decision, packages[project_id], index[project_id]["sha256"])
            if decision.get("role") != role:
                errors.append("directory_role_mismatch")
            if role == "terra" and decision.get("parent_decision_sha256") not in {None, ""}:
                errors.append("terra_parent_must_be_empty")
            if role == "sol":
                expected_parent = terra_digests.get(project_id)
                if not expected_parent or decision.get("parent_decision_sha256") != expected_parent:
                    errors.append("sol_parent_decision_sha256")
            if errors:
                invalid_roles.add((project_id, role))
                exceptions.append({
                    "project_id": project_id, "reason": ";".join(errors), "path": str(path),
                    "blocking": int(not (role == "terra" and project_id in risk_ids)),
                })
                continue
            decisions.setdefault(project_id, {})[role] = (decision, decision_digest)

    changes = []
    unresolved = []
    with _readonly(main_db) as connection:
        for project_id, package in packages.items():
            pair = decisions.get(project_id, {})
            if project_id in risk_ids:
                chosen = pair.get("sol")
                if chosen is None:
                    if (project_id, "sol") not in invalid_roles:
                        exceptions.append({
                            "project_id": project_id, "reason": "missing_sol_risk_review",
                            "path": "", "blocking": 1,
                        })
                    continue
            else:
                chosen = pair.get("sol") or pair.get("terra")
            if chosen is None:
                if project_id not in submitted_projects:
                    exceptions.append({"project_id": project_id, "reason": "missing_decision", "path": "", "blocking": 1})
                continue
            decision, decision_hash = chosen
            if decision.get("resolution") != "resolved":
                exceptions.append({"project_id": project_id, "reason": "needs_manual_review", "path": "", "blocking": 0})
                unresolved.append({
                    "project_id": project_id, "decision_role": decision["role"],
                    "decision_model": decision["model"], "decision_sha256": decision_hash,
                    "review_reason": decision.get("review_reason"),
                    "canonical_decision": decision["canonical_decision"],
                })
                continue
            current = connection.execute(
                "SELECT id,category,subcategory FROM canonical_projects WHERE id=? AND excluded=0", (project_id,)
            ).fetchone()
            if current is None:
                exceptions.append({"project_id": project_id, "reason": "project_missing_or_excluded", "path": "", "blocking": 1})
                continue
            canonical = decision["canonical_decision"]
            changes.append({
                "project_id": project_id, "decision_role": decision["role"],
                "decision_model": decision["model"],
                "decision_sha256": decision_hash,
                "before": dict(current),
                "after": {
                    "category": canonical["proposed_category"],
                    "subcategory": canonical["proposed_subcategory"],
                    "technology_tags_json": json.dumps(canonical["technology_tags"], ensure_ascii=False),
                },
                "canonical_decision": canonical,
                "event_decisions": decision["event_decisions"],
            })

    output_dir.mkdir(parents=True)
    _write_json(output_dir / "changes.json", changes)
    _write_json(output_dir / "unresolved.json", unresolved)
    with (output_dir / "before-after.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["project_id", "decision_role", "before_category",
            "after_category", "before_subcategory", "after_subcategory", "technology_tags_json"])
        writer.writeheader()
        for item in changes:
            writer.writerow({"project_id": item["project_id"], "decision_role": item["decision_role"],
                "before_category": item["before"]["category"], "after_category": item["after"]["category"],
                "before_subcategory": item["before"]["subcategory"], "after_subcategory": item["after"]["subcategory"],
                "technology_tags_json": item["after"]["technology_tags_json"]})
    with (output_dir / "exceptions.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["project_id", "reason", "path", "blocking"])
        writer.writeheader(); writer.writerows(exceptions)
    blocking_count = sum(int(item["blocking"]) for item in exceptions)
    plan = {
        "campaign_version": CAMPAIGN_VERSION, "main_db_sha256": _sha256(main_db),
        "candidate_count": len(index), "change_count": len(changes), "exception_count": len(exceptions),
        "blocking_exception_count": blocking_count, "unresolved_count": len(unresolved),
        "changes_sha256": _sha256(output_dir / "changes.json"),
        "unresolved_sha256": _sha256(output_dir / "unresolved.json"),
        "apply_allowed": blocking_count == 0 and len(changes) + len(unresolved) == len(index),
    }
    _write_json(output_dir / "apply-plan.json", plan)
    return plan


def _backup_database(main_db: Path) -> Path:
    """Create and verify a WAL-safe SQLite backup before any apply transaction."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    destination = main_db.with_name(f"{main_db.name}.bak.storage-taxonomy.{stamp}")
    source = sqlite3.connect(str(main_db)); backup = sqlite3.connect(str(destination))
    try:
        source.backup(backup)
    finally:
        source.close(); backup.close()
    if not destination.is_file() or not destination.stat().st_size:
        raise RuntimeError("数据库备份失败")
    return destination


def _generation_capacity_target(
    event: dict[str, Any], current: sqlite3.Row,
) -> tuple[float | None, str | None, str | None, float | None]:
    """Return generation-only capacity fields, clearing legacy storage values deterministically."""
    current_values = (
        current["capacity_mw"], current["capacity_mw_raw"],
        current["capacity_source"], current["pv_capacity_mw"],
    )
    if not event.get("generation_sources"):
        return None, None, None, None
    storage_energy = event.get("storage_energy_mwh")
    capacity = current["capacity_mw"]
    capacity_text = " ".join(
        str(current[field] or "") for field in ("capacity_mw_raw", "capacity_details")
    )
    if (
        storage_energy is not None
        and capacity is not None
        and abs(float(capacity) - float(storage_energy)) < 1e-9
        and STORAGE_ENERGY_UNIT.search(capacity_text)
    ):
        return None, None, None, current["pv_capacity_mw"]
    return current_values


def _already_applied(
    connection: sqlite3.Connection,
    changes: list[dict[str, Any]],
    unresolved: list[dict[str, Any]],
) -> bool:
    """Return true only when every resolved value and unresolved audit row equals the target state."""
    for item in changes:
        row = connection.execute(
            "SELECT category,subcategory,technology_tags_json FROM canonical_projects WHERE id=?",
            (item["project_id"],),
        ).fetchone()
        after = item["after"]
        if row is None or (row["category"], row["subcategory"], row["technology_tags_json"]) != (
            after["category"], after["subcategory"], after["technology_tags_json"]
        ):
            return False
        for event in item["event_decisions"]:
            current = connection.execute(
                "SELECT generation_sources_json,storage_status,storage_power_mw,storage_energy_mwh,"
                "storage_power_mw_raw,storage_energy_mwh_raw,energy_composition_evidence,"
                "capacity_mw,capacity_mw_raw,capacity_source,pv_capacity_mw,capacity_details "
                "FROM project_events WHERE id=?", (event["event_id"],),
            ).fetchone()
            capacity_target = _generation_capacity_target(event, current) if current is not None else ()
            expected = (
                json.dumps(event["generation_sources"], ensure_ascii=False), event["storage_status"],
                event["storage_power_mw"], event["storage_energy_mwh"], event["storage_power_mw_raw"],
                event["storage_energy_mwh_raw"], json.dumps(event["energy_composition_evidence"], ensure_ascii=False),
                *capacity_target, current["capacity_details"],
            )
            if current is None or tuple(current) != expected:
                return False
        shadow = connection.execute(
            "SELECT generation_sources_json,storage_status,storage_power_mw,storage_energy_mwh,"
            "proposed_category,technology_tags_json,classification_version,needs_review,"
            "classification_source FROM project_classification_shadow WHERE canonical_id=?",
            (item["project_id"],),
        ).fetchone()
        canonical = item["canonical_decision"]
        expected_shadow = (
            json.dumps(canonical["generation_sources"], ensure_ascii=False),
            canonical["storage_status"], canonical["storage_power_mw"], canonical["storage_energy_mwh"],
            canonical["proposed_category"], item["after"]["technology_tags_json"],
            "energy_taxonomy_v2", 0,
            f"{item['decision_role']}:{item['decision_model']}:{item['decision_sha256']}",
        )
        if shadow is None or tuple(shadow) != expected_shadow:
            return False
    for item in unresolved:
        shadow = connection.execute(
            "SELECT proposed_category,technology_tags_json,classification_version,needs_review,"
            "review_reason,classification_source FROM project_classification_shadow WHERE canonical_id=?",
            (item["project_id"],),
        ).fetchone()
        canonical = item["canonical_decision"]
        expected = (
            canonical["proposed_category"],
            json.dumps(canonical["technology_tags"], ensure_ascii=False),
            "energy_taxonomy_v2", 1, item.get("review_reason"),
            f"{item['decision_role']}:{item['decision_model']}:{item['decision_sha256']}",
        )
        if shadow is None or tuple(shadow) != expected:
            return False
    return True


def apply_compiled(main_db: Path, compiled_dir: Path, confirmation: str) -> dict[str, Any]:
    """Apply a fully resolved compile exactly once using backup and one transaction."""
    if confirmation != CAMPAIGN_VERSION:
        raise ValueError(f"必须显式传入 --confirm {CAMPAIGN_VERSION}")
    plan = json.loads((compiled_dir / "apply-plan.json").read_text(encoding="utf-8"))
    changes_path = compiled_dir / "changes.json"
    unresolved_path = compiled_dir / "unresolved.json"
    if not plan.get("apply_allowed") or plan.get("blocking_exception_count", plan.get("exception_count", 0)):
        raise RuntimeError("apply gate 失败：存在缺失或无效决策")
    if _sha256(changes_path) != plan["changes_sha256"]:
        raise RuntimeError("apply gate 失败：变更计划 hash 漂移")
    if not unresolved_path.is_file() or _sha256(unresolved_path) != plan.get("unresolved_sha256"):
        raise RuntimeError("apply gate 失败：未决审计计划 hash 漂移")
    changes = json.loads(changes_path.read_text(encoding="utf-8"))
    unresolved = json.loads(unresolved_path.read_text(encoding="utf-8"))
    connection = sqlite3.connect(str(main_db))
    connection.row_factory = sqlite3.Row
    required_canonical = {"technology_tags_json"}
    required_events = set(EVENT_FACT_COLUMNS)
    if not required_canonical.issubset(_table_columns(connection, "canonical_projects")):
        connection.close(); raise RuntimeError("缺少 canonical_projects.technology_tags_json schema")
    if not required_events.issubset(_table_columns(connection, "project_events")):
        connection.close(); raise RuntimeError("缺少 project_events 能源事实 schema")
    required_shadow = {
        "canonical_id", "generation_sources_json", "storage_status", "storage_power_mw",
        "storage_energy_mwh", "proposed_category", "technology_tags_json",
        "classification_version", "needs_review", "review_reason", "classification_source",
        "manual_locked", "updated_at",
    }
    if not required_shadow.issubset(_table_columns(connection, "project_classification_shadow")):
        connection.close(); raise RuntimeError("缺少 project_classification_shadow v2 schema")
    if _sha256(main_db) != plan["main_db_sha256"]:
        if _already_applied(connection, changes, unresolved):
            connection.close()
            return {
                "updated_projects": 0, "audited_unresolved": len(unresolved),
                "backup": None, "idempotent": True,
            }
        connection.close()
        raise RuntimeError("apply gate 失败：数据库 hash 漂移且目标状态尚未完整落库")
    backup = _backup_database(main_db)
    before_ids = {row[0] for row in connection.execute("SELECT id FROM canonical_projects")}
    event_ids = {row[0] for row in connection.execute("SELECT id FROM project_events")}
    entity_ids = (
        {row[0] for row in connection.execute("SELECT id FROM chinese_entities")}
        if _table_columns(connection, "chinese_entities") else set()
    )
    before_foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
    updated = 0
    try:
        connection.execute("BEGIN IMMEDIATE")
        applied_at = datetime.now(timezone.utc).isoformat()
        for item in changes:
            project_id = item["project_id"]
            current = connection.execute(
                "SELECT category,subcategory,technology_tags_json FROM canonical_projects WHERE id=?", (project_id,)
            ).fetchone()
            expected = item["before"]
            after = item["after"]
            already = current["category"] == after["category"] and current["subcategory"] == after["subcategory"] \
                and current["technology_tags_json"] == after["technology_tags_json"]
            if not already and (current["category"] != expected["category"] or current["subcategory"] != expected["subcategory"]):
                raise RuntimeError(f"并发漂移：{project_id}")
            if not already:
                connection.execute("UPDATE canonical_projects SET category=?,subcategory=?,technology_tags_json=? WHERE id=?",
                    (after["category"], after["subcategory"], after["technology_tags_json"], project_id))
                updated += 1
            for event in item["event_decisions"]:
                current_event = connection.execute(
                    "SELECT capacity_mw,capacity_mw_raw,capacity_source,pv_capacity_mw,capacity_details "
                    "FROM project_events WHERE id=?", (event["event_id"],),
                ).fetchone()
                if current_event is None:
                    raise RuntimeError(f"事件不存在：{event['event_id']}")
                capacity_target = _generation_capacity_target(event, current_event)
                connection.execute(
                    "UPDATE project_events SET generation_sources_json=?,storage_status=?,storage_power_mw=?,"
                    "storage_energy_mwh=?,storage_power_mw_raw=?,storage_energy_mwh_raw=?,energy_composition_evidence=?,"
                    "capacity_mw=?,capacity_mw_raw=?,capacity_source=?,pv_capacity_mw=? WHERE id=?",
                    (json.dumps(event["generation_sources"], ensure_ascii=False), event["storage_status"],
                     event["storage_power_mw"], event["storage_energy_mwh"], event["storage_power_mw_raw"],
                     event["storage_energy_mwh_raw"], json.dumps(event["energy_composition_evidence"], ensure_ascii=False),
                    *capacity_target,
                    event["event_id"]),
                )
            canonical = item["canonical_decision"]
            classification_source = (
                f"{item['decision_role']}:{item['decision_model']}:{item['decision_sha256']}"
            )
            connection.execute(
                """INSERT INTO project_classification_shadow
                   (canonical_id,generation_sources_json,storage_status,storage_power_mw,
                    storage_energy_mwh,proposed_category,technology_tags_json,
                    classification_version,needs_review,review_reason,classification_source,
                    manual_locked,updated_at)
                   VALUES (?,?,?,?,?,?,?,'energy_taxonomy_v2',0,NULL,?,0,?)
                   ON CONFLICT(canonical_id) DO UPDATE SET
                     generation_sources_json=excluded.generation_sources_json,
                     storage_status=excluded.storage_status,
                     storage_power_mw=excluded.storage_power_mw,
                     storage_energy_mwh=excluded.storage_energy_mwh,
                     proposed_category=excluded.proposed_category,
                     technology_tags_json=excluded.technology_tags_json,
                     classification_version=excluded.classification_version,
                     needs_review=0,review_reason=NULL,
                     classification_source=excluded.classification_source,
                     manual_locked=0,updated_at=excluded.updated_at""",
                (
                    project_id,
                    json.dumps(canonical["generation_sources"], ensure_ascii=False),
                    canonical["storage_status"], canonical["storage_power_mw"],
                    canonical["storage_energy_mwh"], canonical["proposed_category"],
                    after["technology_tags_json"], classification_source, applied_at,
                ),
            )
        for item in unresolved:
            canonical = item["canonical_decision"]
            classification_source = (
                f"{item['decision_role']}:{item['decision_model']}:{item['decision_sha256']}"
            )
            connection.execute(
                """INSERT INTO project_classification_shadow
                   (canonical_id,generation_sources_json,storage_status,storage_power_mw,
                    storage_energy_mwh,proposed_category,technology_tags_json,
                    classification_version,needs_review,review_reason,classification_source,
                    manual_locked,updated_at)
                   VALUES (?,?,?,?,?,?,?,'energy_taxonomy_v2',1,?,?,0,?)
                   ON CONFLICT(canonical_id) DO UPDATE SET
                     generation_sources_json=excluded.generation_sources_json,
                     storage_status=excluded.storage_status,
                     storage_power_mw=excluded.storage_power_mw,
                     storage_energy_mwh=excluded.storage_energy_mwh,
                     proposed_category=excluded.proposed_category,
                     technology_tags_json=excluded.technology_tags_json,
                     classification_version=excluded.classification_version,
                     needs_review=1,review_reason=excluded.review_reason,
                     classification_source=excluded.classification_source,
                     manual_locked=0,updated_at=excluded.updated_at""",
                (
                    item["project_id"],
                    json.dumps(canonical["generation_sources"], ensure_ascii=False),
                    canonical["storage_status"], canonical["storage_power_mw"],
                    canonical["storage_energy_mwh"], canonical["proposed_category"],
                    json.dumps(canonical["technology_tags"], ensure_ascii=False),
                    item.get("review_reason"), classification_source, applied_at,
                ),
            )
        if before_ids != {row[0] for row in connection.execute("SELECT id FROM canonical_projects")}:
            raise RuntimeError("canonical ID 集合发生变化")
        if event_ids != {row[0] for row in connection.execute("SELECT id FROM project_events")}:
            raise RuntimeError("event ID 集合发生变化")
        after_entity_ids = (
            {row[0] for row in connection.execute("SELECT id FROM chinese_entities")}
            if _table_columns(connection, "chinese_entities") else set()
        )
        if entity_ids != after_entity_ids:
            raise RuntimeError("entity ID 集合发生变化")
        if before_foreign_keys != list(connection.execute("PRAGMA foreign_key_check")):
            raise RuntimeError("外键校验结果发生变化")
        connection.commit()
    except Exception:
        connection.rollback(); raise
    finally:
        connection.close()
    return {
        "updated_projects": updated, "audited_unresolved": len(unresolved),
        "backup": str(backup), "idempotent": False,
    }


def main() -> None:
    """Expose build, risk selection, compile, and guarded apply phases as an explicit CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--main-db", type=Path, required=True); build.add_argument("--raw-db", type=Path, required=True)
    build.add_argument("--out", type=Path, required=True)
    risk_parser = commands.add_parser("select-risk")
    risk_parser.add_argument("--campaign", type=Path, required=True)
    risk_parser.add_argument("--out", type=Path, required=True)
    compile_parser = commands.add_parser("compile")
    compile_parser.add_argument("--campaign", type=Path, required=True); compile_parser.add_argument("--main-db", type=Path, required=True)
    compile_parser.add_argument("--out", type=Path, required=True)
    apply_parser = commands.add_parser("apply")
    apply_parser.add_argument("--main-db", type=Path, required=True); apply_parser.add_argument("--compiled", type=Path, required=True)
    apply_parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    if args.command == "build":
        result = build_campaign(args.main_db, args.raw_db, args.out)
    elif args.command == "select-risk":
        result = select_risk_reviews(args.campaign, args.out)
    elif args.command == "compile":
        result = compile_dry_run(args.campaign, args.main_db, args.out)
    else:
        result = apply_compiled(args.main_db, args.compiled, args.confirm)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
