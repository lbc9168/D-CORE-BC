"""Validate Studio paths and construct read-only SQLite connection URIs."""

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Iterable


_DATA_MUTABLE_RELATIVE_PATHS = (
    Path("studio-config.json"),
    Path("review.db"),
    Path("sample-manifest.json"),
    Path("model_cache"),
    Path("reports"),
)


@dataclass(frozen=True)
class StudioConfig:
    """Store the validated input locations and fixed sampling parameters for a Studio run."""

    raw_news_db: Path
    projects_db: Path
    main_db: Path
    data_dir: Path
    seed: int = 20260806
    pilot_size: int = 60
    target_size: int = 250
    negative_fraction: float = 0.18

    @classmethod
    def from_paths(cls, *, raw_news_db, projects_db, main_db, data_dir):
        """Create a configuration after verifying that each source database exists as a file."""
        values = {
            "raw_news_db": Path(raw_news_db),
            "projects_db": Path(projects_db),
            "main_db": Path(main_db),
        }
        for name, path in values.items():
            if not path.is_file():
                raise ValueError(f"{name} does not exist: {path}")
        mutable_root = Path(data_dir)
        assert_mutable_paths_isolated(
            values.values(),
            [mutable_root, *(mutable_root / relative for relative in _DATA_MUTABLE_RELATIVE_PATHS)],
        )
        return cls(**values, data_dir=mutable_root)

    @property
    def source_paths(self) -> tuple[Path, Path, Path]:
        """Return the complete immutable source database set for repeated mutable-path checks."""
        return (self.raw_news_db, self.projects_db, self.main_db)

    def assert_mutable_paths(self, *paths: str | Path) -> None:
        """Recheck caller-selected cache, report, review, or output paths against every configured source."""
        assert_mutable_paths_isolated(self.source_paths, [Path(path) for path in paths])


def sqlite_ro_uri(path: Path) -> str:
    """Return a SQLite URI that opens the resolved database path in read-only mode."""
    return f"file:{path.resolve().as_posix()}?mode=ro"


def assert_mutable_paths_isolated(
    source_paths: Iterable[str | Path], mutable_paths: Iterable[str | Path]
) -> None:
    """Reject resolved-path, symlink, same-file, and hardlink aliases between source DBs and mutable targets."""
    sources = [Path(path) for path in source_paths]
    mutable = [Path(path) for path in mutable_paths]
    source_identities = [(_resolved_identity(path), _stat_identity(path)) for path in sources]
    for target in mutable:
        target_resolved = _resolved_identity(target)
        target_stat = _stat_identity(target)
        for source, (source_resolved, source_stat) in zip(sources, source_identities, strict=True):
            same_resolved = target_resolved == source_resolved
            same_inode = target_stat is not None and source_stat is not None and target_stat == source_stat
            same_file = False
            if target.exists() and source.exists():
                try:
                    same_file = os.path.samefile(target, source)
                except OSError:
                    same_file = False
            if same_resolved or same_inode or same_file:
                raise ValueError(f"mutable path aliases source database: {target} -> {source}")


def _resolved_identity(path: Path) -> str:
    """Normalize a possibly nonexistent or symlinked path for case-insensitive filesystem identity comparison."""
    try:
        resolved = path.resolve(strict=False)
    except OSError as error:
        raise ValueError(f"cannot safely resolve path: {path}") from error
    return os.path.normcase(os.path.abspath(resolved))


def _stat_identity(path: Path) -> tuple[int, int] | None:
    """Return device/inode identity for an existing target so hardlinks cannot bypass resolved-path checks."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ValueError(f"cannot safely inspect path: {path}") from error
    return stat.st_dev, stat.st_ino
