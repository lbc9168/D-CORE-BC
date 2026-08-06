"""Validate Studio paths and construct read-only SQLite connection URIs."""

from dataclasses import dataclass
from pathlib import Path


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
        return cls(**values, data_dir=Path(data_dir))


def sqlite_ro_uri(path: Path) -> str:
    """Return a SQLite URI that opens the resolved database path in read-only mode."""
    return f"file:{path.resolve().as_posix()}?mode=ro"
