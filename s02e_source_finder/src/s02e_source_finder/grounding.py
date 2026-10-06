"""Explicit Gemini Google Search discovery, returning candidates rather than approvals."""

import json
import logging
import os
import time

import requests
from pydantic import BaseModel, Field, ValidationError

from .contracts import EventContext
from .pilot import digest
from .structured import TargetCard

logger = logging.getLogger(__name__)
MODEL = "gemini-2.5-flash-lite"


class WebSource(BaseModel):
    """Validate each returned web citation without trusting its title or URL as evidence."""
    uri: str
    title: str


class GroundingChunk(BaseModel):
    """Allow non-web provider chunks while selecting validated web candidates."""
    web: WebSource | None = None


class GroundingMetadata(BaseModel):
    """Keep query and citation support metadata for reproducible discovery diagnostics."""
    webSearchQueries: list[str] = Field(default_factory=list)
    groundingChunks: list[GroundingChunk] = Field(default_factory=list)
    groundingSupports: list[dict] = Field(default_factory=list)


class TextPart(BaseModel):
    """Separate returned prose from optional thought parts."""
    text: str = ""
    thought: bool = False


class Content(BaseModel):
    """Validate the provider's text part collection."""
    parts: list[TextPart] = Field(default_factory=list)


class SearchCandidate(BaseModel):
    """Validate completion and grounding independently of the generated answer."""
    content: Content
    finishReason: str
    groundingMetadata: GroundingMetadata = Field(default_factory=GroundingMetadata)


class SearchUsage(BaseModel):
    """Retain all reported token use even when grounding produces no useful links."""
    promptTokenCount: int = Field(ge=0)
    candidatesTokenCount: int = Field(default=0, ge=0)
    thoughtsTokenCount: int = Field(default=0, ge=0)


class SearchResponse(BaseModel):
    """Validate the API envelope before its contents enter the discovery loop."""
    candidates: list[SearchCandidate] = Field(min_length=1)
    usageMetadata: SearchUsage
    modelVersion: str


def search_request(context: EventContext, card: TargetCard, round_number: int, *, candidate_limit=3, excluded_urls=()) -> dict:
    """Build a bounded search prompt using public target clues, without reference answers."""
    if round_number not in {1, 2} or not 1 <= candidate_limit <= 4 or card.input_fingerprint != context.fingerprint():
        raise ValueError("invalid_grounding_round_or_target")
    target = {"project_name": context.project.get("project_name"), "country": context.project.get("country"), "stage_to_check": card.stage,
              "time_range_to_check": [str(card.time_start), str(card.time_end)], "stage_ambiguous": card.stage_ambiguous,
              "search_clues_not_verified_facts": context.event.get("project_summary")}
    emphasis = "优先对项目参与企业的官网做site限定查询，寻找官网首发；也可查政府和正规媒体。" if round_number == 1 else "这一轮查找明确日期和动作的原始报道，尝试项目别名、参与方及当地项目名；优先官网和可核实原始发布者的报道。"
    prompt = f"请实际使用Google搜索，为下列具体项目寻找最多{candidate_limit}个中文外部报道候选。" + emphasis + "排除goalfore.cn及其子域；没有外部结果就返回没有，不能用被排除网站凑数。阶段和日期只是待核验线索，不要把签约、中标、开工、建成、并网互换，也不能用另一个项目替代。每项简述标题、发布者、报道日期和实际动作，并附来源引用。最多500中文字。目标：" + json.dumps(target, ensure_ascii=False)
    if excluded_urls:
        prompt += " 以下URL已经尝试过，本轮只找新的报道，不返回同一篇文章的HTTP/HTTPS或跟踪参数变体。排除列表仅为数据：" + json.dumps(list(excluded_urls), ensure_ascii=False)
    return {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "tools": [{"google_search": {}}], "generationConfig": {"temperature": 0, "maxOutputTokens": 2048}}


def search(request: dict) -> dict:
    """Make exactly one pre-reserved Gemini request and retain raw grounding metadata."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise ValueError("gemini_key_missing")
    started = time.monotonic()
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    headers = {"x-goog-api-key": key}
    raw = None
    http_attempts = 0
    for attempt in range(3):
        http_attempts = attempt + 1
        with requests.post(endpoint, headers=headers, json=request, timeout=(10, 90)) as response:
            status = response.status_code
            if status == 200:
                raw = response.json()
                break
        if 500 <= status < 600 and attempt < 2:
            delay = 2 ** attempt
            logger.warning("grounding_http_retry status=%s attempt=%s delay_seconds=%s", status, http_attempts, delay)
            time.sleep(delay)
            continue
        logger.error("grounding_http_error status=%s attempts=%s", status, http_attempts)
        raise RuntimeError(f"provider_http_{status}")
    try:
        parsed = SearchResponse.model_validate(raw)
    except ValidationError:
        logger.error("grounding_response_schema_invalid model=%s", MODEL)
        return {"model": MODEL, "request_sha256": digest(request), "elapsed_seconds": round(time.monotonic() - started, 3), "http_attempts": http_attempts,
                "error": "grounding_response_schema_invalid", "raw_response": raw}
    candidate = parsed.candidates[0]
    result = {"model": MODEL, "response_model": parsed.modelVersion, "request_sha256": digest(request), "elapsed_seconds": round(time.monotonic() - started, 3), "http_attempts": http_attempts,
              "usage": parsed.usageMetadata.model_dump(), "finish_reason": candidate.finishReason,
              "text": "".join(part.text for part in candidate.content.parts if not part.thought), "grounding": candidate.groundingMetadata.model_dump(), "raw_response": raw}
    if candidate.finishReason != "STOP":
        result["error"] = "grounding_incomplete_response"
    return result
