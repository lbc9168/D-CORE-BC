"""Publish immutable, evidence-preserving Studio gold releases with eval-compatible projections."""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
from typing import Any, Iterable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from gold_standard_studio.field_registry import FIELD_REGISTRY
from gold_standard_studio.review_store import ReviewStore
from gold_standard_studio.schemas import GoldEntity, GoldField, RichGoldRecord


_VERSION_PATTERN = re.compile(r"v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\Z")
_ALLOWED_STATUSES = {"valid", "no_chinese", "irrelevant", "chinese_unnamed"}
_GOLD_DRAFT_MUTATION_ACTIONS = {"field_decision", "adjudication", "structure_decision"}


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


@dataclass(frozen=True)
class _ConfirmationProof:
    """Capture the one audited confirmation event that authorizes a persisted record for publication."""

    audit_id: int
    action: str
    created_at: str
    field_path: str | None


@dataclass(frozen=True)
class _PublicationRecord:
    """Bind a validated rich record to its durable confirmation proof from one SQLite snapshot."""

    record: RichGoldRecord
    confirmation: _ConfirmationProof


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
    field_eligibility: dict[str, bool] = Field(default_factory=dict)


class ProjectionRecord(BaseModel):
    """Validate one deterministic eval row while allowing only documented Studio metadata."""

    model_config = ConfigDict(extra="forbid")
    source_link: str = Field(min_length=1)
    article_id: str | None = None
    status: str
    projects: list[ProjectionProject] = Field(default_factory=list)
    dataset_split: str | None = None
    risk_tags: list[str] = Field(default_factory=list)
    difficulty: dict[str, JsonValue] | None = None


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
    root.mkdir(parents=True, exist_ok=True)
    final_dir = root / version
    with _version_lock(root, version):
        if final_dir.exists():
            raise FileExistsError(f"gold version already exists: {final_dir}")
        temporary = root / f".{version}.{uuid4().hex}.tmp"
        published_records = _load_records(store)
        records = [item.record for item in published_records]
        rich_records = [_public_rich_record(item) for item in published_records]
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
            _commit_version_directory(temporary, final_dir)
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


def _load_records(store: ReviewStore) -> list[_PublicationRecord]:
    """Read reviewed drafts and durable confirmation proofs from one stable read-only SQLite snapshot."""
    database_uri = f"{store.path.resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(database_uri, uri=True) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN")
            rows = conn.execute(
                "SELECT sample_id, payload_json, review_status FROM article_drafts ORDER BY sample_id"
            ).fetchall()
            audits = conn.execute(
                "SELECT id, sample_id, action, field_path, new_json, created_at FROM audit_log ORDER BY id"
            ).fetchall()
            conn.execute("ROLLBACK")
    except sqlite3.Error as error:
        raise PublishError(f"cannot read review store: {error}") from error
    if not rows:
        raise PublishError("no reviewed records are available for publication")
    audits_by_sample: dict[str, list[sqlite3.Row]] = {}
    for audit in audits:
        audits_by_sample.setdefault(audit["sample_id"], []).append(audit)
    published: list[_PublicationRecord] = []
    for row in rows:
        record = RichGoldRecord.model_validate_json(row["payload_json"])
        _assert_publishable(record, row["review_status"], audits_by_sample.get(record.sample_id, []))
        published.append(
            _PublicationRecord(record=record, confirmation=_confirmation_proof(audits_by_sample[record.sample_id]))
        )
    return published


def _assert_publishable(record: RichGoldRecord, indexed_status: str, audits: list[sqlite3.Row]) -> None:
    """Require matching reviewed payload/index status, audited confirmation, and no later gold draft mutation."""
    if indexed_status != record.review_status:
        raise PublishError(f"record {record.sample_id} status mismatch between draft and index")
    try:
        record.assert_publishable()
    except ValueError as error:
        raise PublishError(f"record {record.sample_id} is not publishable: {error}") from error
    confirmation = _confirmation_proof(audits)
    if any(
        audit["id"] > confirmation.audit_id and audit["action"] in _GOLD_DRAFT_MUTATION_ACTIONS
        for audit in audits
    ):
        raise PublishError(f"record {record.sample_id} has later gold-draft mutation after confirmation")


def _confirmation_proof(audits: list[sqlite3.Row]) -> _ConfirmationProof:
    """Return the latest audited reviewed confirmation whose payload and field path match the persisted status."""
    matches = [
        audit for audit in audits
        if audit["action"] == "article_confirmed"
        and audit["field_path"] == "article_review_status"
        and _is_reviewed_json(audit["new_json"])
    ]
    if not matches:
        raise PublishError("record is not publishable without a matching confirmation audit")
    audit = matches[-1]
    return _ConfirmationProof(
        audit_id=int(audit["id"]), action=audit["action"], created_at=audit["created_at"], field_path=audit["field_path"]
    )


def _is_reviewed_json(value: str | None) -> bool:
    """Validate the canonical audit payload proves a transition to the reviewed article state."""
    try:
        return json.loads(value or "null") == "reviewed"
    except json.JSONDecodeError:
        return False


def _public_rich_record(published: _PublicationRecord) -> dict[str, Any]:
    """Preserve rich evidence while publishing only allowlisted confirmation provenance instead of arbitrary metadata."""
    record = published.record
    payload = record.model_dump(mode="json")
    proof = published.confirmation
    payload["audit_metadata"] = {
        "confirmation": {
            "audit_id": proof.audit_id,
            "action": proof.action,
            "created_at": proof.created_at,
            "field_path": proof.field_path,
        }
    }
    return payload


def _project_record(record: RichGoldRecord) -> dict[str, Any]:
    """Create one stable legacy eval row while retaining only documented projection metadata."""
    projects: list[dict[str, Any]] = []
    for project in sorted(record.projects, key=lambda item: item.gold_project_id):
        projected: dict[str, Any] = {"entities": _project_entities(project.entities), "field_eligibility": {}}
        for field_name in sorted(project.fields):
            field = project.fields[field_name]
            value, eligible = project_field(field)
            projected["field_eligibility"][field_name] = eligible
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
        difficulty=record.difficulty.model_dump(mode="json") if record.difficulty is not None else None,
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


@contextmanager
def _version_lock(root: Path, version: str):
    """Hold a cooperative exclusive lock for one semantic version while its release directory is committed."""
    lock_path = root / f".{version}.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise FileExistsError(f"gold version is already being published: {version}") from error
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.fsync(descriptor)
        yield
    finally:
        os.close(descriptor)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _commit_version_directory(temporary: Path, final_dir: Path) -> None:
    """Atomically move a fully validated staging directory into a previously nonexistent final version path."""
    if os.name == "nt":
        _windows_rename_no_replace(temporary, final_dir)
        return
    if sys.platform.startswith("linux"):
        _linux_rename_no_replace(temporary, final_dir)
        return
    raise PublishError("platform lacks a verified no-replace directory rename; refusing publication")


def _windows_rename_no_replace(temporary: Path, final_dir: Path) -> None:
    """Use Windows rename semantics, which fail when a destination directory already exists, to prevent clobbering."""
    try:
        os.rename(temporary, final_dir)
    except FileExistsError:
        raise
    except OSError as error:
        if final_dir.exists():
            raise FileExistsError(f"gold version already exists: {final_dir}") from error
        raise


def _linux_rename_no_replace(temporary: Path, final_dir: Path) -> None:
    """Call Linux renameat2 with RENAME_NOREPLACE and fail closed if that atomic primitive is unavailable."""
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise PublishError("Linux renameat2 is unavailable; refusing non-atomic directory publication")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        -100, os.fsencode(temporary), -100, os.fsencode(final_dir), 1
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(f"gold version already exists: {final_dir}")
    if error_number in {errno.ENOSYS, errno.EINVAL}:
        raise PublishError("Linux renameat2 RENAME_NOREPLACE is unavailable; refusing publication")
    raise OSError(error_number, os.strerror(error_number), str(final_dir))


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
