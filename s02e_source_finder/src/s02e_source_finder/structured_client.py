"""One reserved structured extraction request with explicit usage and version metadata."""

import hashlib
import json
import logging
import os
import re
import time
from urllib.parse import urlsplit

import requests
from pydantic import ValidationError

from .structured import Extraction, SYSTEM

logger = logging.getLogger(__name__)


class ExtractionError(ValueError):
    """Carry known usage through malformed model output without exposing raw responses."""

    def __init__(self, code: str, call: dict):
        super().__init__(code)
        self.call = call


def native_schema() -> dict:
    """Keep server grammar structural; validate numerical and array limits locally."""
    def simplify(value):
        """Remove bounded-value constraints that multiply the provider grammar state space."""
        if isinstance(value, list):
            return [simplify(item) for item in value]
        if isinstance(value, dict):
            omitted = {"title", "description", "minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"}
            return {key: simplify(item) for key, item in value.items() if key not in omitted}
        return value
    return simplify(Extraction.model_json_schema())


def request_spec(payload: dict, provider: str, *, model: str | None = None, reasoning_effort: str | None = None) -> dict:
    """Credential-free identity includes endpoint, model and every generation parameter."""
    if provider not in {"gemini", "deepseek", "openai"}:
        raise ValueError("unknown_provider")
    if reasoning_effort is not None and (provider != "openai" or reasoning_effort not in {"none", "low", "medium", "high", "xhigh", "max"}):
        raise ValueError("unsupported_reasoning_effort")
    schema_prompt = SYSTEM + "\nJSON schema:\n" + json.dumps(Extraction.model_json_schema(), ensure_ascii=False)
    contents = json.dumps(payload, ensure_ascii=False)
    if provider == "gemini":
        model = model or "gemini-2.5-flash-lite"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        request = {"systemInstruction": {"parts": [{"text": schema_prompt}]}, "contents": [{"role": "user", "parts": [{"text": contents}]}], "generationConfig": {"temperature": 0, "maxOutputTokens": 4096, "responseMimeType": "application/json", "responseJsonSchema": native_schema()}}
    elif provider == "deepseek":
        model = model or "deepseek-v4-flash"
        url = "https://api.deepseek.com/chat/completions"
        request = {"model": model, "messages": [{"role": "system", "content": schema_prompt}, {"role": "user", "content": contents}], "response_format": {"type": "json_object"}, "temperature": 0, "max_tokens": 4096, "thinking": {"type": "disabled"}}
    else:
        model = model or "gpt-6-luna"
        base = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("invalid_openai_base_url")
        url = base + "/chat/completions"
        request = {"model": model, "messages": [{"role": "system", "content": schema_prompt}, {"role": "user", "content": contents}], "response_format": {"type": "json_object"}, "reasoning_effort": reasoning_effort or "low", "max_completion_tokens": 8192}
    return {"provider": provider, "model": model, "url": url, "request": request, "prompt_sha256": hashlib.sha256((schema_prompt + contents).encode()).hexdigest()}


def extract(payload: dict, provider: str, *, model: str | None = None, reasoning_effort: str | None = None) -> tuple[Extraction, dict]:
    """Call only the configured destination once; retries require a new explicit reservation."""
    if provider not in {"gemini", "deepseek", "openai"}:
        raise ValueError("unknown_provider")
    spec = request_spec(payload, provider, model=model, reasoning_effort=reasoning_effort)
    model, url, request = spec["model"], spec["url"], spec["request"]
    if provider == "gemini":
        key = os.environ.get("GEMINI_API_KEY", "")
    elif provider == "openai":
        key = os.environ.get("OPENAI_API_KEY") or os.environ.get("GPT_API_KEY", "")
    else:
        key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("DS_API_KEY", "")
    headers = {"x-goog-api-key": key} if provider == "gemini" else {"Authorization": f"Bearer {key}"}
    if not key:
        raise ValueError("provider_key_missing")
    started = time.monotonic()
    with requests.post(url, headers=headers, json=request, timeout=(10, 90)) as response:
        if response.status_code != 200:
            # Stop after 403/429/5xx instead of retrying outside the paid-call reservation budget.
            # A rate limit is deferred, so there is no immediate retry that could violate Retry-After.
            detail = response.text[:1600].replace(key, "[redacted]")
            detail = re.sub(r"https?://[^\s\"<>]+", "[url]", detail)
            logger.error("structured_http_error provider=%s status=%s detail=%s", provider, response.status_code, detail)
            raise RuntimeError(f"provider_http_{response.status_code}")
        body = response.json()
    if not isinstance(body, dict):
        raise ValueError("provider_invalid_envelope")
    usage = body.get("usageMetadata" if provider == "gemini" else "usage", {})
    resolved = body.get("modelVersion" if provider == "gemini" else "model", model)
    if not isinstance(usage, dict) or not isinstance(resolved, str):
        raise ValueError("provider_invalid_metadata")
    call = {"provider": provider, "model": model, "response_model": resolved, "usage": usage, "elapsed_seconds": round(time.monotonic() - started, 3), "prompt_sha256": spec["prompt_sha256"], "request_sha256": hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
    call["reasoning_effort"] = request.get("reasoning_effort")
    call["raw_response"] = json.loads(json.dumps(body, ensure_ascii=False).replace(key, "[redacted]"))
    try:
        if provider == "gemini":
            candidates = body.get("candidates")
            if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict) or candidates[0].get("finishReason") != "STOP":
                raise ValueError("provider_incomplete_response")
            content = candidates[0].get("content")
            parts = content.get("parts") if isinstance(content, dict) else None
            if not isinstance(parts, list) or any(not isinstance(part, dict) or not isinstance(part.get("text", ""), str) for part in parts):
                raise ValueError("provider_invalid_content")
            output = "".join(part.get("text", "") for part in parts if not part.get("thought"))
        else:
            choices = body.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict) or choices[0].get("finish_reason") != "stop":
                raise ValueError("provider_incomplete_response")
            message = choices[0].get("message")
            if not isinstance(message, dict) or not isinstance(message.get("content"), str):
                raise ValueError("provider_invalid_content")
            output = message["content"]
        return Extraction.model_validate_json(output), call
    except (ValidationError, ValueError) as exc:
        logger.error("structured_output_invalid provider=%s type=%s", provider, type(exc).__name__)
        code = "provider_invalid_extraction" if isinstance(exc, ValidationError) else str(exc)
        if isinstance(exc, ValidationError):
            call["validation_errors"] = [{"loc": list(error["loc"]), "type": error["type"]} for error in exc.errors(include_input=False, include_url=False)]
        # Audit only the model body, never HTTP headers; redact the configured secret.
        call["raw_response"] = json.loads(json.dumps(body, ensure_ascii=False).replace(key, "[redacted]"))
        raise ExtractionError(code, call) from exc
