"""分阶段提取编排：extract_staged() 按阶段顺序调 LLM client + 每阶段 guardrail+重试，返回与原 extract_one 一致的 result dict。

Phase 1：S1 相关性门（非 valid 短路）→ valid 时仍用原单 prompt 跑 S2-5。后续 Phase 逐阶段替换。"""
import logging

logger = logging.getLogger(__name__)


def _run_stage_with_retry(run_fn, validate_fn, *, stage: str, retries: int = 2) -> dict:
    """跑某阶段并校验：校验不过则重试；仍不过记 logging.error 并返回最后一次输出（不静默吞，交由上层/批级 guardrail 兜底）。"""
    last = None
    for attempt in range(1, retries + 1):
        last = run_fn()
        errors = validate_fn(last)
        if not errors:
            return last
        logger.error("阶段 %s 校验未过 attempt=%d/%d errors=%s", stage, attempt, retries, errors)
    return last if last is not None else {}


def extract_staged(title: str, content: str, date: str, *, llm) -> dict:
    """分阶段提取：S1 门控（非 valid 短路）→ 后续阶段；返回与原 extract_one 一致的 result dict。"""
    from s01b_project_extractor.stages.s1_relevance import run_s1, validate_s1
    from s01b_project_extractor.tools.project_extractor_tool import _build_prompt

    # ── S1：相关性 + 中企门（带重试：校验不过则重跑该阶段）──
    s1 = _run_stage_with_retry(
        lambda: run_s1(title, content, date, llm=llm), validate_s1, stage="s1", retries=2,
    )
    status = s1.get("status")
    analysis = {
        "project_subject": s1.get("project_subject", "未知主体"),
        "primary_function": s1.get("primary_function", ""),
        "is_power_generation_plant": s1.get("is_power_generation_plant"),
        "reasoning": s1.get("reasoning", "无详细理由"),
    }
    if status != "valid_project":
        return {"analysis": analysis, "status": status, "projects": []}

    # ── valid：本 Phase 暂用原单 prompt 跑 S2-5；status 以 S1 为准 ──
    legacy = llm.complete(_build_prompt(title, content, date), stage="legacy_single", json_mode=True)
    if not isinstance(legacy, dict):
        raise RuntimeError(f"legacy 阶段返回非 dict: {type(legacy)}")
    legacy["status"] = "valid_project"
    legacy.setdefault("analysis", {}).update({"s1_reasoning": s1.get("reasoning", "")})
    return legacy
