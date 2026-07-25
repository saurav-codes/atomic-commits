"""Anthropic provider over plain HTTP (implementation.md section 12.2).

Uses the Messages API. Asks for JSON-only output and extracts JSON robustly.
"""

from __future__ import annotations

import json
from typing import Any

from ..errors import ProviderError
from .base import _post_with_retry, _record_usage, extract_json

_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.anthropic.com",
        timeout: float = 180.0,
        attempts: int = 3,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.attempts = attempts

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        max_tokens: int,
        temperature: float,
        stream: bool = False,
        timeout: float | None = None,
        attempts: int | None = None,
    ) -> dict[str, Any]:
        # Streaming is not yet implemented; ``stream`` is accepted for API parity only.
        url = f"{self.base_url}/v1/messages"
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system + "\n\nRespond with a single valid JSON object and nothing else.",
            "messages": [{"role": "user", "content": user}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
        }
        text = _post_with_retry(
            url, payload, headers,
            timeout=timeout if timeout is not None else self.timeout,
            attempts=attempts if attempts is not None else self.attempts,
        )
        data = json.loads(text)
        usage = data.get("usage") if isinstance(data, dict) else None
        if usage:
            prompt = int(usage.get("input_tokens", 0) or 0)
            completion = int(usage.get("output_tokens", 0) or 0)
            _record_usage(
                {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": prompt + completion,
                }
            )
        try:
            blocks = data["content"]
            raw = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            stop_reason = data.get("stop_reason")
        except (KeyError, TypeError) as exc:
            raise ProviderError("unexpected response shape from Anthropic provider") from exc
        if not raw.strip():
            hint = "Model returned empty content."
            if stop_reason == "max_tokens":
                hint = "The model ran out of output space; ATC will use smaller requests on the next run."
            elif stop_reason == "content_filter":
                hint = "Output was blocked by the provider's content filter."
            raise ProviderError(
                f"provider returned empty content (stop_reason: {stop_reason})",
                hint=hint,
            )
        return extract_json(raw)
