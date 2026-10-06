"""S01B routine SQLite snapshots: five runs plus six calendar months.

Snapshots precede schema/data writes and use SQLite's WAL-safe backup API.
Only manifest-owned files are pruned; named correction baselines stay preserved.
"""
import argparse
import hashlib
import json
import logging
import re
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)
MODULE_ROOT = Path(__file__).resolve().parents[2]
RECENT_RUNS = 5
MONTHLY_MONTHS = 6
VERSION = "s01b-backup-v1"
STAMP_FORMAT = "%Y%m%d_%H%M%S_%f"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_hash(path: Path) -> str:
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"Invalid backup file: {path}")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError(f"Backup integrity check failed: {path}")
    return _sha256(path)


def _record(root: Path, action: str, **details) -> None:
    with (root / "maintenance.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"time": datetime.now().isoformat(), "action": action,
                                 **details}, ensure_ascii=False) + "\n")
    logger.info("backup %s: %s", action, details)


def _write_manifest(folder: Path, created: datetime, entries: list[dict]) -> None:
    with (folder / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump({"version": VERSION, "created_at": created.isoformat(), "files": entries},
                  stream, ensure_ascii=False, indent=2)


def _load_group(folder: Path) -> dict:
    """Validate ownership, paths and fingerprints before copying or deleting a group."""
    if folder.is_symlink() or (folder / "manifest.json").is_symlink():
        raise ValueError(f"Symlink in backup group: {folder}")
    data = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or set(data) != {"version", "created_at", "files"}
            or data.get("version") != VERSION or not isinstance(data.get("created_at"), str)):
        raise ValueError(f"Unknown backup manifest: {folder}")
    created = datetime.fromisoformat(data["created_at"])
    if created.tzinfo is not None or folder.name != created.strftime(STAMP_FORMAT):
        raise ValueError(f"Invalid backup timestamp: {folder}")
    entries = data.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"Empty backup manifest: {folder}")
    names = set()
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {"file", "source", "sha256"}
                or not all(isinstance(value, str) for value in entry.values())):
            raise ValueError(f"Invalid backup entry: {folder}")
        name = entry["file"]
        if (Path(name).name != name or name in {"", ".", "..", "manifest.json"}
                or name in names or not Path(entry["source"]).is_absolute()
                or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
            raise ValueError(f"Unsafe backup entry: {folder}")
        names.add(name)
        if _snapshot_hash(folder / name) != entry["sha256"]:
            raise ValueError(f"Backup hash mismatch: {folder / name}")
    if {path.name for path in folder.iterdir()} != names | {"manifest.json"}:
        raise ValueError(f"Unexpected files in backup group: {folder}")
    return {"path": folder, "created": created, "files": entries}


def _groups(parent: Path) -> list[dict]:
    if not parent.exists():
        return []
    if parent.is_symlink():
        raise ValueError(f"Symlink in backup storage: {parent}")
    return [_load_group(folder) for folder in sorted(parent.iterdir())]


def _copy_group(group: dict, parent: Path) -> dict:
    folder = parent / group["path"].name
    if folder.exists():
        existing = _load_group(folder)
        if existing["files"] != group["files"]:
            raise ValueError(f"Backup destination conflict: {folder}")
        return existing
    folder.mkdir(parents=True)
    try:
        for entry in group["files"]:
            with (folder / entry["file"]).open("xb") as dest:
                with (group["path"] / entry["file"]).open("rb") as source:
                    shutil.copyfileobj(source, dest)
        _write_manifest(folder, group["created"], group["files"])
        return _load_group(folder)
    except (OSError, sqlite3.Error, ValueError):
        _discard_incomplete(folder, [entry["file"] for entry in group["files"]])
        raise


def _discard_incomplete(folder: Path, names: list[str]) -> None:
    try:
        for name in names + ["manifest.json"]:
            (folder / name).unlink(missing_ok=True)
        folder.rmdir()
    except OSError:
        logger.exception("Failed to remove incomplete backup group: %s", folder)


def _remove_group(root: Path, group: dict) -> None:
    folder = group["path"]
    resolved = folder.resolve()
    if root.resolve() not in resolved.parents:
        raise ValueError(f"Refusing deletion outside backup storage: {folder}")
    verified = _load_group(folder)
    _record(root, "prune", group=str(folder), files=verified["files"])
    # No recursive deletion: unknown files or directories are never swept away.
    for entry in verified["files"]:
        (folder / entry["file"]).unlink()
    (folder / "manifest.json").unlink()
    folder.rmdir()


def retain_backups(root: Path, *, now: datetime | None = None) -> dict:
    """Keep five latest runs and the latest snapshot in each of six calendar months.

    The window includes the current month. Missing months are not synthesized.
    All monthly copies are verified before any older backup is removed.
    """
    root = root.resolve()
    now = now or datetime.now()
    recent = sorted(_groups(root / "recent"), key=lambda group: group["created"])
    monthly_root = root / "monthly"
    monthly = []
    if monthly_root.exists():
        if monthly_root.is_symlink():
            raise ValueError(f"Symlink in monthly backup storage: {monthly_root}")
        for month_dir in sorted(monthly_root.iterdir()):
            if not re.fullmatch(r"\d{4}-\d{2}", month_dir.name):
                raise ValueError(f"Unknown monthly backup directory: {month_dir}")
            groups = _groups(month_dir)
            if any(group["created"].strftime("%Y-%m") != month_dir.name for group in groups):
                raise ValueError(f"Monthly timestamp mismatch: {month_dir}")
            monthly.extend(groups)
    cutoff = now.year * 12 + now.month - MONTHLY_MONTHS + 1
    chosen = {}
    for group in sorted(recent + monthly, key=lambda group: group["created"]):
        created = group["created"]
        if created > now:
            raise ValueError(f"Backup timestamp is in the future: {group['path']}")
        if created.year * 12 + created.month >= cutoff:
            chosen[created.strftime("%Y-%m")] = group
    keep_monthly = set()
    for month, group in sorted(chosen.items()):
        target = _copy_group(group, monthly_root / month)
        keep_monthly.add(target["path"])
    # Refresh after promotions; an older monthly snapshot may now be superseded.
    for month_dir in sorted(monthly_root.iterdir()) if monthly_root.exists() else []:
        for group in _groups(month_dir):
            if group["path"] not in keep_monthly:
                _remove_group(root, group)
        if not any(month_dir.iterdir()):
            month_dir.rmdir()
    for group in recent[:-RECENT_RUNS]:
        _remove_group(root, group)
    summary = {"recent_runs": min(len(recent), RECENT_RUNS), "months": sorted(chosen)}
    _record(root, "retained", **summary)
    return summary


def _storage_root(paths: list[Path]) -> Path:
    defaults = {MODULE_ROOT / "data/projects.db", MODULE_ROOT / "data/review.db"}
    if set(paths) <= defaults:
        return MODULE_ROOT / "backup"
    # Custom/isolated databases stay beside their own inputs, with separate scopes.
    scope = hashlib.sha256("\n".join(sorted(map(str, paths))).encode()).hexdigest()[:12]
    return paths[0].parent / "backup" / scope


def backup_databases(db_paths: list[str | Path], *, now: datetime | None = None) -> Path | None:
    """Back up one extraction run as a group; failure stops writing and pruning."""
    paths = [Path(path).resolve() for path in db_paths]
    if not paths or len(set(paths)) != len(paths) or len({path.name for path in paths}) != len(paths):
        raise ValueError("Backup sources must have distinct paths and filenames")
    existing = [path for path in paths if path.exists()]
    if not existing:
        return None  # First extraction has no old database state to restore.
    created = now or datetime.now()
    root = _storage_root(paths)
    folder = root / "recent" / created.strftime(STAMP_FORMAT)
    folder.mkdir(parents=True)  # Exclusive run directory; never overwrite a snapshot.
    entries = []
    try:
        for source in existing:
            if not source.is_file():
                raise ValueError(f"Backup source is not a file: {source}")
            dest = folder / source.name
            with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as conn:
                with closing(sqlite3.connect(str(dest))) as output:
                    conn.backup(output)
            entries.append({"file": source.name, "source": str(source), "sha256": _snapshot_hash(dest)})
        _write_manifest(folder, created, entries)
        _load_group(folder)
    except (OSError, sqlite3.Error, ValueError):
        # An incomplete pair must not become a recovery point or poison future runs.
        _discard_incomplete(folder, [source.name for source in existing])
        raise
    _record(root, "created", group=str(folder), files=entries)
    retain_backups(root, now=created)
    return folder


def organize_legacy(module_root: Path = MODULE_ROOT, *, now: datetime | None = None) -> dict:
    """Organize only projects/review backup files directly in S01B and its data folder.

    Strict timestamped names enter rotation. Other named baselines are preserved.
    Legacy paired files with microsecond differences share their recorded second.
    """
    module_root = module_root.resolve()
    root = module_root / "backup"
    root.mkdir(exist_ok=True)
    pattern = re.compile(r"^(projects|review)\.db\.bak\.(\d{8})(?:_(\d{6})(?:_(\d{6}))?)?$")
    candidates = []
    by_run = {}
    for parent in (module_root, module_root / "data"):
        if not parent.exists():
            continue
        for path in sorted(parent.iterdir()):
            if not (path.name.startswith(("projects.db", "review.db")) and ".bak" in path.name):
                continue
            fingerprint = _snapshot_hash(path)
            match = pattern.fullmatch(path.name)
            item = {"path": str(path), "sha256": fingerprint}
            candidates.append(item)
            if match:
                created = datetime.strptime(match[2] + (match[3] or "000000"), "%Y%m%d%H%M%S")
                group = by_run.setdefault(created, [])
                name = match[1] + ".db"
                if any(entry["file"] == name for _, entry in group):
                    raise ValueError(f"Ambiguous legacy backup group: {path}")
                group.append((path, {"file": name, "source": str(module_root / "data" / name),
                                     "sha256": fingerprint}))
            else:
                dest = root / "preserved" / path.name
                dest.parent.mkdir(exist_ok=True)
                if dest.exists():
                    if _snapshot_hash(dest) != fingerprint:
                        raise ValueError(f"Preserved backup conflict: {dest}")
                else:
                    with dest.open("xb") as output, path.open("rb") as source:
                        shutil.copyfileobj(source, output)
                    if _snapshot_hash(dest) != fingerprint:
                        raise ValueError(f"Preserved backup hash mismatch: {dest}")
                item["preserved"] = str(dest)
    for created, sources in sorted(by_run.items()):
        folder = root / "recent" / created.strftime(STAMP_FORMAT)
        entries = [entry for _, entry in sources]
        if folder.exists():
            if _load_group(folder)["files"] != entries:
                raise ValueError(f"Legacy backup destination conflict: {folder}")
        else:
            folder.mkdir(parents=True)
            for path, entry in sources:
                with (folder / entry["file"]).open("xb") as output, path.open("rb") as source:
                    shutil.copyfileobj(source, output)
            _write_manifest(folder, created, entries)
            _load_group(folder)
    _record(root, "legacy_verified", files=candidates)
    summary = retain_backups(root, now=now)
    # Check all originals again before the first unlink; live databases are excluded.
    for item in candidates:
        if _sha256(Path(item["path"])) != item["sha256"]:
            raise ValueError(f"Legacy backup changed during organization: {item['path']}")
    for item in candidates:
        Path(item["path"]).unlink()
    _record(root, "legacy_organized", files=candidates, **summary)
    return {"organized_files": len(candidates), **summary}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organize", action="store_true", help="Organize existing S01B backups")
    args = parser.parse_args()
    if not args.organize:
        parser.error("Specify --organize to organize existing backups")
    print(json.dumps(organize_legacy(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
