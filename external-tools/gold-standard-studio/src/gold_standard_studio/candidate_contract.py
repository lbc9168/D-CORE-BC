"""Validate Stage 4E Terra Provider and Sol Reviewer artifacts before web import."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .review_cohorts import REVIEW_BY_ID
from .single_article import load_article
from .stage4e_manifest import stage4e_article_ids

TOOL_ROOT = Path(__file__).resolve().parents[2]
CANDIDATE_DIR = TOOL_ROOT / "data" / "candidates"
DEFAULT_RUN_MANIFEST = TOOL_ROOT / "data" / "runs" / "stage-4f-terra-sol-40-manifest.json"
EXPECTED_MODELS = {"provider": "gpt-5.6-terra", "reviewer": "gpt-5.6-sol"}
VALID_ARTICLE_STATUSES = {
    "valid_project", "no_chinese_participation", "chinese_unnamed_participation",
    "irrelevant", "other_non_project",
}
REQUIRED_PROJECT_FIELDS = {
    "project_name", "category", "subcategory_raw_fact", "generation_sources",
    "storage_status", "storage_power_mw", "storage_energy_mwh", "generation_capacity_mw",
    "project_stage", "country", "location", "investment", "financing_mode",
    "project_developer", "chinese_entities",
}


def _unwrap_value(value: Any) -> Any:
    """Read an envelope value while accepting plain scalars only for structural diagnostics."""
    return value.get("value") if isinstance(value, dict) else value


def _evidence_supported(evidence: str, sources: str | list[str]) -> bool:
    """Accept exact title/body quotes or a delimiter-joined list of exact snippets."""
    source_list = [sources] if isinstance(sources, str) else sources
    if any(evidence in source for source in source_list):
        return True
    fragments = [part.strip(" 。；;，,：:\n") for part in re.split(r"[；;。\n]", evidence)]
    nonempty = [fragment for fragment in fragments if fragment]
    return bool(nonempty) and all(
        any(fragment in source for source in source_list) for fragment in nonempty
    )


def _walk_field_contracts(value: Any, path: str = "$") -> list[tuple[str, str]]:
    """Validate candidate field states while requiring evidence only for actually present facts."""
    errors: list[tuple[str, str]] = []
    if isinstance(value, dict):
        if "value_state" in value and "value" in value:
            state = value.get("value_state")
            field_value = value.get("value")
            allowed = {
                "present", "extracted", "explicit", "derived_from_explicit",
                "not_mentioned", "not_applicable", "uncertain", "unknown", "needs_adjudication",
            }
            if state not in allowed:
                errors.append((path, f"非法 value_state：{state}"))
            is_absent = state in {"not_mentioned", "not_applicable", "uncertain", "unknown", "needs_adjudication"}
            is_absent = is_absent or field_value in (None, "", "not_mentioned", "Unknown")
            if not is_absent and not str(value.get("evidence") or "").strip():
                errors.append((path, "present 事实缺少原文证据"))
        for key, child in value.items():
            errors.extend(_walk_field_contracts(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(_walk_field_contracts(child, f"{path}[{index}]"))
    return errors


def _walk_evidence(value: Any, path: str = "$") -> list[tuple[str, str]]:
    """Collect every non-empty evidence string with its JSON path for source validation."""
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key == "evidence" and isinstance(child, str) and child.strip():
                found.append((child_path, child.strip()))
            else:
                found.extend(_walk_evidence(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_walk_evidence(child, f"{path}[{index}]"))
    return found


def validate_candidate_payload(payload: dict[str, Any], role: str,
                               parent: dict[str, Any] | None = None) -> list[str]:
    """Return fail-closed provenance, structure, and evidence errors for one candidate payload."""
    errors: list[str] = []
    article_id = payload.get("article_id")
    if role not in EXPECTED_MODELS:
        return [f"未知 role：{role}"]
    if article_id not in REVIEW_BY_ID:
        return [f"article_id 不在复核 cohort：{article_id}"]
    article = load_article(article_id)["article"]
    if payload.get("execution_role") != role:
        errors.append(f"execution_role 应为 {role}")
    if payload.get("model") != EXPECTED_MODELS[role]:
        errors.append(f"model 应为 {EXPECTED_MODELS[role]}")
    if payload.get("article_sha256") != article["article_sha256"]:
        errors.append("article_sha256 与固定原文不匹配")
    if not str(payload.get("candidate_id") or "").strip():
        errors.append("缺少 candidate_id")
    status = _unwrap_value(payload.get("article_status"))
    if status not in VALID_ARTICLE_STATUSES:
        errors.append(f"article_status 非法：{status}")
    projects = payload.get("projects")
    if not isinstance(projects, list):
        errors.append("projects 必须是数组")
        projects = []
    if status == "valid_project" and not projects:
        errors.append("valid_project 至少需要一个项目")
    if status != "valid_project" and projects:
        errors.append("非 valid_project 的 projects 必须为空")
    if not isinstance(payload.get("prompt_manifest"), dict):
        errors.append("缺少 prompt_manifest，无法证明固定 Prompt provenance")
    if role == "provider":
        stages = payload.get("stage_outputs")
        if not isinstance(stages, dict) or "s1" not in stages:
            errors.append("Provider 缺少 S01B-S1 stage output")
        else:
            s1_status = _unwrap_value(stages.get("s1", {}).get("status"))
            if s1_status != status:
                errors.append(
                    f"Provider 最终 article_status={status} 与 S01B-S1 status={s1_status} 不一致"
                )
        if isinstance(stages, dict) and status == "valid_project":
            missing_stages = [name for name in ("s2", "s34", "s5") if name not in stages]
            if missing_stages:
                errors.append(f"Provider 缺少 production stage output：{', '.join(missing_stages)}")
            s2_projects = (stages.get("s2") or {}).get("projects") or []
            in_scope_projects = [
                project for project in s2_projects
                if isinstance(project, dict) and project.get("in_scope") is not False
            ]
            if len(in_scope_projects) != len(projects):
                errors.append("Provider S02A-S2 in-scope 项目数必须与最终项目数一致")
            for name in ("s34", "s5"):
                outputs = stages.get(name)
                if not isinstance(outputs, list) or len(outputs) != len(projects):
                    errors.append(f"Provider {name} 输出数量必须与项目数一致")
    for index, project in enumerate(projects, start=1):
        if not isinstance(project, dict):
            errors.append(f"项目 {index} 必须是 object")
            continue
        missing = sorted(REQUIRED_PROJECT_FIELDS - set(project))
        if missing:
            errors.append(f"项目 {index} 缺少字段：{', '.join(missing)}")
        entities = project.get("chinese_entities")
        if not isinstance(entities, list):
            errors.append(f"项目 {index} chinese_entities 必须是数组")
        else:
            for entity_index, entity in enumerate(entities, start=1):
                if not isinstance(entity, dict) or not isinstance(
                    entity.get("entity") or entity.get("name") or entity.get("entity_name"), dict
                ):
                    errors.append(
                        f"项目 {index} 中国实体 {entity_index} 缺少 entity/name/entity_name envelope"
                    )
                if not isinstance(entity, dict) or not isinstance(entity.get("role"), dict):
                    errors.append(f"项目 {index} 中国实体 {entity_index} 缺少 role envelope")
    article_sources = [str(article.get("title") or ""), str(article.get("content") or "")]
    for path, evidence in _walk_evidence(payload):
        if not _evidence_supported(evidence, article_sources):
            errors.append(f"证据无法定位：{path}")
    for path, message in _walk_field_contracts(payload):
        errors.append(f"字段合同错误：{path} {message}")
    if role == "reviewer":
        if parent is None:
            errors.append("Reviewer 缺少 Provider 父候选")
        else:
            parent_errors = validate_candidate_payload(parent, "provider")
            if parent_errors:
                errors.append("Reviewer 的 Provider 父候选未通过合同：" + " | ".join(parent_errors))
            if payload.get("parent_candidate_id") != parent.get("candidate_id"):
                errors.append("parent_candidate_id 与 Provider 不匹配")
            if parent.get("article_id") != article_id or parent.get("article_sha256") != payload.get("article_sha256"):
                errors.append("Reviewer 与 Provider 的文章 provenance 不匹配")
    return errors


def validate_candidate_file(path: Path, role: str, parent_path: Path | None = None) -> dict[str, Any]:
    """Load and validate one candidate file, raising one actionable error on failure."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    parent = json.loads(parent_path.read_text(encoding="utf-8")) if parent_path else None
    errors = validate_candidate_payload(payload, role, parent)
    if errors:
        raise ValueError("；".join(errors))
    return payload


def stage4e_candidate_status(candidate_dir: Path = CANDIDATE_DIR) -> dict[str, Any]:
    """Report complete and missing Terra/Sol artifacts for the fixed forty-article cohort."""
    provider_ready: list[int] = []
    reviewer_ready: list[int] = []
    invalid: dict[str, str] = {}
    for article_id in stage4e_article_ids():
        provider_path = candidate_dir / f"terra-provider-{article_id}.json"
        reviewer_path = candidate_dir / f"sol-reviewer-{article_id}.json"
        if provider_path.is_file():
            try:
                validate_candidate_file(provider_path, "provider")
                provider_ready.append(article_id)
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                invalid[provider_path.name] = str(exc)
        if reviewer_path.is_file():
            try:
                validate_candidate_file(reviewer_path, "reviewer", provider_path)
                reviewer_ready.append(article_id)
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                invalid[reviewer_path.name] = str(exc)
    ids = stage4e_article_ids()
    return {
        "total": len(ids),
        "provider_ready": provider_ready,
        "provider_missing": [article_id for article_id in ids if article_id not in provider_ready],
        "reviewer_ready": reviewer_ready,
        "reviewer_missing": [article_id for article_id in ids if article_id not in reviewer_ready],
        "invalid": invalid,
    }


def _sha256_file(path: Path) -> str:
    """Hash one immutable candidate or rendered-prompt artifact."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_stage4f_run_manifest(output: Path = DEFAULT_RUN_MANIFEST,
                               candidate_dir: Path = CANDIDATE_DIR) -> dict[str, Any]:
    """Publish one non-overwritable manifest only after all forty Terra/Sol pairs validate."""
    if output.exists():
        raise FileExistsError(f"拒绝覆盖既有 Stage 4F run manifest：{output}")
    status = stage4e_candidate_status(candidate_dir)
    if status["provider_missing"] or status["reviewer_missing"] or status["invalid"]:
        raise ValueError("Stage 4F 候选尚未全部通过校验")
    records = []
    for article_id in stage4e_article_ids():
        provider_path = candidate_dir / f"terra-provider-{article_id}.json"
        reviewer_path = candidate_dir / f"sol-reviewer-{article_id}.json"
        provider = validate_candidate_file(provider_path, "provider")
        reviewer = validate_candidate_file(reviewer_path, "reviewer", provider_path)
        records.append({
            "article_id": article_id,
            "article_sha256": provider["article_sha256"],
            "cohort": REVIEW_BY_ID[article_id]["cohort"],
            "split": REVIEW_BY_ID[article_id]["split"],
            "provider": {"candidate_id": provider["candidate_id"], "model": provider["model"],
                         "path": str(provider_path.resolve()), "sha256": _sha256_file(provider_path)},
            "reviewer": {"candidate_id": reviewer["candidate_id"], "model": reviewer["model"],
                         "parent_candidate_id": reviewer["parent_candidate_id"],
                         "path": str(reviewer_path.resolve()), "sha256": _sha256_file(reviewer_path)},
        })
    manifest = {
        "schema_version": "stage4f-terra-sol-run-v1",
        "cohort": "stage-4e-40-v1",
        "article_count": len(records),
        "provider_model": EXPECTED_MODELS["provider"],
        "reviewer_model": EXPECTED_MODELS["reviewer"],
        "records": records,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8", newline="")
    return manifest


def main() -> None:
    """Expose individual validation and Stage 4E completeness reporting for agent runs."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("role", choices=sorted(EXPECTED_MODELS))
    validate.add_argument("path", type=Path)
    validate.add_argument("--parent", type=Path)
    status = subparsers.add_parser("status")
    status.add_argument("--candidate-dir", type=Path, default=CANDIDATE_DIR)
    manifest = subparsers.add_parser("manifest")
    manifest.add_argument("--output", type=Path, default=DEFAULT_RUN_MANIFEST)
    manifest.add_argument("--candidate-dir", type=Path, default=CANDIDATE_DIR)
    args = parser.parse_args()
    if args.command == "validate":
        payload = validate_candidate_file(args.path, args.role, args.parent)
        result = {"ok": True, "candidate_id": payload["candidate_id"], "article_id": payload["article_id"]}
    elif args.command == "status":
        result = stage4e_candidate_status(args.candidate_dir)
    else:
        built = build_stage4f_run_manifest(args.output, args.candidate_dir)
        result = {"ok": True, "output": str(args.output.resolve()),
                  "article_count": built["article_count"]}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
