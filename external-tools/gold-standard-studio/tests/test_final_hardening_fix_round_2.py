"""Reproduce and lock the second final-hardening review findings at public security boundaries."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest
from typer.testing import CliRunner

import gold_standard_studio.config as config_module
import gold_standard_studio.review_store as review_store_module
from gold_standard_studio.exporter import publish_gold
from gold_standard_studio.review_store import ReviewStore
from gold_standard_studio.schemas import SamplingProvenance

from test_final_hardening import _complete_project, _snapshot, _source_files
from test_final_hardening_fix_round import (
    _create_data_bearing_v1,
    _seed_valid,
    _source_repository,
)


def test_backup_secures_destination_before_sqlite_writes_and_cleans_swapped_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Abort when the backup temporary path becomes a source hardlink before destination SQLite open."""
    sources = _source_files(tmp_path)
    store = ReviewStore(tmp_path / "review.db", source_paths=sources)
    store.initialize()
    destination = tmp_path / "backup.db"
    displaced = tmp_path / "claimed-empty-backup.db"
    before = hashlib.sha256(sources[0].read_bytes()).hexdigest()

    real_connect = sqlite3.connect
    swapped = False

    def swap_backup_before_open(database: object, *args: object, **kwargs: object):
        """Replace only the selected backup temporary immediately before SQLite opens it."""
        nonlocal swapped
        path = Path(database) if isinstance(database, (str, Path)) else None
        if (
            path is not None
            and path.name.startswith(".backup.db.")
            and path.name.endswith(".tmp")
            and not swapped
        ):
            if path.exists():
                os.replace(path, displaced)
            os.link(sources[0], path)
            swapped = True
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(review_store_module.sqlite3, "connect", swap_backup_before_open)

    with pytest.raises((ValueError, PermissionError)):
        store.backup(destination)

    assert hashlib.sha256(sources[0].read_bytes()).hexdigest() == before
    assert not destination.exists()
    assert list(tmp_path.glob(".backup.db.*.tmp")) == []


@pytest.mark.parametrize(
    "artifact_name",
    [
        "rich_gold.jsonl",
        "eval_projection.jsonl",
        "rich_gold.schema.json",
        "distribution.json",
        "CHANGELOG.md",
        "manifest.json",
    ],
)
@pytest.mark.parametrize("link_kind", ["hardlink", "symlink"])
def test_publish_secures_every_staged_artifact_against_source_alias_races(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_name: str,
    link_kind: str,
) -> None:
    """Reject a source alias introduced at any staged artifact path before its atomic commit."""
    sources = _source_files(tmp_path)
    snapshot = _snapshot()
    store = ReviewStore(tmp_path / "review.db", source_paths=sources)
    store.initialize()
    _seed_valid(store, snapshot)
    store.confirm_article("S001", actor="human")
    before = hashlib.sha256(sources[0].read_bytes()).hexdigest()
    swapped = False

    def alias_artifact_before_replace(path: Path) -> None:
        """Install exactly one forbidden source alias at the selected staged artifact destination."""
        nonlocal swapped
        if path.name != artifact_name or swapped:
            return
        try:
            if link_kind == "hardlink":
                os.link(sources[0], path)
            else:
                os.symlink(sources[0], path)
        except OSError as error:
            if link_kind == "symlink":
                pytest.skip(f"symbolic links unavailable: {error}")
            raise
        swapped = True

    monkeypatch.setattr(config_module, "_before_atomic_replace", alias_artifact_before_replace)
    output = tmp_path / "gold"

    with pytest.raises((ValueError, OSError), match="source database|changed during atomic write"):
        publish_gold(store, output, "v1.0.0")

    assert hashlib.sha256(sources[0].read_bytes()).hexdigest() == before
    assert not (output / "v1.0.0").exists()
    assert list(output.glob(".v1.0.0.*.tmp")) == []


@pytest.mark.parametrize(
    "legacy_sampling",
    [
        None,
        {
            "seed": "not-an-integer",
            "inclusion_reasons": "not-a-list",
            "category_tags": ["光伏", 7],
            "risk_tags": {"bad": "mapping"},
            "negative_proxy": "false",
        },
    ],
    ids=["missing", "invalid"],
)
def test_migration_loads_missing_or_invalid_sampling_as_explicit_unverified_provenance(
    tmp_path: Path, legacy_sampling: object
) -> None:
    """Keep malformed legacy provenance loadable but explicitly unverified and ineligible for confirmation."""
    snapshot = _snapshot()
    path = tmp_path / "legacy.db"
    _create_data_bearing_v1(
        path,
        [("LEGACY", snapshot, [_complete_project(snapshot)], "valid")],
    )
    with sqlite3.connect(path) as conn:
        payload = json.loads(
            conn.execute(
                "SELECT payload_json FROM article_drafts WHERE sample_id='LEGACY'"
            ).fetchone()[0]
        )
        if legacy_sampling is None:
            payload["audit_metadata"].pop("sampling", None)
        else:
            payload["audit_metadata"]["sampling"] = legacy_sampling
        conn.execute(
            "UPDATE article_drafts SET payload_json=? WHERE sample_id='LEGACY'",
            (json.dumps(payload, ensure_ascii=False),),
        )
    store = ReviewStore(path)

    store.initialize()

    migrated = store.get_article("LEGACY")
    assert migrated.sampling_provenance is not None
    assert migrated.sampling_provenance.verified is False
    assert migrated.sampling_provenance.verification_source == "legacy_unknown"
    assert "sampling_provenance_repair_required" in [
        entry.action for entry in store.list_audit("LEGACY")
    ]
    store.attach_article_snapshot("LEGACY", snapshot, actor="recovery")
    store.save_article_status("LEGACY", "valid", actor="reviewer")
    with pytest.raises(ValueError, match="verified sampling provenance|verify-provenance"):
        store.confirm_article("LEGACY", actor="reviewer")


def test_operator_replaces_unverified_provenance_with_audited_verified_data_then_publishes(
    tmp_path: Path,
) -> None:
    """Allow publication only after an operator replaces legacy provenance through an audited store decision."""
    snapshot = _snapshot()
    path = tmp_path / "legacy.db"
    _create_data_bearing_v1(
        path,
        [("LEGACY", snapshot, [_complete_project(snapshot)], "valid")],
    )
    store = ReviewStore(path)
    store.initialize()
    store.attach_article_snapshot("LEGACY", snapshot, actor="recovery")
    provenance = SamplingProvenance(
        sampling_seed=73,
        inclusion_reasons=["operator_reconstructed_manifest"],
        original_category_strata=["光伏"],
        risk_tags=["legacy_recovery"],
        negative_proxy=False,
        verified=True,
        verification_source="operator_review",
    )

    store.replace_sampling_provenance("LEGACY", provenance, actor="reviewer")
    store.save_article_status("LEGACY", "valid", actor="reviewer")
    store.confirm_article("LEGACY", actor="reviewer")
    paths = publish_gold(store, tmp_path / "gold", "v2.0.0")

    assert store.list_audit("LEGACY")[-3].action == "sampling_provenance_decision"
    rich = json.loads(paths.rich_gold.read_text(encoding="utf-8").splitlines()[0])
    assert rich["sampling_provenance"] == provenance.model_dump(mode="json")


def test_cli_verify_provenance_repairs_migrated_record_with_operator_data(tmp_path: Path) -> None:
    """Expose the audited legacy-provenance repair through the configured public CLI."""
    from gold_standard_studio.cli import app

    snapshot = _snapshot()
    config, repository = _source_repository(tmp_path, [snapshot])
    repository.close()
    runner = CliRunner()
    initialized = runner.invoke(
        app,
        [
            "init",
            "--raw-news-db",
            str(config.raw_news_db),
            "--projects-db",
            str(config.projects_db),
            "--main-db",
            str(config.main_db),
            "--data-dir",
            str(config.data_dir),
        ],
    )
    assert initialized.exit_code == 0, initialized.output
    (config.data_dir / "review.db").unlink()
    _create_data_bearing_v1(
        config.data_dir / "review.db",
        [("LEGACY", snapshot, [_complete_project(snapshot)], "valid")],
    )
    assert runner.invoke(
        app,
        [
            "rehydrate-snapshots",
            "--sample-id",
            "LEGACY",
            "--data-dir",
            str(config.data_dir),
        ],
    ).exit_code == 0

    repaired = runner.invoke(
        app,
        [
            "verify-provenance",
            "--sample-id",
            "LEGACY",
            "--sampling-seed",
            "73",
            "--inclusion-reason",
            "operator_reconstructed_manifest",
            "--category-stratum",
            "光伏",
            "--risk-tag",
            "legacy_recovery",
            "--no-negative-proxy",
            "--data-dir",
            str(config.data_dir),
        ],
    )

    assert repaired.exit_code == 0, repaired.output
    reopened = ReviewStore(config.data_dir / "review.db", source_paths=config.source_paths)
    provenance = reopened.get_article("LEGACY").sampling_provenance
    assert provenance is not None and provenance.verified is True
    assert provenance.verification_source == "operator_review"
    assert reopened.list_audit("LEGACY")[-1].action == "sampling_provenance_decision"
