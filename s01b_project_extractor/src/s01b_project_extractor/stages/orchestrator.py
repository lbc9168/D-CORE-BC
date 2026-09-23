"""分阶段提取编排：extract_staged() 按阶段顺序调 LLM client + 每阶段 guardrail+重试，返回与原 extract_one 一致的 result dict。

Phase 4：S1 相关性门（非 valid 短路）→ S2 项目识别（防过度拆分）→ 逐项目 S4 实体抽取 → S3 金融信息 → S5 摘要+枚举自检。"""
import logging
import os
import json
from copy import deepcopy
from typing import Any
from s01b_project_extractor.stages.entity_response import EntityResponseError
from s01b_project_extractor.llm.client import LLMResponseParseError
import bri_enums as enums
from s01b_project_extractor.stages.entity_normalize import (
    UNMATCHED_NEEDS_REVIEW,
    VERIFIED_CHINESE,
    VERIFIED_NON_CHINESE,
)

logger = logging.getLogger(__name__)

_VALID_S1_STATUS = {"valid_project", "no_chinese_participation", "irrelevant", "chinese_unnamed_participation"}

# S1 raw status → 对外透出的标准化 status；仅 chinese_unnamed_participation 做重命名，
# 其余为恒等项（确保 _S1_STATUS_MAP 与 _VALID_S1_STATUS 保持全覆盖）。
_S1_STATUS_MAP = {
    "valid_project": "valid_project",
    "no_chinese_participation": "no_chinese_participation",
    "irrelevant": "irrelevant",
    "chinese_unnamed_participation": "chinese_unnamed",  # ← 唯一重命名项
}


def _apply_entity_publication_gate(project: dict, *, unnamed: bool = False) -> None:
    """身份、名称入册和参与证据分开判断；待核实成员不得否定已确认关系。"""
    entities = project.get("chinese_entities", []) or []
    # 保存候选原文与证据，发布投影移除的关系仍能进入同一补录清单。
    project["entity_candidates"] = entities
    project["entity_gate_excluded"] = False
    project["publication_status"] = "eligible"
    project["needs_review"] = False
    if unnamed:
        project["entity_gate_excluded"] = True
        project["publication_status"] = "pending_review"
        project["needs_review"] = True
        project["entity_review_reason"] = "原文明示中企参与但未具名，待人工复核"
        project["entity_review_reason_type"] = "unnamed_entity"
        return

    publishable, identity_pending, relation_pending, unlinked = [], [], [], []
    for entity in entities:
        status = entity.get("_entity_verification_status")
        name = str(entity.get("_entity_raw_name") or entity.get("Subsidiary Name")
                   or entity.get("Implementing Chinese Entity", "")).strip()
        if status == VERIFIED_NON_CHINESE:
            continue
        if entity.get("_participation_evidence_status") == "not_participating":
            continue
        chinese = status in {VERIFIED_CHINESE, "model_supported_chinese"} or (
            status == UNMATCHED_NEEDS_REVIEW
            and entity.get("_identity_evidence_status") == "verified"
            and entity.get("_entity_matched_source") not in {"ambiguous", "registry_needs_review"}
        )
        if not chinese:
            identity_pending.append(name)
            continue
        if entity.get("_participation_evidence_status") != "verified":
            relation_pending.append(name)
            continue
        publishable.append(entity)
        if not entity.get("_entity_id"):
            unlinked.append(name)

    project["chinese_entities"] = publishable
    if not publishable:
        project["entity_gate_excluded"] = True
        all_foreign = bool(entities) and all(
            e.get("_entity_verification_status") == VERIFIED_NON_CHINESE
            or e.get("_participation_evidence_status") == "not_participating" for e in entities
        )
        project["publication_status"] = "excluded" if all_foreign else "pending_review"
        if all_foreign:
            project["entity_review_reason"] = "无目标项目中企参与：明确外企或不参与"
            return
    if identity_pending or relation_pending or unlinked or not entities:
        project["needs_review"] = True
        names = identity_pending + relation_pending + unlinked
        project["entity_review_entities"] = "; ".join(dict.fromkeys(n for n in names if n))
        if identity_pending or unlinked or not entities:
            project["entity_review_reason_type"] = "entity_identity"
            project["entity_review_reason"] = (
                "已有中企参与证据；部分企业名称或身份待补录，已确认关系正常保留"
                if publishable else "企业身份或名称尚待核实，保留候选与原文证据"
            )
        else:
            project["entity_review_reason_type"] = "entity_relation"
            project["entity_review_reason"] = "部分企业的目标项目参与证据待核实，保留候选原文"


def _run_stage_with_retry(run_fn, validate_fn, *, stage: str, max_attempts: int = 2,
                          feedback: list[str] | None = None) -> Any:
    """跑某阶段并校验：校验不过则重试；最多尝试 max_attempts 次；仍不过记 logging.error 并返回最后一次输出（不静默吞，交由上层/批级 guardrail 兜底）。"""
    last = None
    candidates = []
    failures = []
    for attempt in range(1, max_attempts + 1):
        try:
            current = run_fn()
        except LLMResponseParseError as exc:
            logger.error("阶段 %s 解析失败 attempt=%d/%d: %s", stage, attempt, max_attempts, exc)
            failures.append({"attempt": attempt, "error": str(exc), "response": exc.payload})
            if feedback is not None:
                feedback[:] = [str(exc)]
            if attempt == max_attempts and last is None:
                raise EntityResponseError(f"{stage}: all attempts failed parsing", failures) from exc
            continue
        last = current
        if stage in {"s4", "s34"}:
            entities = current if stage == "s4" else current["chinese_entities"]
            names = {e.get("_entity_raw_name") for e in entities}
            for entity in entities:
                prior = [deepcopy(e) for e in candidates
                         if e.get("_entity_raw_name") == entity.get("_entity_raw_name")]
                if prior:
                    entity["_prior_candidate_evidence"] = prior
            # A retry can omit earlier candidates; retain those omissions for review,
            # without promoting their unverified participation into the final projection.
            for prior in candidates:
                if prior.get("_entity_raw_name") not in names:
                    preserved = deepcopy(prior)
                    preserved["_participation_evidence_status"] = "missing_or_ambiguous"
                    preserved.setdefault("_participation_evidence_reasons", []).append("重试未保留该候选，保留证据待复核")
                    entities.append(preserved)
            candidates = deepcopy(entities)
            for entity in entities:
                if failures:
                    entity["_stage_response_errors"] = deepcopy(failures)
        errors = validate_fn(last)
        if feedback is not None:
            feedback[:] = errors
        if not errors:
            return last
        logger.error("阶段 %s 校验未过 attempt=%d/%d errors=%s", stage, attempt, max_attempts, errors)
    if failures and stage in {"s4", "s34"} and last is not None:
        entities = last if stage == "s4" else last["chinese_entities"]
        for entity in entities:
            entity["_stage_response_errors"] = deepcopy(failures)
    return last if last is not None else {}


def extract_staged(title: str, content: str, date: str, *, llm) -> dict:
    """分阶段提取：S1 门控（非 valid 短路）→ S2 项目识别 → 逐项目 S4 实体抽取；返回与原 extract_one 一致的 result dict。"""
    from s01b_project_extractor.stages.s1_relevance import run_s1, validate_s1

    # ── S1：相关性 + 中企门（带重试：校验不过则重跑该阶段）──
    s1 = _run_stage_with_retry(
        lambda: run_s1(title, content, date, llm=llm), validate_s1, stage="s1", max_attempts=2,
    )
    status = s1.get("status")
    analysis = {
        "project_subject": s1.get("project_subject", "未知主体"),
        "is_power_generation_plant": s1.get("is_power_generation_plant"),
        "reasoning": s1.get("reasoning", "无详细理由"),
        "identity_recall": s1.get("identity_recall", {}),
        "candidate_entities": s1.get("candidate_entities"),
    }

    # Fix I1: S1 重试后仍输出非法 status → fail loud，不静默丢行
    if status not in _VALID_S1_STATUS:
        raise RuntimeError(f"S1 门控重试后仍输出非法 status={status!r}（不静默丢弃，记为提取失败）")

    normalized_status = _S1_STATUS_MAP.get(status, status)

    if status in ("no_chinese_participation", "irrelevant"):
        return {"analysis": analysis, "status": normalized_status, "projects": []}

    # ── valid_project 或 chinese_unnamed_participation：S2 项目识别 + 基本信息（防过度拆分）──
    is_unnamed = status == "chinese_unnamed_participation"
    from s01b_project_extractor.stages.s2_basic import run_s2, validate_s2_blocking
    from s01b_project_extractor.stages.s4_entity import run_s4, validate_s4
    from s01b_project_extractor.stages.s3_finance import run_s3, apply_finance_review
    from s01b_project_extractor.stages.s5_summary import run_s5, apply_s5
    s2_feedback: list[str] = []
    def run_s2_with_feedback():
        result = run_s2(title, content, date, llm=llm, retry_feedback=s2_feedback)
        s2_feedback[:] = validate_s2_blocking(result)
        return result
    projects = _run_stage_with_retry(run_s2_with_feedback, validate_s2_blocking, stage="s2", max_attempts=2, feedback=s2_feedback)
    if not projects:
        # An explicit, parsed empty S2 list is a successful scope decision. Persist
        # it through the existing discarded channel so normal URL dedup skips it.
        analysis["reasoning"] += "；S2未识别本次范围内项目或实质非项目记录"
        return {"analysis": analysis, "status": "irrelevant", "projects": []}
    # 合并开关（S01B_MERGE_S34）：开启则 named 项目用一次合并调用同出实体+金融（省一次正文重发）；默认关=原行为。
    merge_s34 = bool(os.getenv("S01B_MERGE_S34"))
    pending_projects = [(project, False) for project in projects]
    projects = []
    while pending_projects:
        proj, boundary_retried = pending_projects.pop(0)
        proj.setdefault("project_summary", "")
        ci = proj.get("common_info", {}) or {}
        # Optional quantities already rejected by S2 must not become target identity
        # in S34/S4/S3 or facts in S5. Original names remain in the audit ledger.
        ci.update(ci.get("capacity_clean_names", {}))
        pname = ci.get("Project Name", "") or ci.get("Project Name (EN)", "")
        cat = ci.get("Category", "")
        try:
            tags = tuple(json.loads(ci.get("technology_tags_json", "[]") or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            tags = ()
        run_s3_separately = True
        entity_feedback: list[str] = []
        if is_unnamed:
            # unnamed：不调 S4，直接放占位实体（S3 仍单独跑）
            proj["chinese_entities"] = [{"Implementing Chinese Entity": enums.UNNAMED_ENTITY_PLACEHOLDER,
                                         "Type of involvement of Chinese Entities": "Unknown",
                                         "is_unnamed_placeholder": True}]
        elif merge_s34:
            # ── S34 合并：一次调用同出 chinese_entities + 金融字段，跳过单独 S3 ──
            from s01b_project_extractor.stages.s34_entity_finance import run_s34, validate_s34
            combined = _run_stage_with_retry(
                lambda pn=pname, c=cat, ts=tags: run_s34(
                    title, content, pn, c, llm=llm, technology_tags=ts, retry_feedback=entity_feedback,
                    project_context=ci.get("Project Context")),
                validate_s34, stage="s34", max_attempts=2, feedback=entity_feedback,
            )
            proj["chinese_entities"] = (combined or {}).get("chinese_entities") or []
            proj["project_assessment"] = (combined or {}).get("project_assessment")
            _fin = (combined or {}).get("finance") or {}
            if _fin:
                apply_finance_review(proj.setdefault("common_info", {}), _fin, stage="s34")
            run_s3_separately = False
        else:
            ents = _run_stage_with_retry(
                lambda pn=pname, c=cat, ts=tags: run_s4(
                    title, content, pn, c, llm=llm, technology_tags=ts, retry_feedback=entity_feedback,
                    project_context=ci.get("Project Context")),
                validate_s4, stage="s4", max_attempts=2, feedback=entity_feedback,
            )
            proj["chinese_entities"] = ents or []
            proj["project_assessment"] = getattr(ents, "project_assessment", None)
        # ── S3：逐项目抽金融字段，非空值合并进 common_info（合并模式下已并入 S34，跳过）──
        if run_s3_separately:
            fin = run_s3(title, content, pname, llm=llm, project_context=ci.get("Project Context"))
            if fin:
                apply_finance_review(proj.setdefault("common_info", {}), fin, stage="s3")
        _apply_entity_publication_gate(proj, unnamed=is_unnamed)
        assessment = proj.get("project_assessment") or {}
        if (assessment.get("is_single_physical_project") is False and not boundary_retried
                and len(assessment.get("separate_projects", [])) >= 2):
            # One bounded repair uses the strong stage's explicit physical-site
            # judgment. S2 must re-extract each asset; do not clone the package's
            # capacity, investment, dates or entity roles into children.
            feedback = [
                "项目边界复核明确指出误并，修复当前目标：" + pname,
                assessment["rationale"],
                "本次仅分别提取这些独立工程，其他已处理工程不重复：" + json.dumps(assessment["separate_projects"], ensure_ascii=False),
                "每站一条；共同合同/标题不是单个物理项目。合计容量和金额只写明细，每站未知留空。",
            ]
            repaired = _run_stage_with_retry(
                lambda: run_s2(title, content, date, llm=llm, retry_feedback=feedback),
                validate_s2_blocking, stage="s2", max_attempts=1,
            )
            analysis.setdefault("boundary_corrections", []).append({
                "original_project": deepcopy(proj), "feedback": feedback,
                "reextracted_project_count": len(repaired),
            })
            if len(repaired) >= 2:
                pending_projects[0:0] = [(p, True) for p in repaired]
                continue
            proj["boundary_reextract_failed"] = True
        projects.append(proj)
        if assessment.get("is_current_event") is False:
            proj.update(in_scope=False, out_of_scope_reason=assessment.get("rationale", "仅背景事件"),
                        publication_status="excluded", entity_gate_excluded=True)
            continue
        if assessment and (assessment.get("is_single_physical_project") is not True or assessment.get("is_current_event") is None):
            proj.update(publication_status="pending_review", entity_gate_excluded=True, needs_review=True,
                        entity_review_reason_type="project_fields", entity_review_reason=assessment.get("rationale", "项目边界未决"))
            continue
        # ── S5：只基于结构化事实生成摘要，不授予事实修改权 ──
        apply_s5(proj, run_s5(proj, llm=llm))
        ci.update(ci.get("capacity_clean_names", {}))
        _apply_structure_publication_gate(proj)
    return {"analysis": analysis, "status": normalized_status, "projects": projects}


def _apply_structure_publication_gate(project: dict) -> None:
    """Core ambiguity blocks the project; safe null fields remain independently reviewable."""
    from s01b_project_extractor.stages.s2_basic import classify_s2_validation
    errors, warnings = classify_s2_validation([project])
    project["structure_validation_errors"] = errors
    project["field_review_warnings"] = warnings
    if warnings:
        project["needs_review"] = True
        logger.warning("项目字段留空/格式复核，不阻止已核实关系流转: %s", warnings)
    if not errors:
        return
    project["structure_validation_errors"] = errors
    project["entity_gate_excluded"] = True
    project["publication_status"] = "pending_review"
    project["needs_review"] = True
    previous = project.get("entity_review_reason", "")
    project["entity_review_reason"] = (previous + "；" if previous else "") + "项目字段校验失败，禁止仅凭中企参与发布"
    project.setdefault("entity_review_reason_type", "project_fields")
    logger.error("项目发布门控: %s", errors)
