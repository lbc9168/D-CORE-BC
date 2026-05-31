"""critic：批判 agent——重读原文质疑某项目的提取字段并挑战相关性，输出经 pydantic 校验的 verdict 列表。"""
import json
import logging
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)


class Verdict(BaseModel):
    """单字段核验结论：verdict 三选一，confidence∈[0,1]。"""
    field: str
    verdict: Literal["ok", "wrong", "unsupported"]
    suggested_value: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = ""


_PROMPT = """你是严谨的核查员。下面是一篇新闻原文和从中提取的某个项目记录。
请逐字段核验提取是否被原文支持，并额外挑战：这真的是"有中国企业参与的可再生能源项目"吗（还是油气/矿业/民用工程被误收）？

原文标题：{title}
原文正文：{content}

提取记录（JSON）：{project}
重点核查这些可疑字段：{suspicious}

只输出 JSON：{{"verdicts":[{{"field":"字段名或 status","verdict":"ok|wrong|unsupported","suggested_value":"建议值或null","confidence":0.0,"evidence":"原文依据"}}]}}
- verdict=ok 表示原文支持当前值；wrong 表示错且给 suggested_value；unsupported 表示原文找不到依据。
- 对"是否相关/是否真有中企项目"的判断，field 写 "status"。"""


def review_project(*, article: dict, project: dict, suspicious: list, llm) -> list:
    """对一个可疑项目跑批判核验；返回 Verdict 列表；LLM/解析失败由 llm 层抛出，输出非法则抛 ValidationError。"""
    prompt = _PROMPT.format(
        title=article.get("title", ""), content=article.get("content", ""),
        project=json.dumps(project, ensure_ascii=False), suspicious=suspicious,
    )
    raw = llm.complete(prompt, stage="critic", json_mode=True)
    items = raw.get("verdicts", []) if isinstance(raw, dict) else []
    try:
        return [Verdict.model_validate(it) for it in items]
    except ValidationError as e:
        logger.error("critic 输出校验失败: %s | 原始=%s", e, raw)
        raise
