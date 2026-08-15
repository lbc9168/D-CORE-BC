"""Serve the localhost-only Golden Standard Pilot and Stage 4E review application."""

from __future__ import annotations

import json
import importlib.util
import copy
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .candidate_contract import validate_candidate_payload
from .draft_store import DraftStore
from .pilot_manifest import PILOT_BY_ID
from .review_cohorts import REVIEW_BY_ID, review_article_ids, review_navigation
from .single_article import ARTICLE_ID, WORKTREE_ROOT, load_article

PACKAGE_DIR = Path(__file__).resolve().parent
TOOL_ROOT = PACKAGE_DIR.parents[1]
DATA_DIR = TOOL_ROOT / "data"
CANDIDATE_DIR = DATA_DIR / "candidates"
FIELD_DEFINITIONS = [
    ("project_name", "项目名称", "text"),
    ("category", "项目类别", "text"),
    ("subcategory", "项目子类", "text"),
    ("generation_sources", "发电能源来源", "json"),
    ("storage_status", "储能状态", "text"),
    ("storage_power_mw", "储能功率 MW", "number"),
    ("storage_energy_mwh", "储能容量 MWh", "number"),
    ("generation_capacity_mw", "发电装机 MW", "number"),
    ("project_stage", "项目阶段", "text"),
    ("country", "国家", "text"),
    ("location", "S01B 原文地点", "text"),
    ("normalized_location", "S02B-2 标准地点", "text"),
    ("investment", "投资金额", "json"),
    ("financing_mode", "融资模式", "text"),
    ("project_developer", "项目开发商", "text"),
    ("planned_start_date", "开工日期", "text"),
    ("planned_complete_date", "计划完成日期", "text"),
    ("entities", "S01B 原文实体与角色", "json"),
    ("normalized_entities", "S02B 标准实体与母公司", "json"),
]
DERIVED_FIELD_NAMES = {"normalized_location", "normalized_entities"}

app = FastAPI(title="Golden Standard Studio — Pilot + Stage 4E")
app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
store = DraftStore(DATA_DIR / "review.db")


def _field_object(value: Any, evidence: str = "", confidence: float | None = None) -> dict[str, Any]:
    """Normalize one value into the editable field envelope used by the page."""
    state = "not_mentioned" if value in (None, "", "Unknown") else "present"
    return {"value_state": state, "value": None if state == "not_mentioned" else value,
            "evidence": evidence, "confidence": confidence, "review_action": "unreviewed"}


def _article_evidence_sources(article: dict[str, Any]) -> list[str]:
    """Return the immutable title and body as separate allowed verbatim evidence sources."""
    return [str(article.get("title") or ""), str(article.get("content") or "")]


def _normalize_candidate_state(raw_state: str | None, value: Any) -> tuple[str, Any]:
    """Map model-specific envelope states to Gold draft states without demanding evidence for absence."""
    if raw_state in {"not_mentioned", "not_applicable", "needs_adjudication"}:
        return raw_state, None if raw_state == "not_mentioned" else value
    if raw_state == "unknown":
        return "uncertain", value
    if value in (None, "", "Unknown", "not_mentioned"):
        return "not_mentioned", None
    return "present", value


def _unwrap_agent_field(raw: Any) -> tuple[Any, str, float | None, str | None]:
    """Read a Provider or Reviewer field whether it is scalar or a structured envelope."""
    if not isinstance(raw, dict):
        return raw, "", None, None
    evidence = raw.get("evidence") or raw.get("quote") or ""
    if isinstance(evidence, dict):
        evidence = evidence.get("quote") or ""
    return raw.get("value"), str(evidence), raw.get("confidence"), raw.get("value_state")


def _exact_quote(evidence: str, article_sources: str | list[str]) -> str:
    """Return an exact title-or-body substring without fuzzy rewriting or invention."""
    sources = [article_sources] if isinstance(article_sources, str) else article_sources
    evidence = evidence.strip()
    if evidence and any(evidence in source for source in sources):
        return evidence
    fragments = [fragment.strip(" 。；;，,：:\n") for fragment in re.split(r"[；;。\n]", evidence)]
    matches: list[str] = []
    for fragment in fragments:
        if not fragment:
            continue
        if any(fragment in source for source in sources):
            matches.append(fragment)
            continue
        for source in sources:
            whitespace_tolerant = re.search(r"\s*".join(re.escape(char) for char in fragment), source)
            if whitespace_tolerant:
                matches.append(whitespace_tolerant.group(0))
                break
    return max(matches, key=len, default="")


def _normalize_agent_project(project: dict[str, Any], source: str,
                             article_sources: str | list[str]) -> dict[str, Any]:
    """Normalize one Subagent project into the common comparison shape without inventing values."""
    output: dict[str, Any] = {"source_kind": source}
    fields = project.get("fields") if isinstance(project.get("fields"), dict) else project
    for key, _label, _kind in FIELD_DEFINITIONS:
        if key in DERIVED_FIELD_NAMES:
            continue
        raw = fields.get(key) if isinstance(fields, dict) else None
        if raw is None and key == "subcategory":
            raw = fields.get("subcategory_raw_fact")
        if raw is None and key == "entities" and isinstance(project.get("chinese_entities"), list):
            entity_envelopes = [
                entity.get("entity") or entity.get("name") or entity.get("entity_name")
                for entity in project["chinese_entities"] if isinstance(entity, dict)
            ]
            raw = {
                "value_state": "present" if project["chinese_entities"] else "not_mentioned",
                "value": [
                    {
                        "name": _unwrap_agent_field(
                            entity.get("entity") or entity.get("name") or entity.get("entity_name")
                        )[0],
                        "role": _unwrap_agent_field(entity.get("role"))[0],
                    }
                    for entity in project["chinese_entities"] if isinstance(entity, dict)
                ],
                "evidence": "；".join(
                    filter(None, (_unwrap_agent_field(envelope)[1] for envelope in entity_envelopes))
                ),
            }
        value, evidence, confidence, state = _unwrap_agent_field(raw)
        evidence = _exact_quote(evidence, article_sources)
        if key == "project_name" and value is None:
            value = project.get("project_name")
        normalized_state, normalized_value = _normalize_candidate_state(state, value)
        field = _field_object(normalized_value, evidence, confidence)
        field["value_state"] = normalized_state
        field["value"] = normalized_value
        output[key] = field
    return output


def _normalize_database_project(project: dict[str, Any]) -> dict[str, Any]:
    """Project current aggregate database values into the common field comparison shape."""
    mapped = {
        "project_name": project.get("project_name"), "category": project.get("category"),
        "subcategory": project.get("subcategory"), "generation_sources": [],
        "storage_status": "confirmed" if project.get("storage_energy_mwh") else "not_mentioned",
        "storage_power_mw": project.get("storage_power_mw"),
        "storage_energy_mwh": project.get("storage_energy_mwh"), "generation_capacity_mw": None,
        "project_stage": project.get("project_stage"), "country": project.get("country"),
        "location": project.get("location"),
        "normalized_location": project.get("location_norm") or project.get("location"),
        "investment": project.get("total_investment_usd_raw"),
        "financing_mode": project.get("financing_model"),
        "project_developer": project.get("project_developer"),
        "planned_start_date": project.get("planned_start_date"),
        "planned_complete_date": project.get("planned_complete_date"),
        "entities": [
            {"name": entity.get("subsidiary_name") or entity.get("implementing_entity"),
             "role": entity.get("involvement_type")}
            for entity in project.get("entities", [])
        ],
        "normalized_entities": [
            {"mentioned_name": entity.get("subsidiary_name") or entity.get("implementing_entity"),
             "canonical_entity": entity.get("subsidiary_name") or entity.get("implementing_entity"),
             "parent_company": entity.get("implementing_entity"),
             "role": entity.get("involvement_type")}
            for entity in project.get("entities", [])
        ],
    }
    return {"source_kind": "database", "cross_source_aggregate": project.get("cross_source_aggregate"),
            **{key: _field_object(value) for key, value in mapped.items()}}


def _normalize_historical_project(project: dict[str, Any]) -> dict[str, Any]:
    """Project the article's stored pipeline output into the common field comparison shape."""
    output = {"source_kind": "pipeline"}
    for key, _label, _kind in FIELD_DEFINITIONS:
        value = project.get(key)
        if key == "normalized_location":
            value = value or project.get("location")
        output[key] = _field_object(value)
    return output


@lru_cache(maxsize=1)
def _energy_policy_module():
    """Load the worktree's current deterministic energy taxonomy module without installing the pipeline."""
    pipeline_src = WORKTREE_ROOT / "s01b_project_extractor" / "src"
    for import_root in (WORKTREE_ROOT, pipeline_src):
        if str(import_root) not in sys.path:
            sys.path.insert(0, str(import_root))
    path = pipeline_src / "s01b_project_extractor" / "energy_taxonomy.py"
    module_name = "gold_studio_energy_taxonomy"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载确定性 policy：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _deterministic_project(provider_project: dict[str, Any]) -> dict[str, Any]:
    """Derive energy facts and proposed category from one project's own exact evidence snippets."""
    policy = _energy_policy_module()
    generation_evidence = provider_project.get("generation_sources", {}).get("evidence", "")
    storage_evidence_parts = list(dict.fromkeys(
        provider_project.get(name, {}).get("evidence", "")
        for name in ("storage_status", "storage_power_mw", "storage_energy_mwh")
        if provider_project.get(name, {}).get("evidence")
    ))
    storage_evidence = "\n".join(storage_evidence_parts)
    generation_facts = policy.extract_energy_facts(generation_evidence)
    storage_facts = policy.extract_energy_facts(storage_evidence)
    classification = policy.classify_energy(
        generation_facts.generation_sources, storage_facts.storage_status
    )
    category_evidence = provider_project.get("category", {}).get("evidence", "")
    return {
        "source_kind": "policy", "policy_version": classification.policy_version,
        "project_name": provider_project["project_name"],
        "category": _field_object(classification.proposed_category, category_evidence),
        "generation_sources": _field_object(list(generation_facts.generation_sources), generation_evidence),
        "storage_status": _field_object(storage_facts.storage_status, storage_evidence_parts[0] if storage_evidence_parts else ""),
        "storage_power_mw": _field_object(storage_facts.storage_power_mw,
                                           provider_project.get("storage_power_mw", {}).get("evidence", "")),
        "storage_energy_mwh": _field_object(storage_facts.storage_energy_mwh,
                                             provider_project.get("storage_energy_mwh", {}).get("evidence", "")),
    }


def _read_candidate_artifacts(article_id: int, article_sha256: str) -> list[dict[str, Any]]:
    """Import valid Terra/Sol JSON artifacts whose article hash matches the immutable snapshot."""
    raw_artifacts: list[dict[str, Any]] = []
    if not CANDIDATE_DIR.is_dir():
        return raw_artifacts
    for path in sorted(CANDIDATE_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("article_sha256") != article_sha256:
            continue
        source = payload.get("execution_role", "").lower()
        if source not in {"provider", "reviewer"}:
            continue
        candidate_id = payload.get("candidate_id") or path.stem
        if payload.get("article_id") != article_id:
            continue
        raw_artifacts.append({"candidate_id": candidate_id, "source": source, "payload": payload})
    provider_by_id = {
        item["candidate_id"]: item["payload"] for item in raw_artifacts if item["source"] == "provider"
    }
    artifacts = []
    for item in raw_artifacts:
        parent = provider_by_id.get(item["payload"].get("parent_candidate_id"))
        is_stage4e = REVIEW_BY_ID[article_id]["cohort"] != "pilot-v0.1.1"
        if is_stage4e and validate_candidate_payload(item["payload"], item["source"], parent):
            continue
        store.import_candidate(item["candidate_id"], article_id, item["source"],
                               item["payload"].get("model") or "unknown", item["payload"],
                               item["payload"].get("parent_candidate_id"))
        artifacts.append(item)
    return sorted(artifacts, key=lambda item: {"provider": 0, "reviewer": 1}[item["source"]])


def _candidate_projects(payload: dict[str, Any], source: str,
                        article_sources: str | list[str]) -> list[dict[str, Any]]:
    """Extract normalized projects from a Provider or Reviewer artifact."""
    projects = payload.get("projects") or payload.get("reviewed_projects") or []
    return [
        _normalize_agent_project(project, source, article_sources)
        for project in projects if isinstance(project, dict)
    ]


def _align_projects(anchor: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> list[dict[str, Any] | None]:
    """Align source projects to Terra's review order by project name instead of fragile row position."""
    remaining = set(range(len(candidates)))
    aligned: list[dict[str, Any] | None] = []
    for project in anchor:
        anchor_name = str(project.get("project_name", {}).get("value") or "")
        scores = [
            (SequenceMatcher(None, anchor_name, str(candidates[index].get("project_name", {}).get("value") or "")).ratio(), index)
            for index in remaining
        ]
        score, best = max(scores, default=(0.0, -1))
        if best >= 0 and score >= 0.28:
            aligned.append(candidates[best])
            remaining.remove(best)
        else:
            aligned.append(None)
    return aligned


def _initial_draft(context: dict[str, Any], artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    """Prefill a lenient human draft from Terra output or the stored pipeline result."""
    provider = next((item for item in artifacts if item["source"] == "provider"), None)
    article_sources = _article_evidence_sources(context["article"])
    base_projects = (_candidate_projects(provider["payload"], "provider", article_sources) if provider else
                     [_normalize_historical_project(item) for item in context["historical_extraction_projects"]])
    normalization_sources = {
        "pipeline": _align_projects(
            base_projects,
            [_normalize_historical_project(item) for item in context["historical_extraction_projects"]],
        ),
        "database": _align_projects(
            base_projects,
            [_normalize_database_project(item) for item in context["database_projects"]],
        ),
    } if base_projects else {}
    projects = []
    for index, project in enumerate(base_projects, start=1):
        fields = {}
        for key, _label, _kind in FIELD_DEFINITIONS:
            source_field = project.get(key)
            if key in DERIVED_FIELD_NAMES:
                source_order = ("database", "pipeline") if key == "normalized_location" else ("pipeline", "database")
                source_field = next(
                    (aligned[index - 1].get(key) for source in source_order
                     for aligned in [normalization_sources[source]]
                     if aligned[index - 1] and aligned[index - 1].get(key, {}).get("value_state") == "present"),
                    None,
                )
            fields[key] = copy.deepcopy(source_field or _field_object(None))
        projects.append({"gold_project_id": f"P{index:02d}",
                         "project_name": fields["project_name"].get("value") or f"项目 {index}",
                         "relation_note": "文章内独立项目", "fields": fields})
    provider_status = ((provider or {}).get("payload") or {}).get("article_status") or {}
    provider_status = provider_status.get("value") if isinstance(provider_status, dict) else provider_status
    provider_reason = ((provider or {}).get("payload") or {}).get("non_project_reason") or {}
    provider_reason = provider_reason.get("value") if isinstance(provider_reason, dict) else provider_reason
    article_status = {
        "valid_project": "valid", "no_chinese_participation": "no_chinese",
        "chinese_unnamed_participation": "chinese_unnamed", "irrelevant": "irrelevant",
        "other_non_project": "irrelevant",
    }.get(provider_status)
    if article_status is None:
        article_id = context["article"]["id"]
        article_status = (
            "valid" if base_projects else PILOT_BY_ID.get(article_id, {}).get("expected_status", "")
        )
    if article_status != "valid":
        projects = []
    return {"article_status": article_status, "non_project_reason": provider_reason, "projects": projects,
            "last_evidence": "", "scroll_y": 0}


def _upgrade_layered_draft(context: dict[str, Any], artifacts: list[dict[str, Any]],
                           draft: dict[str, Any]) -> bool:
    """Add layered fields and repair legacy empty location prefills without replacing manual edits."""
    defaults = _initial_draft(context, artifacts)
    changed = False
    for index, project in enumerate(draft["payload"].get("projects") or []):
        fields = project.setdefault("fields", {})
        default_fields = defaults["projects"][index]["fields"] if index < len(defaults["projects"]) else {}
        for field_name in DERIVED_FIELD_NAMES:
            current = fields.get(field_name)
            default = default_fields.get(field_name) or _field_object(None)
            legacy_empty_location = (
                field_name == "normalized_location"
                and isinstance(current, dict)
                and current.get("value") in (None, "")
                and current.get("value_state") == "not_mentioned"
                and current.get("review_action") != "modified"
                and default.get("value_state") == "present"
            )
            if current is None or legacy_empty_location:
                fields[field_name] = copy.deepcopy(default)
                changed = True
    return changed


def _upgrade_untouched_prefill(context: dict[str, Any], artifacts: list[dict[str, Any]],
                               draft: dict[str, Any]) -> dict[str, Any] | None:
    """Replace only an untouched automatic first draft when a new Provider candidate becomes available."""
    if draft["confirmed"] or draft["revision"] != 1 or not any(
        item["source"] == "provider" for item in artifacts
    ):
        return None
    projects = draft["payload"].get("projects") or []
    if any(
        field.get("review_action") != "unreviewed"
        for project in projects for field in (project.get("fields") or {}).values()
    ):
        return None
    return _initial_draft(context, artifacts)


def build_review_context(article_id: int = ARTICLE_ID) -> dict[str, Any]:
    """Assemble immutable sources, Subagent candidates, and the latest local human draft."""
    if article_id not in REVIEW_BY_ID:
        raise LookupError(f"文章 {article_id} 不在当前复核 cohort")
    context = load_article(article_id)
    article = context["article"]
    store.seed_article(article_id, article["article_sha256"], context)
    artifacts = _read_candidate_artifacts(article_id, article["article_sha256"])
    draft = store.get_draft(article_id)
    if draft is None:
        store.save_draft(article_id, _initial_draft(context, artifacts))
        draft = store.get_draft(article_id)
    elif (replacement := _upgrade_untouched_prefill(context, artifacts, draft)) is not None:
        store.save_draft(article_id, replacement)
        draft = store.get_draft(article_id)
    elif _upgrade_layered_draft(context, artifacts, draft) and not draft["confirmed"]:
        store.save_draft(article_id, draft["payload"])
        draft = store.get_draft(article_id)
    sources: dict[str, list[dict[str, Any]]] = {
        "database": [_normalize_database_project(item) for item in context["database_projects"]],
        "pipeline": [_normalize_historical_project(item) for item in context["historical_extraction_projects"]],
    }
    for artifact in artifacts:
        sources[artifact["source"]] = _candidate_projects(
            artifact["payload"], artifact["source"], _article_evidence_sources(article)
        )
    article_candidates = [
        {"source": artifact["source"],
         "status": _unwrap_agent_field(artifact["payload"].get("article_status"))[0],
         "reason": _unwrap_agent_field(artifact["payload"].get("non_project_reason"))[0]}
        for artifact in artifacts
    ]
    if sources.get("provider"):
        for source in ("database", "pipeline", "reviewer"):
            if sources.get(source):
                sources[source] = _align_projects(sources["provider"], sources[source])
        sources["policy"] = [_deterministic_project(project) for project in sources["provider"]]
    source_project_summary = {
        source: [project.get("project_name", {}).get("value") for project in projects if project]
        for source, projects in sources.items() if source in {"database", "pipeline"}
    }
    return {**context, "field_definitions": FIELD_DEFINITIONS,
            "derived_field_names": sorted(DERIVED_FIELD_NAMES), "candidate_sources": sources,
            "candidate_runs": [
                {"candidate_id": artifact["candidate_id"], "article_id": article_id,
                 "source": artifact["source"], "model": artifact["payload"].get("model"),
                 "parent_candidate_id": artifact["payload"].get("parent_candidate_id")}
                for artifact in artifacts
            ],
            "review_actions": store.list_actions(article_id), "draft": draft,
            "article_candidates": article_candidates,
            "source_project_summary": source_project_summary,
            "candidates_hidden": False,
            "pilot": REVIEW_BY_ID[article_id], "pilot_nav": _pilot_navigation(article_id)}


def _pilot_navigation(article_id: int) -> dict[str, Any]:
    """Retain the template compatibility name for navigation across all review cohorts."""
    return review_navigation(article_id)


def build_queue_context() -> dict[str, Any]:
    """Build the Pilot plus Stage 4E queue without recording human timing."""
    items = []
    for article_id in review_article_ids():
        article = load_article(article_id)["article"]
        draft = store.get_draft(article_id)
        candidates = _read_candidate_artifacts(article_id, article["article_sha256"])
        items.append({**REVIEW_BY_ID[article_id], "title": article["title"],
                      "publish_date": article.get("publish_date"),
                      "state": "confirmed" if draft and draft["confirmed"] else
                               "draft" if draft else "not_started",
                      "candidate_roles": sorted({item["source"] for item in candidates})})
    confirmed = sum(item["state"] == "confirmed" for item in items)
    started = sum(item["state"] != "not_started" for item in items)
    split_counts = Counter(item["split"] for item in items)
    groups = [
        {"key": "pilot", "label": "Pilot development", "items": [item for item in items if item["cohort"] == "pilot-v0.1.1"]},
        {"key": "stage4e-development", "label": "Stage 4E development", "items": [item for item in items if item["cohort"] != "pilot-v0.1.1" and item["split"] == "development"]},
        {"key": "stage4e-challenge", "label": "Stage 4E challenge", "items": [item for item in items if item["split"] == "challenge"]},
    ]
    return {"items": items, "groups": groups, "confirmed": confirmed, "started": started,
            "total": len(items), "development": split_counts["development"],
            "challenge": split_counts["challenge"], "blind": 0}


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """Redirect the Pilot root to the risk-ordered review queue."""
    return RedirectResponse("/queue")


@app.get("/queue", response_class=HTMLResponse)
def queue_page(request: Request) -> HTMLResponse:
    """Render the grouped fifty-article Pilot and Stage 4E queue."""
    return templates.TemplateResponse(request, "queue.html", {"queue": build_queue_context()})


@app.get("/review/{article_id}", response_class=HTMLResponse)
def review_page(request: Request, article_id: int) -> HTMLResponse:
    """Render the double-column evidence review page for one fixed cohort article."""
    if article_id not in REVIEW_BY_ID:
        raise HTTPException(404, "文章不在当前复核 cohort")
    context = build_review_context(article_id)
    return templates.TemplateResponse(request, "review.html", {"context": context})


@app.get("/api/review/{article_id}")
def review_state(article_id: int) -> dict[str, Any]:
    """Return the complete comparison and draft state used after refresh."""
    if article_id not in REVIEW_BY_ID:
        raise HTTPException(404, "文章不在当前复核 cohort")
    return build_review_context(article_id)


@app.put("/api/review/{article_id}/draft")
async def save_draft(article_id: int, request: Request) -> dict[str, Any]:
    """Autosave an unrestricted JSON draft and return its new revision."""
    if article_id not in REVIEW_BY_ID:
        raise HTTPException(404, "文章不在当前复核 cohort")
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("草稿必须是 JSON object")
        return {"ok": True, **store.save_draft(article_id, payload)}
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/review/{article_id}/confirm")
def confirm_article(article_id: int) -> dict[str, Any]:
    """Apply strict final validation and confirm the article without changing source data."""
    if article_id not in REVIEW_BY_ID:
        raise HTTPException(404, "文章不在当前复核 cohort")
    try:
        return {"ok": True, **store.confirm(article_id)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/review/{article_id}/reopen")
def reopen_article(article_id: int) -> dict[str, Any]:
    """Reopen a confirmed draft and immediately persist any safe schema or prefill upgrades."""
    if article_id not in REVIEW_BY_ID:
        raise HTTPException(404, "文章不在当前复核 cohort")
    try:
        store.reopen(article_id)
        upgraded = build_review_context(article_id)["draft"]
        return {"ok": True, "confirmed": False, "revision": upgraded["revision"],
                "updated_at": upgraded["updated_at"]}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
