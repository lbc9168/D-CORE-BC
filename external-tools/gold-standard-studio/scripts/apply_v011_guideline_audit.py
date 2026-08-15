"""Apply the user-approved v0.1.1 guideline corrections to the local Studio review database."""

from __future__ import annotations

import copy
import json
import os
import shutil
import sqlite3
import sys
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from gold_standard_studio.app import DATA_DIR
from gold_standard_studio.draft_store import DraftStore, validate_for_confirmation
from gold_standard_studio.pilot_release import sha256_file
from gold_standard_studio.single_article import load_article

TOOL_ROOT = Path(__file__).resolve().parents[1]
REVIEW_DB = DATA_DIR / "review.db"
AUDIT_ROOT = DATA_DIR / "audits"
AUDIT_ID = "pilot-v0.1.1-guideline-audit"
RELEASE_V010 = DATA_DIR / "releases" / "pilot-v0.1.0"

CHANGES: tuple[dict[str, Any], ...] = (
    {
        "rule": "acquirer_is_not_developer",
        "article_id": 1533,
        "project_index": 0,
        "field": "project_developer",
        "expected_state": "present",
        "expected_value": "三一集团",
        "new_state": "present",
        "new_value": "Enero",
        "new_evidence": "罗马尼亚开发商Enero",
    },
    {
        "rule": "acquisition_is_not_financing",
        "article_id": 1533,
        "project_index": 0,
        "field": "financing_mode",
        "expected_state": "present",
        "expected_value": "股权融资",
        "new_state": "not_mentioned",
        "new_value": None,
        "new_evidence": "",
    },
    *(
        {
            "rule": "portfolio_role_does_not_propagate",
            "article_id": 1533,
            "project_index": project_index,
            "field": "entities",
            "expected_state": "present",
            "expected_value": [{"name": "三一硅能", "role": "一体化方案提供商"}],
            "new_state": "present",
            "new_value": [{"name": "三一硅能", "role": None}],
            "new_evidence": evidence,
        }
        for project_index, evidence in (
            (1, "三一硅能在海外“光伏+储能+柴发”微电网发电领域的首次尝试"),
            (2, "三一硅能凭借集团积累的矿业业主资源"),
            (3, "三一硅能凭借集团积累的矿业业主资源"),
            (4, "三一硅能复制赞比亚的经验，并且已经初显成效"),
            (5, "三一硅能复制赞比亚的经验，并且已经初显成效"),
        )
    ),
    {
        "rule": "acquirer_is_not_developer",
        "article_id": 1533,
        "project_index": 7,
        "field": "project_developer",
        "expected_state": "present",
        "expected_value": "三一重能",
        "new_state": "not_mentioned",
        "new_value": None,
        "new_evidence": "",
    },
    {
        "rule": "candidate_site_not_in_project_name",
        "article_id": 1586,
        "project_index": 0,
        "field": "project_name",
        "expected_state": "present",
        "expected_value": "英国苏格兰阿德赛尔港风电机组制造基地项目",
        "new_state": "present",
        "new_value": "英国苏格兰风电机组制造基地项目",
        "new_evidence": "在苏格兰建设英国最大、也是首个全产业链一体化风电机组制造基地",
    },
)


def _json_bytes(value: Any) -> bytes:
    """Serialize one deterministic UTF-8 JSON artifact."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _hash_tree(root: Path) -> dict[str, str]:
    """Hash every immutable release artifact below one directory."""
    return {path.relative_to(root).as_posix(): sha256_file(path) for path in sorted(root.rglob("*")) if path.is_file()}


def _restore_database(backup: Path, target: Path) -> None:
    """Restore the exact pre-audit SQLite snapshot after a failed multi-article update."""
    with closing(sqlite3.connect(backup)) as source, closing(sqlite3.connect(target)) as destination:
        source.backup(destination)


def _build_payloads(store: DraftStore) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]], dict[int, int]]:
    """Build and prevalidate all corrected payloads before making the first database change."""
    payloads: dict[int, dict[str, Any]] = {}
    revisions: dict[int, int] = {}
    audit_rows: list[dict[str, Any]] = []
    for article_id in sorted({int(item["article_id"]) for item in CHANGES}):
        draft = store.get_draft(article_id)
        if draft is None or not draft["confirmed"]:
            raise ValueError(f"article {article_id} 必须处于 confirmed 状态")
        payloads[article_id] = copy.deepcopy(draft["payload"])
        revisions[article_id] = int(draft["revision"])
    for change in CHANGES:
        article_id = int(change["article_id"])
        project = payloads[article_id]["projects"][int(change["project_index"])]
        field = project["fields"][change["field"]]
        if field.get("value_state") != change["expected_state"] or field.get("value") != change["expected_value"]:
            raise ValueError(
                f"审计前置值不匹配：article={article_id}, project={change['project_index'] + 1}, "
                f"field={change['field']}"
            )
        before = copy.deepcopy(field)
        field.update({
            "value_state": change["new_state"],
            "value": copy.deepcopy(change["new_value"]),
            "evidence": change["new_evidence"],
            "review_action": "modified",
            "accepted_source": "human_guideline_audit",
            "confidence": None,
        })
        if change["field"] == "project_name":
            project["project_name"] = change["new_value"]
        audit_rows.append({
            "rule": change["rule"], "article_id": article_id,
            "project_index": int(change["project_index"]) + 1,
            "gold_project_id": project.get("gold_project_id"), "field": change["field"],
            "before": before, "after": copy.deepcopy(field),
        })
    for article_id, payload in payloads.items():
        content = load_article(article_id)["article"]["content"]
        errors = validate_for_confirmation(payload, content)
        if errors:
            raise ValueError(f"article {article_id} 修订后确认校验失败：{'；'.join(errors)}")
    return payloads, audit_rows, revisions


def apply_audit() -> Path:
    """Back up, apply, strictly reconfirm, and atomically record the approved Gold corrections."""
    final_dir = AUDIT_ROOT / AUDIT_ID
    if final_dir.exists():
        raise FileExistsError(f"审计目录已经存在，禁止重复应用：{final_dir}")
    if not RELEASE_V010.is_dir():
        raise FileNotFoundError(f"缺少不可变基线 release：{RELEASE_V010}")
    store = DraftStore(REVIEW_DB)
    payloads, audit_rows, revisions_before = _build_payloads(store)
    temporary = AUDIT_ROOT / f".{AUDIT_ID}.tmp-{uuid.uuid4().hex}"
    temporary.mkdir(parents=True)
    backup = temporary / "review-before.db"
    with closing(sqlite3.connect(REVIEW_DB)) as source, closing(sqlite3.connect(backup)) as destination:
        source.backup(destination)
    review_hash_before = sha256_file(REVIEW_DB)
    release_hashes_before = _hash_tree(RELEASE_V010)
    results: dict[int, dict[str, Any]] = {}
    try:
        for article_id in sorted(payloads):
            store.reopen(article_id)
            saved = store.save_draft(article_id, payloads[article_id])
            confirmed = store.confirm(article_id)
            results[article_id] = {"saved": saved, "confirmed": confirmed}
        report = {
            "schema_version": "gold-guideline-audit-v1",
            "target_release": "pilot-v0.1.1",
            "source_release": "pilot-v0.1.0",
            "source_release_hashes": release_hashes_before,
            "review_db_sha256_before": review_hash_before,
            "review_db_sha256_after": sha256_file(REVIEW_DB),
            "revisions_before": revisions_before,
            "revisions_after": {str(article_id): result["confirmed"]["revision"] for article_id, result in results.items()},
            "changes": audit_rows,
            "kept_after_audit": [{
                "article_id": 1533, "project_index": 7, "gold_project_id": "P07",
                "field": "project_stage", "value": "在建",
                "rule": "participation_in_construction_supports_under_construction",
                "evidence": "三一硅能参建达尔汗5MW+4MWh光储电站",
            }],
        }
        (temporary / "audit-report.json").write_bytes(_json_bytes(report))
        artifacts = {
            "audit-report.json": sha256_file(temporary / "audit-report.json"),
            "review-before.db": sha256_file(backup),
        }
        manifest = {"schema_version": "gold-guideline-audit-v1", "audit_id": AUDIT_ID,
                    "immutable": True, "artifacts": artifacts}
        (temporary / "audit-manifest.json").write_bytes(_json_bytes(manifest))
        if _hash_tree(RELEASE_V010) != release_hashes_before:
            raise ValueError("pilot-v0.1.0 在审计过程中发生变化")
        AUDIT_ROOT.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, final_dir)
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError):
        _restore_database(backup, REVIEW_DB)
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return final_dir


def main() -> int:
    """Apply the fixed audit once and emit its immutable artifact directory."""
    try:
        path = apply_audit()
        print(json.dumps({"ok": True, "path": str(path)}, ensure_ascii=False, indent=2))
        return 0
    except (FileNotFoundError, FileExistsError, ValueError, OSError, sqlite3.Error) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
