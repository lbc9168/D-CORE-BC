"""Verify fail-closed, immutable Pilot Gold publication and layered projections."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from gold_standard_studio.draft_store import DraftStore, REQUIRED_FIELDS
from gold_standard_studio.pilot_release import check_release_readiness, publish_release, sha256_file


def _field(value: Any = None, state: str = "not_mentioned", evidence: str = "") -> dict[str, Any]:
    """Build one fully reviewed field envelope for a release test fixture."""
    return {"value": value, "value_state": state, "review_action": "confirmed", "evidence": evidence}


def _payload() -> dict[str, Any]:
    """Build one complete confirmed valid-article payload across fact and derived layers."""
    fields = {name: _field() for name in REQUIRED_FIELDS}
    fields["project_name"] = _field("Alpha 项目", "present", "Alpha 项目")
    fields["category"] = _field("光伏", "present", "光伏")
    fields["country"] = _field("测试国", "present", "测试国")
    fields["location"] = _field("测试地", "present", "测试地")
    fields["generation_sources"] = _field(["光伏"], "present", "光伏")
    fields["entities"] = _field([{"name": "中企甲", "role": "开发商"}], "present", "中企甲")
    fields["normalized_location"] = _field("测试地", "present")
    fields["normalized_entities"] = _field(
        [{"mentioned_name": "中企甲", "canonical_entity": "中企甲", "parent_company": "中企集团", "role": "开发商"}],
        "present",
    )
    return {
        "article_status": "valid", "non_project_reason": "", "projects": [
            {"gold_project_id": "gold-1", "project_name": "Alpha 项目", "relation_note": "独立项目", "fields": fields}
        ]
    }


def _seed(tmp_path: Path, confirmed: bool = True) -> tuple[Path, Any]:
    """Create an isolated Studio review DB and matching immutable article loader."""
    review_db = tmp_path / "review.db"
    store = DraftStore(review_db)
    content = "中企甲在测试国测试地建设Alpha 项目光伏设施。"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    article = {"id": 1, "title": "测试文章", "url": "https://example.test/1", "content": content,
               "publish_date": "2026-08-10", "article_sha256": digest}
    store.seed_article(1, digest, {"article": article})
    store.save_draft(1, _payload())
    if confirmed:
        store.confirm(1)

    values = {name: envelope["value"] for name, envelope in _payload()["projects"][0]["fields"].items()}

    def loader(article_id: int) -> dict[str, Any]:
        """Return source, S01B, and database fixtures aligned exactly with the Gold payload."""
        assert article_id == 1
        database = {**values, "entities": [{"subsidiary_name": "中企甲", "implementing_entity": "中企集团",
                                              "involvement_type": "开发商"}], "location_norm": "测试地"}
        return {"article": article, "historical_extraction_projects": [values],
                "database_projects": [database], "source_paths": {}}

    return review_db, loader


def _load_gold_loader(worktree: Path):
    """Load the repository's strict legacy Gold loader directly from its source file."""
    path = worktree / "eval" / "gold_loader.py"
    spec = importlib.util.spec_from_file_location("pilot_test_gold_loader", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unconfirmed_article_blocks_release_without_partial_directory(tmp_path: Path) -> None:
    """An unconfirmed draft must report its blocker and leave no version directory behind."""
    review_db, loader = _seed(tmp_path, confirmed=False)
    report = check_release_readiness(review_db, [1], loader)
    assert report["ready"] is False
    assert "文章尚未确认" in report["articles"][0]["blockers"]
    with pytest.raises(ValueError, match="尚未满足发布条件"):
        publish_release("pilot-v0.1.0", review_db, tmp_path / "releases", [1], loader)
    assert not (tmp_path / "releases" / "pilot-v0.1.0").exists()


def test_publish_is_immutable_hashed_and_legacy_loader_compatible(tmp_path: Path) -> None:
    """A ready review publishes all layers once with verified hashes and legacy eval compatibility."""
    review_db, loader = _seed(tmp_path)
    release = publish_release("pilot-v0.1.0", review_db, tmp_path / "releases", [1], loader)
    manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["article_ids"] == [1]
    assert manifest["immutable"] is True
    for relative, expected_hash in manifest["artifacts"].items():
        assert sha256_file(release / relative) == expected_hash
    baseline = json.loads((release / "baseline" / "stored-pipeline-baseline.json").read_text(encoding="utf-8"))
    assert baseline["s01b"]["projects"]["f1"] == 1.0
    assert baseline["s02a"]["projects"]["f1"] == 1.0

    worktree = Path(__file__).resolve().parents[3]
    records = _load_gold_loader(worktree).load(release / "projections" / "s01b-eval.jsonl")
    assert records[0]["projects"][0]["project_name"] == "Alpha 项目"

    before = {path.relative_to(release): path.read_bytes() for path in release.rglob("*") if path.is_file()}
    with pytest.raises(FileExistsError, match="禁止覆盖"):
        publish_release("pilot-v0.1.0", review_db, tmp_path / "releases", [1], loader)
    assert before == {path.relative_to(release): path.read_bytes() for path in release.rglob("*") if path.is_file()}
