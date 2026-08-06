"""Publish immutable, evidence-preserving Studio gold releases with eval-compatible projections."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from typing import Any, Iterable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from gold_standard_studio.field_registry import FIELD_REGISTRY
from gold_standard_studio.review_store import ReviewStore
from gold_standard_studio.schemas import GoldEntity, GoldField, RichGoldRecord


_VERSION_PATTERN = re.compile(r"v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\Z")
_ALLOWED_STATUSES = {"valid", "no_chinese", "irrelevant", "chinese_unnamed"}
_SECRET_KEY_PARTS = ("api_key", "apikey", "authorization", "credential", "password", "secret", "token")


class PublishError(ValueError):
    """Signal that a review snapshot cannot safely become an immutable gold release."""


@dataclass(frozen=True)
class PublishedPaths:
    """Name every validated artifact in one successfully committed immutable version directory."""

    version_dir: Path
    rich_gold: Path
    eval_projection: Path
    manifest: Path
    schema: Path
    distribution: Path
    changelog: Path


class ProjectionProject(BaseModel):
    """Validate the legacy-compatible project shape written into an eval projection row."""

    model_config = ConfigDict(extra="forbid")
    project_name: JsonValue | None = None
    country: JsonValue | None = None
    location: JsonValue | None = None
    category: JsonValue | None = None
    generation_sources: JsonValue | None = None
    storage_status: JsonValue | None = None
    capacity_mw: JsonValue | None = None
    storage_power_mw: JsonValue | None = None
    storage_energy_mwh: JsonValue | None = None
    project_stage: JsonValue | None = None
    project_developer: JsonValue | None = None
    planned_start_date: JsonValue | None = None
    planned_complete_date: JsonValue | None = None
    investment_date: JsonValue | None = None
    total_investment_usd: JsonValue | None = None
    contract_amount_usd: JsonValue | None = None
    financing_amount_usd: JsonValue | None = None
    financing_model: JsonValue | None = None
    project_summary: JsonValue | None = None
    entities: list[dict[str, JsonValue]] = Field(default_factory=list)


class ProjectionRecord(BaseModel):
    """Validate one deterministic eval row while allowing only documented Studio metadata."""

    model_config = ConfigDict(extra="forbid")
    source_link: str = Field(min_length=1)
    article_id: str | None = None
    status: str
    projects: list[ProjectionProject] = Field(default_factory=list)
    dataset_split: str | None = None
    risk_tags: list[str] = Field(default_factory=list)
    field_eligibility: dict[str, bool] = Field(default_factory=dict)


class PublicationManifest(BaseModel):
    """Validate release metadata that binds immutable artifact hashes to a requested version."""

    model_config = ConfigDict(extra="forbid")
    version: str
    sample_ids: list[str]
    record_count: int = Field(ge=0)
    artifacts: dict[str, str]


class DistributionReport(BaseModel):
    """Validate deterministic release distributions used for audit and sampling review."""

    model_config = ConfigDict(extra="forbid")
    status: dict[str, int]
    category: dict[str, int]
    split: dict[str, int]
    difficulty: dict[str, int]
    risk_tag: dict[str, int]


def publish_gold(store: ReviewStore, output_root: str | Path, version: str) -> PublishedPaths:
    """Publish all reviewed records into one fsync-backed immutable release directory without mutating the store."""
    _validate_version(version)
    root = Path(output_root)
    if root.exists() and not root.is_dir():
        raise NotADirectoryError(f"output root is not a directory: {root}")
    final_dir = root / version
    if final_dir.exists():
        raise FileExistsError(f"gold version already exists: {final_dir}")
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / f".{version}.{uuid4().hex}.tmp"
    records = _load_records(store)
    _assert_publishable(records)
    rich_records = [_public_rich_record(record) for record in records]
    projection_records = [_project_record(record) for record in records]
    _validate_jsonl_models(rich_records, RichGoldRecord)
    _validate_jsonl_models(projection_records, ProjectionRecord)
    try:
        temporary.mkdir()
        rich_path = temporary / "rich_gold.jsonl"
        projection_path = temporary / "eval_projection.jsonl"
        schema_path = temporary / "rich_gold.schema.json"
        distribution_path = temporary / "distribution.json"
        changelog_path = temporary / "CHANGELOG.md"
        manifest_path = temporary / "manifest.json"
        _write_jsonl(rich_path, rich_records)
        _write_jsonl(projection_path, projection_records)
        _write_canonical_json(schema_path, RichGoldRecord.model_json_schema())
        distribution = _distribution(records, projection_records)
        DistributionReport.model_validate(distribution)
        _write_canonical_json(distribution_path, distribution)
        _write_text(changelog_path, _changelog(version, len(records)))
        manifest = _manifest(version, records, rich_path, projection_path, schema_path, distribution_path, changelog_path)
        PublicationManifest.model_validate(manifest)
        _write_canonical_json(manifest_path, manifest)
        _validate_staged_artifacts(temporary)
        _fsync_directory(temporary)
        temporary.replace(final_dir)
        _fsync_directory(root)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return PublishedPaths(
        version_dir=final_dir,
        rich_gold=final_dir / "rich_gold.jsonl",
        eval_projection=final_dir / "eval_projection.jsonl",
        manifest=final_dir / "manifest.json",
        schema=final_dir / "rich_gold.schema.json",
        distribution=final_dir / "distribution.json",
        changelog=final_dir / "CHANGELOG.md",
    )


def load_projection(path: str | Path) -> list[dict[str, Any]]:
    """Load a published eval projection through its release-local Pydantic compatibility contract."""
    return [record.model_dump(exclude_none=True) for record in _load_jsonl_models(Path(path), ProjectionRecord)]


def project_field(field: GoldField) -> tuple[JsonValue | None, bool]:
    """Map a reviewed rich field to its legacy eval value and exact scoring eligibility flag."""
    if field.state == "present":
        return field.value, True
    if field.state == "not_mentioned":
        return None, True
    if field.state in {"not_applicable", "uncertain"}:
        return None, False
    raise PublishError(f"state {field.state} is not publishable")


def _validate_version(version: str) -> None:
    """Reject non-semantic versions so a caller cannot create traversal or ambiguous release paths."""
    if not isinstance(version, str) or not _VERSION_PATTERN.fullmatch(version):
        raise ValueError("version must match vMAJOR.MINOR.PATCH")


def _load_records(store: ReviewStore) -> list[RichGoldRecord]:
    """Read all persisted review drafts in stable ID order through a SQLite read-only connection."""
    database_uri = f"{store.path.resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(database_uri, uri=True) as conn:
            rows = conn.execute("SELECT payload_json FROM article_drafts ORDER BY sample_id").fetchall()
    except sqlite3.Error as error:
        raise PublishError(f"cannot read review store: {error}") from error
    records = [RichGoldRecord.model_validate_json(row[0]) for row in rows]
    if not records:
        raise PublishError("no reviewed records are available for publication")
    return records


def _assert_publishable(records: Iterable[RichGoldRecord]) -> None:
    """Reject the entire release when any persisted draft is not confirmed and conflict-free."""
    for record in records:
        try:
            record.assert_publishable()
        except ValueError as error:
            raise PublishError(f"record {record.sample_id} is not publishable: {error}") from error


def _public_rich_record(record: RichGoldRecord) -> dict[str, Any]:
    """Preserve rich evidence and audit context while recursively redacting operational secret values."""
    payload = record.model_dump(mode="json")
    payload["audit_metadata"] = _redact_secrets(payload["audit_metadata"])
    return payload


def _redact_secrets(value: Any) -> Any:
    """Remove values under common operational-secret keys without discarding ordinary audit metadata."""
    if isinstance(value, dict):
        return {
            key: "[redacted]" if _is_secret_key(str(key)) else _redact_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    return value


def _is_secret_key(key: str) -> bool:
    """Identify common credential-bearing audit-metadata keys without treating regular provenance as secret."""
    lowered = key.casefold().replace("-", "_")
    return any(part in lowered for part in _SECRET_KEY_PARTS)


def _project_record(record: RichGoldRecord) -> dict[str, Any]:
    """Create one stable legacy eval row while retaining only documented projection metadata."""
    projects: list[dict[str, Any]] = []
    eligibility_values: dict[str, list[bool]] = {}
    for project in sorted(record.projects, key=lambda item: item.gold_project_id):
        projected: dict[str, Any] = {"entities": _project_entities(project.entities)}
        for field_name in sorted(project.fields):
            field = project.fields[field_name]
            value, eligible = project_field(field)
            eligibility_values.setdefault(field_name, []).append(eligible)
            if field_name in FIELD_REGISTRY and value is not None:
                projected[field_name] = value
        projects.append(projected)
    return ProjectionRecord(
        source_link=record.source_link or f"studio://{record.sample_id}",
        article_id=record.sample_id,
        status=_projection_status(record),
        projects=projects,
        dataset_split=record.dataset_split,
        risk_tags=sorted(set(record.risk_tags)),
        field_eligibility={name: all(flags) for name, flags in sorted(eligibility_values.items())},
    ).model_dump(exclude_none=True)


def _project_entities(entities: list[GoldEntity]) -> list[dict[str, JsonValue]]:
    """Convert role-specific rich entities into the established eval name and involvement-type pair."""
    return [
        {"name": entity.name, "involvement_type": entity.role}
        for entity in sorted(entities, key=lambda item: (item.name, item.role))
    ]


def _projection_status(record: RichGoldRecord) -> str:
    """Derive an eval status from reviewed structure while honoring an explicit safe negative classification."""
    declared = record.audit_metadata.get("eval_status")
    if isinstance(declared, str) and declared in _ALLOWED_STATUSES:
        if record.projects and declared != "valid":
            raise PublishError(f"record {record.sample_id} has projects but non-valid eval status")
        return declared
    return "valid" if record.projects else "irrelevant"


def _validate_jsonl_models(records: Iterable[dict[str, Any]], model: type[BaseModel]) -> None:
    """Validate every in-memory JSONL row before any release file can be committed."""
    for record in records:
        model.model_validate(record)


def _write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    """Write canonical JSONL and fsync the completed file before the containing directory is renamed."""
    content = "".join(_canonical_json(record) + "\n" for record in records)
    _write_text(path, content)


def _write_canonical_json(path: Path, value: Any) -> None:
    """Write one compact canonical JSON artifact and force its bytes to durable storage where supported."""
    _write_text(path, _canonical_json(value) + "\n")


def _write_text(path: Path, content: str) -> None:
    """Write UTF-8 release content and fsync its descriptor before publication proceeds."""
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _canonical_json(value: Any) -> str:
    """Serialize a JSON-compatible value with stable key ordering and no whitespace ambiguity."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _manifest(version: str, records: list[RichGoldRecord], *artifacts: Path) -> dict[str, Any]:
    """Bind each staged artifact's SHA-256 to its release version and deterministically sorted sample IDs."""
    return PublicationManifest(
        version=version,
        sample_ids=[record.sample_id for record in records],
        record_count=len(records),
        artifacts={path.name: _sha256_file(path) for path in sorted(artifacts, key=lambda item: item.name)},
    ).model_dump()


def _sha256_file(path: Path) -> str:
    """Calculate the SHA-256 of one completed staged release artifact."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _distribution(records: list[RichGoldRecord], projections: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Count release rows by status, category, split, exact difficulty score, and unique article risk tags."""
    status = Counter(projection["status"] for projection in projections)
    category = Counter(
        str(field.value)
        for record in records
        for project in record.projects
        for name, field in project.fields.items()
        if name == "category" and field.state == "present"
    )
    split = Counter(record.dataset_split or "unspecified" for record in records)
    difficulty = Counter(
        "unscored" if record.difficulty is None else format(record.difficulty.score, ".12g")
        for record in records
    )
    risk_tag = Counter(tag for record in records for tag in sorted(set(record.risk_tags)))
    return {
        "status": dict(sorted(status.items())),
        "category": dict(sorted(category.items())),
        "split": dict(sorted(split.items())),
        "difficulty": dict(sorted(difficulty.items())),
        "risk_tag": dict(sorted(risk_tag.items())),
    }


def _changelog(version: str, record_count: int) -> str:
    """Describe the immutable release contents without embedding timestamps or operational details."""
    return f"# Gold release {version}\n\nImmutable Studio publication containing {record_count} reviewed records.\n"


def _validate_staged_artifacts(directory: Path) -> None:
    """Re-open and validate every staged JSON artifact before its directory can become a visible version."""
    _load_jsonl_models(directory / "rich_gold.jsonl", RichGoldRecord)
    _load_jsonl_models(directory / "eval_projection.jsonl", ProjectionRecord)
    PublicationManifest.model_validate_json((directory / "manifest.json").read_text(encoding="utf-8"))
    DistributionReport.model_validate_json((directory / "distribution.json").read_text(encoding="utf-8"))
    json.loads((directory / "rich_gold.schema.json").read_text(encoding="utf-8"))
    if not (directory / "CHANGELOG.md").read_text(encoding="utf-8").strip():
        raise PublishError("release changelog must not be empty")


def _load_jsonl_models(path: Path, model: type[BaseModel]) -> list[BaseModel]:
    """Parse every nonblank canonical JSONL line into the requested Pydantic model with line context."""
    records: list[BaseModel] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line:
            continue
        try:
            records.append(model.model_validate_json(raw_line))
        except ValueError as error:
            raise PublishError(f"invalid {path.name} line {line_number}: {error}") from error
    return records


def _fsync_directory(path: Path) -> None:
    """Best-effort fsync a directory after metadata changes on platforms that expose directory descriptors."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
