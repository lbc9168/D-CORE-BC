"""Persist content-addressed Studio candidate values without touching production data."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from gold_standard_studio.config import assert_mutable_paths_isolated
from gold_standard_studio.schemas import CandidateValue
from gold_standard_studio.sources import ArticleSnapshot


@dataclass(frozen=True)
class CandidateCacheKey:
    """Identify one candidate result by its input content and every behavior-changing version."""

    article_sha256: str
    source_name: str
    source_version: str
    prompt_hash: str
    parser_version: str

    def digest(self) -> str:
        """Return the stable filename digest for this complete cache identity."""
        payload = json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cache_key(
    article: ArticleSnapshot,
    *,
    source_name: str,
    model: str,
    prompt_hash: str,
    parser_version: str,
) -> CandidateCacheKey:
    """Build a content-addressed key from an immutable article and runner configuration."""
    return CandidateCacheKey(
        article_sha256=article.article_sha256,
        source_name=source_name,
        source_version=model,
        prompt_hash=prompt_hash,
        parser_version=parser_version,
    )


class CandidateCache:
    """Store one validated JSON candidate record per content-addressed key."""

    def __init__(self, directory: Path, *, source_paths: Sequence[str | Path] = ()) -> None:
        """Configure the private cache directory without creating or reading source data."""
        self._directory = Path(directory)
        self._source_paths = tuple(Path(path) for path in source_paths)
        self._on_invalid_read: Callable[[Path, bytes], None] | None = None

    def load(self, key: CandidateCacheKey) -> CandidateValue | None:
        """Return a validated successful cache value while ignoring malformed records without mutation."""
        path = self._path_for(key)
        try:
            contents = path.read_bytes()
        except (FileNotFoundError, OSError):
            return None
        try:
            raw = json.loads(contents)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._observe_invalid_read(path, contents)
            return None
        if not isinstance(raw, Mapping) or raw.get("key") != asdict(key):
            self._observe_invalid_read(path, contents)
            return None
        try:
            candidate = CandidateValue.model_validate(raw["candidate"])
        except (KeyError, TypeError, ValueError):
            self._observe_invalid_read(path, contents)
            return None
        if candidate.source_name != key.source_name or candidate.source_version != key.source_version:
            self._observe_invalid_read(path, contents)
            return None
        return candidate if candidate.status == "ok" else None

    def store(self, key: CandidateCacheKey, candidate: CandidateValue) -> Path:
        """Atomically write one canonical candidate record using a process-unique temporary file."""
        if self._source_paths:
            assert_mutable_paths_isolated(self._source_paths, [self._directory, self._path_for(key)])
        self._directory.mkdir(parents=True, exist_ok=True)
        destination = self._path_for(key)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        record = {"key": asdict(key), "candidate": candidate.model_dump(mode="json")}
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(record, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(destination)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        return destination

    def _path_for(self, key: CandidateCacheKey) -> Path:
        """Map a validated opaque identity to a confined JSON cache path."""
        return self._directory / f"{key.digest()}.json"

    def _observe_invalid_read(self, path: Path, contents: bytes) -> None:
        """Notify an optional observer of captured corrupt bytes without touching the current cache path."""
        if self._on_invalid_read is not None:
            self._on_invalid_read(path, contents)
