"""Small OpenAI-compatible JSON chat helper for the selected S02B tasks."""

import os
from urllib.parse import urlsplit

import requests


def chat_completion_json(prompt: str, *, model: str, effort: str, api_key: str = "") -> str:
    """Call an OpenAI-compatible Chat Completions endpoint and return its JSON text."""
    key = api_key or os.getenv("OPENAI_API_KEY") or os.getenv("GPT_API_KEY", "")
    if not key:
        raise RuntimeError("OpenAI key missing: set OPENAI_API_KEY or GPT_API_KEY")

    base = (os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("invalid_openai_base_url")
    if effort not in {"low", "medium", "high"}:
        raise ValueError("invalid_reasoning_effort")

    response = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": model,
            "reasoning_effort": effort,
            "max_completion_tokens": 4096,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=(10, 90),
    )
    if response.status_code != 200:
        raise RuntimeError(f"OpenAI request failed HTTP {response.status_code}")
    body = response.json()
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("provider_invalid_response")
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if choices[0].get("finish_reason") != "stop" or not isinstance(content, str) or not content.strip():
        raise ValueError("provider_incomplete_response")
    return content
