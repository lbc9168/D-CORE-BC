"""S1 阶段：调 LLM 做相关性+中企门控，in-memory 校验 status 枚举合法。"""
import logging

logger = logging.getLogger(__name__)

_VALID_STATUS = {"valid_project", "no_chinese_participation", "irrelevant", "chinese_unnamed_participation"}


def run_s1(title: str, content: str, date: str, *, llm) -> dict:
    """跑 S1 门控，返回 {status, project_subject, is_power_generation_plant, reasoning}。"""
    from s01b_project_extractor.prompts.s1_relevance import build_s1_prompt
    prompt = build_s1_prompt(title, content, date)
    out = llm.complete(prompt, stage="s1", json_mode=True)
    if not isinstance(out, dict):
        raise RuntimeError(f"S1 返回非 dict: {type(out)}")
    return out


def validate_s1(s1_out: dict) -> list[str]:
    """S1 输出 in-memory 校验：status 必须是合法枚举。返回错误列表（空=通过）。"""
    errors = []
    status = str(s1_out.get("status", "")).strip()
    if status not in _VALID_STATUS:
        errors.append(f"S1 status 非法: {status!r}（应∈{_VALID_STATUS}）")
    return errors
