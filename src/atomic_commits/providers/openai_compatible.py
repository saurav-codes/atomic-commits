"""Streaming OpenAI-compatible provider using the official OpenAI client."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any

from openai import APIConnectionError, APIStatusError, OpenAI, OpenAIError

from ..errors import InvalidAIResponseError, ProviderError
from .base import _jittered_seconds, _record_usage, _redact_secrets, extract_json

_CONTEXT_LIMIT = re.compile(
    r"maximum context length is (\d+) tokens.*?requested (\d+) output tokens.*?"
    r"prompt contains at least (\d+) input tokens",
    re.IGNORECASE | re.DOTALL,
)
_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


def _response_preview(text: str, limit: int = 240) -> str:
    """Return a compact rolling preview suitable for one live terminal line."""
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return "…" + compact[-(limit - 1) :].lstrip()


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float = 180.0,
        attempts: int = 3,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        self.model = model
        self.timeout = timeout
        self.attempts = attempts
        self.progress = progress
        self.client = OpenAI(api_key=api_key, base_url=base_url.rstrip("/"), max_retries=0)

    def _progress(self, message: str) -> None:
        if self.progress:
            self.progress(message)

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
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        request_timeout = timeout if timeout is not None else self.timeout
        request_attempts = attempts if attempts is not None else self.attempts
        end_time = time.monotonic() + request_timeout
        current_max = max_tokens
        include_usage = True

        def stream_once() -> tuple[str, str | None]:
            nonlocal include_usage
            last_error: Exception | None = None
            attempt = 0
            while attempt < request_attempts:
                remaining = end_time - time.monotonic()
                if remaining <= 0:
                    break
                self._progress(
                    f"request attempt {attempt + 1}/{request_attempts} sent; "
                    "waiting for the first stream chunk"
                )
                params: dict[str, Any] = {
                    "model": self.model,
                    "temperature": temperature,
                    "max_tokens": current_max,
                    "response_format": {"type": "json_object"},
                    "messages": messages,
                    "stream": True,
                }
                if include_usage:
                    params["stream_options"] = {"include_usage": True}
                try:
                    stream = self.client.with_options(timeout=remaining).chat.completions.create(
                        **params
                    )
                    parts: list[str] = []
                    preview_source = ""
                    finish_reason: str | None = None
                    usage = None
                    with stream:
                        for chunk in stream:
                            if chunk.usage is not None:
                                usage = chunk.usage
                            if chunk.choices:
                                choice = chunk.choices[0]
                                if choice.delta.content:
                                    parts.append(choice.delta.content)
                                    preview_source = (preview_source + choice.delta.content)[-480:]
                                finish_reason = choice.finish_reason or finish_reason
                            preview = _response_preview(preview_source)
                            if preview:
                                self._progress(f"model output: {preview}")
                except APIStatusError as exc:
                    body = json.dumps(exc.body, default=str)
                    if exc.status_code == 400 and include_usage and "stream_options" in body:
                        include_usage = False
                        self._progress(
                            "provider does not expose streamed usage; continuing without it"
                        )
                        continue
                    if exc.status_code not in _RETRYABLE_STATUS:
                        raise ProviderError(
                            f"provider returned HTTP {exc.status_code}",
                            hint=_redact_secrets(body[:300]) or None,
                        ) from exc
                    last_error = exc
                except APIConnectionError as exc:
                    last_error = exc
                except OpenAIError as exc:
                    raise ProviderError(
                        "provider stream failed", hint=_redact_secrets(str(exc)) or None
                    ) from exc
                else:
                    if usage is not None:
                        _record_usage(
                            {
                                "prompt_tokens": usage.prompt_tokens,
                                "completion_tokens": usage.completion_tokens,
                                "total_tokens": usage.total_tokens,
                            }
                        )
                    raw = "".join(parts)
                    self._progress("stream complete; decoding model response")
                    return raw, finish_reason

                attempt += 1
                if attempt < request_attempts:
                    remaining = end_time - time.monotonic()
                    if remaining <= 0:
                        break
                    delay = min(_jittered_seconds(float(2**attempt)), remaining)
                    self._progress(
                        f"stream interrupted; retrying in {delay:.1f}s "
                        f"({attempt + 1}/{request_attempts})"
                    )
                    time.sleep(delay)
            raise ProviderError(
                "provider stream failed after retries",
                hint=_redact_secrets(str(last_error)) if last_error else None,
            )

        for output_attempt in range(3):
            for _ in range(3):
                try:
                    raw, finish_reason = stream_once()
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
                    self._progress(
                        f"context limit detected; output budget reduced to {available:,}"
                    )
            else:
                raise ProviderError("provider context budget could not be fitted automatically")

            if finish_reason == "length" and output_attempt < 2:
                current_max = min(current_max * 2, 65536)
                self._progress(
                    f"output was truncated; retrying with {current_max:,} output tokens"
                )
                continue
            if finish_reason == "length":
                raise ProviderError(
                    "provider truncated its JSON response after ATC retried automatically"
                )
            if raw.strip():
                try:
                    return extract_json(raw)
                except InvalidAIResponseError:
                    if output_attempt >= 2:
                        raise
                    self._progress("model returned invalid JSON; retrying automatically")
                    messages[-1]["content"] += (
                        "\n\nYour previous response was invalid JSON. Return one complete, valid "
                        "JSON object now."
                    )
                    continue
            if finish_reason in {None, "stop"} and output_attempt < 2:
                self._progress("model returned empty output; retrying automatically")
                messages[-1]["content"] += (
                    "\n\nYour previous response was empty. Return the required JSON object now."
                )
                continue
            raise ProviderError(
                f"provider returned empty content (finish_reason: {finish_reason})",
                hint="Model returned empty content.",
            )
        raise AssertionError("unreachable")
