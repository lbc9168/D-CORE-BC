"""分阶段提取编排：extract_staged() 按阶段顺序调 LLM client + 每阶段 guardrail+重试，返回与原 extract_one 一致的 result dict。

Phase 0：仍透传原单 prompt（行为不变）。后续 Phase 逐阶段替换（S1 门控 → S4 实体 → S2 基本 → S3/S5）。"""
import logging

logger = logging.getLogger(__name__)


def extract_staged(title: str, content: str, date: str, *, llm) -> dict:
    """对单篇文章跑分阶段提取，返回 {analysis, status, projects:[{project_summary, common_info, chinese_entities}]}。

    llm: LLMClient 实例（complete(prompt, *, stage, json_mode)）。
    Phase 0 实现：调用原 _build_prompt 单 prompt（透传），保证行为与基线一致。"""
    from s01b_project_extractor.tools.project_extractor_tool import _build_prompt
    prompt = _build_prompt(title, content, date)
    result = llm.complete(prompt, stage="legacy_single", json_mode=True)
    if not isinstance(result, dict):
        raise RuntimeError(f"extract_staged: LLM 返回非 dict: {type(result)}")
    return result
