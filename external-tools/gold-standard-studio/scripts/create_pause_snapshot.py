"""Create an immutable Golden Standard Studio pause snapshot and recovery manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOL_ROOT = Path(__file__).resolve().parents[1]
WORKTREE_ROOT = TOOL_ROOT.parents[1]


def _sha256(path: Path) -> str:
    """Return the SHA-256 digest of one file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> str:
    """Read one stable Git identity value from the current worktree."""
    return subprocess.check_output(["git", *args], cwd=WORKTREE_ROOT, text=True).strip()


def _sqlite_snapshot(source: Path, destination: Path) -> None:
    """Create a transactionally consistent SQLite backup without modifying the source."""
    source_connection = sqlite3.connect(str(source)); destination_connection = sqlite3.connect(str(destination))
    try:
        source_connection.backup(destination_connection)
    finally:
        source_connection.close(); destination_connection.close()


def _review_counts(path: Path) -> dict[str, Any]:
    """Read Studio review counts and the required article 23627 checkpoint."""
    with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True) as connection:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("article_snapshots", "candidates", "drafts", "review_actions")
        }
        counts["confirmed_drafts"] = connection.execute("SELECT COUNT(*) FROM drafts WHERE confirmed=1").fetchone()[0]
        row = connection.execute(
            "SELECT revision,confirmed,updated_at FROM drafts WHERE article_id=23627"
        ).fetchone()
    if row is None or row[0] != 25:
        raise RuntimeError(f"23627 暂停门禁失败：预期 revision 25，实际 {row}")
    counts["article_23627"] = {"revision": row[0], "confirmed": bool(row[1]), "updated_at": row[2]}
    return counts


def create_snapshot(destination: Path) -> dict[str, Any]:
    """Freeze review.db, rule text, and hashes for every preserved Studio code/data file."""
    if destination.exists():
        raise FileExistsError(destination)
    review_db = TOOL_ROOT / "data" / "review.db"
    rule_path = WORKTREE_ROOT / "docs" / "rules_and_plans" / "golden_standard_annotation.md"
    destination.mkdir(parents=True)
    _sqlite_snapshot(review_db, destination / "review.db")
    shutil.copy2(rule_path, destination / "golden_standard_annotation.md")
    file_entries = []
    roots = [TOOL_ROOT / "src", TOOL_ROOT / "scripts", TOOL_ROOT / "tests", TOOL_ROOT / "data"]
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or destination in path.parents or ".pytest" in path.as_posix():
                continue
            file_entries.append({
                "path": path.relative_to(WORKTREE_ROOT).as_posix(), "size": path.stat().st_size,
                "sha256": _sha256(path),
            })
    release_manifest = TOOL_ROOT / "data" / "releases" / "pilot-v0.1.1" / "manifest.json"
    cohort_manifest = TOOL_ROOT / "data" / "cohorts" / "stage-4e-40-v1" / "manifest.json"
    authority_manifest = TOOL_ROOT / "data" / "runs" / "stage-4f-terra-sol-40-manifest-r1.json"
    manifest = {
        "snapshot_version": "gold-standard-paused-v1",
        "paused_at": datetime.now(timezone.utc).isoformat(),
        "status": "paused_preserved_not_migrated",
        "branch": _git("branch", "--show-current"), "head": _git("rev-parse", "HEAD"),
        "studio_version": "0.8.0", "rule_version": "2026-08-12",
        "review_counts": _review_counts(destination / "review.db"),
        "frozen_artifacts": {
            "review.db": _sha256(destination / "review.db"),
            "golden_standard_annotation.md": _sha256(destination / "golden_standard_annotation.md"),
            "pilot-v0.1.1/manifest.json": _sha256(release_manifest),
            "stage-4e-40-v1/manifest.json": _sha256(cohort_manifest),
            "stage-4f-terra-sol-40-manifest-r1.json": _sha256(authority_manifest),
        },
        "preserved_file_count": len(file_entries), "preserved_files": file_entries,
        "resume_policy": "Restore the archived review.db only to resume the old rule version; open a new guideline/release version before applying any new taxonomy.",
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    readme = """# Golden Standard Studio 暂停快照

本目录冻结暂停时的独立复核库与标注规则。原有代码、40 篇候选、人工草稿和 `pilot-v0.1.1` 均保留原位；本快照没有按 storage taxonomy v2 改写任何 Gold。

## 恢复

1. 先对 `manifest.json` 中的文件重新计算 SHA-256；不得在 hash 不一致时静默继续。
2. 停止 Studio 后，备份当前 `data/review.db`，再将本目录 `review.db` 复制回该位置。
3. 使用本目录 `golden_standard_annotation.md` 作为旧规则 `2026-08-12` 的唯一恢复口径。
4. 重新启动 Studio 并检查文章 23627 为 revision 25、未确认；核对队列、Terra/Sol 候选和 `pilot-v0.1.1` manifest。
5. 若要采用新的光储分类口径，必须新建 guideline/release 版本，不得覆盖本快照或旧 Gold。

此目录不包含服务进程状态；恢复前后均由操作者显式控制服务。
"""
    (destination / "README.md").write_text(readme, encoding="utf-8")
    return manifest


def main() -> None:
    """Parse the destination and create one non-overwriting pause snapshot."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = create_snapshot(args.out)
    print(json.dumps({"path": str(args.out), "files": result["preserved_file_count"],
                      "review_counts": result["review_counts"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
