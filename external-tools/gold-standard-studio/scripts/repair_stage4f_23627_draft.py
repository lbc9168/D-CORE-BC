"""Safely replace only article 23627's unchanged unconfirmed draft after its audited rerun."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from gold_standard_studio.app import _initial_draft, _read_candidate_artifacts, store
from gold_standard_studio.candidate_contract import validate_candidate_file
from gold_standard_studio.single_article import load_article

ARTICLE_ID = 23627
TOOL_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = TOOL_ROOT / "data"
AUDIT_DIR = DATA_DIR / "audits" / "stage4f-23627-rerun-20260813"
BACKUP_DB = AUDIT_DIR / "review-before.db"
CURRENT_DB = DATA_DIR / "review.db"
PROVIDER_PATH = DATA_DIR / "candidates" / "terra-provider-23627.json"
REVIEWER_PATH = DATA_DIR / "candidates" / "sol-reviewer-23627.json"


def _draft_row(path: Path) -> dict[str, Any]:
    """Read the exact article draft row from one Studio review database."""
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT article_id,revision,payload_json,confirmed,updated_at FROM drafts WHERE article_id=?",
            (ARTICLE_ID,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError(f"数据库没有文章 {ARTICLE_ID} 草稿：{path}")
    return dict(row)


def _sha256(path: Path) -> str:
    """Hash one repair input or output artifact for the audit manifest."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_repair_preconditions() -> dict[str, Any]:
    """Fail closed unless the draft is unchanged and the rerun candidate pair is valid."""
    before = _draft_row(BACKUP_DB)
    current = _draft_row(CURRENT_DB)
    comparable = ("revision", "payload_json", "confirmed", "updated_at")
    if any(current[key] != before[key] for key in comparable):
        raise ValueError("23627 草稿在备份后发生变化，拒绝自动重置")
    if current["confirmed"]:
        raise ValueError("23627 已确认，拒绝自动重置")
    provider = validate_candidate_file(PROVIDER_PATH, "provider")
    reviewer = validate_candidate_file(REVIEWER_PATH, "reviewer", PROVIDER_PATH)
    if provider["article_status"]["value"] != "valid_project" or not provider["projects"]:
        raise ValueError("23627 重跑 Provider 未产生 valid_project 项目，拒绝重置草稿")
    return {"before": before, "provider": provider, "reviewer": reviewer}


def apply_repair() -> dict[str, Any]:
    """Reset the unchanged draft to the corrected Provider prefill and persist an audit record."""
    manifest_path = AUDIT_DIR / "repair-manifest.json"
    if manifest_path.exists():
        raise FileExistsError(f"拒绝覆盖修复审计：{manifest_path}")
    checked = check_repair_preconditions()
    article = load_article(ARTICLE_ID)["article"]
    artifacts = _read_candidate_artifacts(ARTICLE_ID, article["article_sha256"])
    roles = {artifact["source"] for artifact in artifacts}
    if roles != {"provider", "reviewer"}:
        raise ValueError(f"23627 网页候选链不完整：{sorted(roles)}")
    context = load_article(ARTICLE_ID)
    replacement = _initial_draft(context, artifacts)
    result = store.save_draft(ARTICLE_ID, replacement)
    after = store.get_draft(ARTICLE_ID)
    if after is None or after["revision"] != checked["before"]["revision"] + 1:
        raise RuntimeError("23627 草稿 revision 未按预期递增")
    before_payload = json.loads(checked["before"]["payload_json"])
    (AUDIT_DIR / "draft-before.json").write_text(
        json.dumps(before_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="",
    )
    (AUDIT_DIR / "draft-after.json").write_text(
        json.dumps(after["payload"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="",
    )
    manifest = {
        "repair": "stage4f-23627-s1-final-consistency-rerun",
        "article_id": ARTICLE_ID,
        "article_sha256": article["article_sha256"],
        "draft_revision_before": checked["before"]["revision"],
        "draft_revision_after": after["revision"],
        "provider_candidate_id": checked["provider"]["candidate_id"],
        "reviewer_candidate_id": checked["reviewer"]["candidate_id"],
        "provider_sha256": _sha256(PROVIDER_PATH),
        "reviewer_sha256": _sha256(REVIEWER_PATH),
        "review_db_backup_sha256": _sha256(BACKUP_DB),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="",
    )
    return manifest


def apply_entity_schema_upgrade() -> dict[str, Any]:
    """Regenerate only the untouched repair draft after enabling entity/name envelope compatibility."""
    repair_manifest_path = AUDIT_DIR / "repair-manifest.json"
    upgrade_manifest_path = AUDIT_DIR / "entity-schema-upgrade-manifest.json"
    if not repair_manifest_path.is_file():
        raise FileNotFoundError("缺少已完成的 23627 repair manifest")
    if upgrade_manifest_path.exists():
        raise FileExistsError(f"拒绝覆盖实体 schema 升级审计：{upgrade_manifest_path}")
    repair_manifest = json.loads(repair_manifest_path.read_text(encoding="utf-8"))
    current = _draft_row(CURRENT_DB)
    if current["confirmed"] or current["revision"] != repair_manifest["draft_revision_after"]:
        raise ValueError("23627 修复草稿已被编辑或确认，拒绝自动升级实体 schema")
    current_payload = json.loads(current["payload_json"])
    fields = [
        field for project in current_payload.get("projects", [])
        for field in (project.get("fields") or {}).values()
    ]
    if any(field.get("review_action") != "unreviewed" for field in fields):
        raise ValueError("23627 修复草稿已含人工 field action，拒绝自动升级实体 schema")
    article = load_article(ARTICLE_ID)["article"]
    artifacts = _read_candidate_artifacts(ARTICLE_ID, article["article_sha256"])
    replacement = _initial_draft(load_article(ARTICLE_ID), artifacts)
    entity_field = replacement["projects"][0]["fields"]["entities"]
    if entity_field.get("value_state") != "present" or not entity_field.get("evidence"):
        raise ValueError("兼容适配后仍未保留中国实体及原文证据")
    result = store.save_draft(ARTICLE_ID, replacement)
    after = store.get_draft(ARTICLE_ID)
    if after is None or after["revision"] != current["revision"] + 1:
        raise RuntimeError("实体 schema 升级 revision 未按预期递增")
    (AUDIT_DIR / "draft-after-entity-schema-upgrade.json").write_text(
        json.dumps(after["payload"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="",
    )
    manifest = {
        "repair": "stage4f-23627-entity-name-envelope-upgrade",
        "article_id": ARTICLE_ID,
        "draft_revision_before": current["revision"],
        "draft_revision_after": result["revision"],
        "entities_value": entity_field["value"],
        "entities_evidence": entity_field["evidence"],
    }
    upgrade_manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="",
    )
    return manifest


def main() -> None:
    """Expose check and one-time apply commands for the audited article repair."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "apply", "upgrade-entity-schema"))
    args = parser.parse_args()
    if args.command == "check":
        result = check_repair_preconditions()
    elif args.command == "apply":
        result = apply_repair()
    else:
        result = apply_entity_schema_upgrade()
    summary = result if args.command != "check" else {
        "ok": True,
        "article_id": ARTICLE_ID,
        "draft_revision": result["before"]["revision"],
        "provider_candidate_id": result["provider"]["candidate_id"],
        "reviewer_candidate_id": result["reviewer"]["candidate_id"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
