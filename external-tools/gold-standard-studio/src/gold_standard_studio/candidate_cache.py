"""Persist content-addressed Studio candidate values without touching production data."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

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

    def __init__(self, directory: Path) -> None:
        """Configure the private cache directory without creating or reading source data."""
        self._directory = Path(directory)

    def load(self, key: CandidateCacheKey) -> CandidateValue | None:
        """Return a validated cached value only when its embedded key exactly matches."""
        path = self._path_for(key)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        if raw.get("key") != asdict(key):
            return None
        try:
            return CandidateValue.model_validate(raw["candidate"])
        except (KeyError, TypeError, ValueError):
            return None

    def store(self, key: CandidateCacheKey, candidate: CandidateValue) -> Path:
        """Atomically write one canonical candidate record using a process-unique temporary file."""
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
