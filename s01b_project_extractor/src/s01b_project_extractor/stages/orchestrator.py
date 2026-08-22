"""分阶段提取编排：extract_staged() 按阶段顺序调 LLM client + 每阶段 guardrail+重试，返回与原 extract_one 一致的 result dict。

Phase 4：S1 相关性门（非 valid 短路）→ S2 项目识别（防过度拆分）→ 逐项目 S4 实体抽取 → S3 金融信息 → S5 摘要+枚举自检。"""
import logging
import os
import json
from typing import Any
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
    """按中企身份及项目参与证据过滤实体，并阻止无有效中企关系的项目自动发布。"""
    entities = project.get("chinese_entities", []) or []
    if unnamed:
        project["entity_gate_excluded"] = True
        project["needs_review"] = True
        project["entity_review_reason"] = "原文明示中企参与但未具名，待人工复核"
        return

    statuses = [e.get("_entity_verification_status") for e in entities]
    unmatched_names = [
        str(e.get("Implementing Chinese Entity", "")).strip()
        for e in entities
        if e.get("_entity_verification_status") == UNMATCHED_NEEDS_REVIEW
    ]
    missing_relation_names = [
        str(e.get("Subsidiary Name") or e.get("Implementing Chinese Entity", "")).strip()
        for e in entities
        if e.get("_participation_evidence_status") != "verified"
    ]
    identity_filtered = [
        e for e in entities
        if e.get("_entity_verification_status") != VERIFIED_NON_CHINESE
    ]
    project["chinese_entities"] = [
        e for e in identity_filtered
        if e.get("_participation_evidence_status") == "verified"
    ]
    if UNMATCHED_NEEDS_REVIEW in statuses or not statuses:
        project["entity_gate_excluded"] = True
        project["needs_review"] = True
        project["entity_review_reason"] = "中企名称未命中受控登记表，待人工复核"
        project["entity_review_entities"] = "; ".join(name for name in unmatched_names if name)
        project["entity_review_reason_type"] = "entity_identity"
    elif VERIFIED_CHINESE not in statuses:
        project["entity_gate_excluded"] = True
        project["needs_review"] = False
        project["entity_review_reason"] = "无已核实中国企业"
    elif not project["chinese_entities"]:
        project["entity_gate_excluded"] = True
        project["needs_review"] = True
        project["entity_review_reason"] = "没有通过目标项目—具体企业—参与角色证据绑定的中企关系"
        project["entity_review_entities"] = "; ".join(name for name in missing_relation_names if name)
        project["entity_review_reason_type"] = "entity_relation"
    elif missing_relation_names:
        project["needs_review"] = True
        project["entity_review_reason"] = "部分中企关系缺少目标项目—企业—角色绑定证据，已从发布关系中移除"
        project["entity_review_entities"] = "; ".join(name for name in missing_relation_names if name)
        project["entity_review_reason_type"] = "entity_relation"


def _run_stage_with_retry(run_fn, validate_fn, *, stage: str, max_attempts: int = 2) -> Any:
    """跑某阶段并校验：校验不过则重试；最多尝试 max_attempts 次；仍不过记 logging.error 并返回最后一次输出（不静默吞，交由上层/批级 guardrail 兜底）。"""
    last = None
    for attempt in range(1, max_attempts + 1):
        last = run_fn()
        errors = validate_fn(last)
        if not errors:
            return last
        logger.error("阶段 %s 校验未过 attempt=%d/%d errors=%s", stage, attempt, max_attempts, errors)
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
    }

    # Fix I1: S1 重试后仍输出非法 status → fail loud，不静默丢行
    if status not in _VALID_S1_STATUS:
        raise RuntimeError(f"S1 门控重试后仍输出非法 status={status!r}（不静默丢弃，记为提取失败）")

    normalized_status = _S1_STATUS_MAP.get(status, status)

    if status in ("no_chinese_participation", "irrelevant"):
        return {"analysis": analysis, "status": normalized_status, "projects": []}

    # ── valid_project 或 chinese_unnamed_participation：S2 项目识别 + 基本信息（防过度拆分）──
    is_unnamed = status == "chinese_unnamed_participation"
    from s01b_project_extractor.stages.s2_basic import run_s2, validate_s2
    from s01b_project_extractor.stages.s4_entity import run_s4, validate_s4
    from s01b_project_extractor.stages.s3_finance import run_s3
    from s01b_project_extractor.stages.s5_summary import run_s5, apply_s5
    projects = _run_stage_with_retry(
        lambda: run_s2(title, content, date, llm=llm), validate_s2, stage="s2", max_attempts=2,
    )
    # 合并开关（S01B_MERGE_S34）：开启则 named 项目用一次合并调用同出实体+金融（省一次正文重发）；默认关=原行为。
    merge_s34 = bool(os.getenv("S01B_MERGE_S34"))
    for proj in projects:
        proj.setdefault("project_summary", "")
        ci = proj.get("common_info", {}) or {}
        pname = ci.get("Project Name", "") or ci.get("Project Name (EN)", "")
        cat = ci.get("Category", "")
        try:
            tags = tuple(json.loads(ci.get("technology_tags_json", "[]") or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            tags = ()
        run_s3_separately = True
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
                    title, content, pn, c, llm=llm, technology_tags=ts),
                validate_s34, stage="s34", max_attempts=2,
            )
            proj["chinese_entities"] = (combined or {}).get("chinese_entities") or []
            _fin = (combined or {}).get("finance") or {}
            if _fin:
                proj.setdefault("common_info", {}).update({k: v for k, v in _fin.items() if v not in ("", None)})
            run_s3_separately = False
        else:
            ents = _run_stage_with_retry(
                lambda pn=pname, c=cat, ts=tags: run_s4(
                    title, content, pn, c, llm=llm, technology_tags=ts),
                validate_s4, stage="s4", max_attempts=2,
            )
            proj["chinese_entities"] = ents or []
        # ── S3：逐项目抽金融字段，非空值合并进 common_info（合并模式下已并入 S34，跳过）──
        if run_s3_separately:
            fin = run_s3(title, content, pname, llm=llm)
            if fin:
                proj.setdefault("common_info", {}).update({k: v for k, v in fin.items() if v not in ("", None)})
        _apply_entity_publication_gate(proj, unnamed=is_unnamed)
        # ── S5：基于结构化结果生成摘要 + 高置信度枚举自检纠偏 ──
        apply_s5(proj, run_s5(proj, llm=llm))
    return {"analysis": analysis, "status": normalized_status, "projects": projects}
