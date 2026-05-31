"""分阶段提取编排：extract_staged() 按阶段顺序调 LLM client + 每阶段 guardrail+重试，返回与原 extract_one 一致的 result dict。

Phase 3：S1 相关性门（非 valid 短路）→ S2 项目识别（防过度拆分）→ 逐项目 S4 中方实体抽取。"""
import logging
from typing import Any

logger = logging.getLogger(__name__)

_VALID_S1_STATUS = {"valid_project", "no_chinese_participation", "irrelevant"}


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

    if status != "valid_project":
        return {"analysis": analysis, "status": status, "projects": []}

    # ── valid：S2 项目识别 + 基本信息（防过度拆分）──
    from s01b_project_extractor.stages.s2_basic import run_s2, validate_s2
    from s01b_project_extractor.stages.s4_entity import run_s4, validate_s4
    from s01b_project_extractor.stages.s3_finance import run_s3
    projects = _run_stage_with_retry(
        lambda: run_s2(title, content, date, llm=llm), validate_s2, stage="s2", max_attempts=2,
    )
    for proj in projects:
        proj.setdefault("project_summary", "")
        ci = proj.get("common_info", {}) or {}
        pname = ci.get("Project Name", "") or ci.get("Project Name (EN)", "")
        cat = ci.get("Category", "")
        ents = _run_stage_with_retry(
            lambda pn=pname, c=cat: run_s4(title, content, pn, c, llm=llm),
            validate_s4, stage="s4", max_attempts=2,
        )
        proj["chinese_entities"] = ents or []
        # ── S3：逐项目抽金融字段，非空值合并进 common_info（覆盖 S2 的 Unknown 占位）──
        fin = run_s3(title, content, pname, llm=llm)
        if fin:
            proj.setdefault("common_info", {}).update({k: v for k, v in fin.items() if v not in ("", None)})
    return {"analysis": analysis, "status": "valid_project", "projects": projects}
