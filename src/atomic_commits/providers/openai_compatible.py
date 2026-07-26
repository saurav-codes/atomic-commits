"""OpenAI-compatible provider over plain HTTP (implementation.md section 12.1).

Uses urllib.request directly (no SDK) so any OpenAI-compatible endpoint works.
Requests JSON object output and retries transient errors with backoff.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from ..errors import ProviderError
from .base import _post_with_retry, _record_usage, extract_json

_CONTEXT_LIMIT = re.compile(
    r"maximum context length is (\d+) tokens.*?requested (\d+) output tokens.*?"
    r"prompt contains at least (\d+) input tokens",
    re.IGNORECASE | re.DOTALL,
)


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
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
        timeout: float | None = None,
        attempts: int | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        payload = {
            "model": self.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": messages,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
        }
        request_timeout = timeout if timeout is not None else self.timeout
        request_attempts = attempts if attempts is not None else self.attempts
        end_time = time.monotonic() + request_timeout
        current_max = max_tokens

        def post() -> dict[str, Any]:
            nonlocal current_max
            for _ in range(3):
                try:
                    text = _post_with_retry(
                        url, payload, headers,
                        timeout=max(0.1, end_time - time.monotonic()),
                        attempts=request_attempts,
                    )
                    break
                except ProviderError as exc:
                    match = _CONTEXT_LIMIT.search(exc.hint or "")
                    if match is None:
                        raise
                    context_limit, _requested, input_tokens = map(int, match.groups())
                    safety = min(1024, max(128, context_limit // 100))
                    available = context_limit - input_tokens - safety
                    if available < 1 or available >= current_max:
                        raise
                    current_max = available
                    payload["max_tokens"] = available
            else:
                raise ProviderError("provider context budget could not be fitted automatically")
            data = json.loads(text)
            usage = data.get("usage") if isinstance(data, dict) else None
            if usage:
                prompt = int(usage.get("prompt_tokens", 0) or 0)
                completion = int(usage.get("completion_tokens", 0) or 0)
                total = int(usage.get("total_tokens", 0) or 0) or (prompt + completion)
                _record_usage({
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": total,
                })
            return data

        for output_attempt in range(3):
            data = post()
            try:
                choice = data["choices"][0]
                raw = choice["message"]["content"]
                finish_reason = choice.get("finish_reason")
            except (KeyError, IndexError, TypeError) as exc:
                raise ProviderError(
                    "unexpected response shape from OpenAI-compatible provider"
                ) from exc
            if finish_reason == "length" and output_attempt < 2:
                current_max = min(current_max * 2, 65536)
                payload["max_tokens"] = current_max
                continue
            if finish_reason == "length":
                raise ProviderError(
                    "provider truncated its JSON response after ATC retried automatically"
                )
            if isinstance(raw, str) and raw.strip():
                return extract_json(raw)
            if finish_reason in {None, "stop"} and output_attempt < 2:
                messages[-1]["content"] += (
                    "\n\nYour previous response was empty. Return the required JSON object now."
                )
                continue
            hint = "Model returned empty content."
            if finish_reason == "content_filter":
                hint = "Output was blocked by the provider's content filter."
            elif finish_reason == "tool_calls":
                hint = "Model returned a tool call instead of JSON content."
            raise ProviderError(
                f"provider returned empty content (finish_reason: {finish_reason})", hint=hint,
            )
        raise AssertionError("unreachable")
