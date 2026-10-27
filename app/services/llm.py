from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.logging_config import RunTrace
from app.services.http_client import Budget, ProviderError, request_json

_JSON_BLOCK = re.compile(r"\{.*\}", re.S)
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def parse_json_object(provider: str, text: str) -> dict[str, Any]:
    cleaned = _THINK.sub("", text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned[cleaned.find("{") :] if "{" in cleaned else cleaned
    match = _JSON_BLOCK.search(cleaned)
    if not match:
        raise ProviderError(provider, "invalid_response", f"{provider} did not return a JSON object")
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError as error:
        raise ProviderError(provider, "invalid_response", f"{provider} returned malformed JSON") from error
    if not isinstance(value, dict):
        raise ProviderError(provider, "invalid_response", f"{provider} returned JSON that is not an object")
    return value


async def chat_completion_json(
    client: httpx.AsyncClient,
    *,
    provider: str,
    url: str,
    headers: dict[str, str],
    model: str,
    system: str,
    user: str,
    budget: Budget,
    timeout: float,
    retries: int,
    trace: RunTrace,
    stage: str,
    max_tokens: int = 1800,
) -> tuple[dict[str, Any], str]:
    """OpenAI-compatible non-streaming chat call that must return a JSON object. Returns (object, actual model id)."""
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
    }
    trace.emit(stage, "provider_request", "started", provider=provider, model=model, prompt_chars=len(system) + len(user))
    try:
        result = await request_json(client, provider, "POST", url, budget=budget, timeout=timeout, retries=retries, headers=headers, json_body=payload)
    except ProviderError as error:
        trace.emit(stage, "provider_request", "failed", provider=provider, model=model, error_code=error.code, message=str(error), retryable=error.retryable)
        raise
    body = result.body if isinstance(result.body, dict) else {}
    actual_model = str(body.get("model") or "unreported_by_provider")
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        trace.emit(stage, "provider_request", "failed", provider=provider, model=actual_model, error_code="invalid_response", duration_ms=result.duration_ms)
        raise ProviderError(provider, "invalid_response", f"{provider} response had no message content") from error
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    trace.emit(
        stage,
        "provider_request",
        "ok",
        provider=provider,
        model=actual_model,
        duration_ms=result.duration_ms,
        requested_model=model,
        attempts=result.attempts,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
    )
    try:
        parsed = parse_json_object(provider, content if isinstance(content, str) else "")
    except ProviderError as error:
        trace.emit(stage, "provider_output_invalid", "failed", provider=provider, model=actual_model, message=str(error))
        raise
    return parsed, actual_model
