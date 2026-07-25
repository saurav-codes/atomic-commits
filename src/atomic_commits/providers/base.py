"""Provider interface (implementation.md section 12).

Providers return validated JSON objects. Implementations must extract JSON
robustly and never let the rest of the system trust raw model text.
"""

from __future__ import annotations

import http.client
import json
import random
import re
import threading
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..errors import InvalidAIResponseError, ProviderError

_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}

# Tokens that may be echoed in provider error bodies and must be redacted from
# any hint shown to the user. ``sk-ant-`` is listed before ``sk-`` so the longer
# prefix is matched first.
_SECRET_RE = re.compile(r"Bearer \S+|sk-ant-[A-Za-z0-9_-]+|sk-[A-Za-z0-9_-]+")

# Module-level accumulator for token usage across all provider calls in a run.
# Concrete providers record each response's usage via ``_record_usage``; callers
# (cli/output) read the cumulative total via ``get_total_usage``.
_usage_total: dict[str, Any] = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "estimated_cost_usd": None,
}
_usage_lock = threading.Lock()


def _redact_secrets(text: str) -> str:
    """Replace API keys / bearer tokens that may be echoed in provider error bodies."""
    if not text:
        return text
    return _SECRET_RE.sub("[REDACTED]", text)


def _record_usage(usage: dict[str, Any]) -> None:
    """Accumulate a single (normalized) API response usage dict into the run total."""
    if not usage:
        return
    with _usage_lock:
        _usage_total["prompt_tokens"] += int(usage.get("prompt_tokens", 0) or 0)
        _usage_total["completion_tokens"] += int(usage.get("completion_tokens", 0) or 0)
        _usage_total["total_tokens"] += int(usage.get("total_tokens", 0) or 0)


def get_total_usage() -> dict[str, Any]:
    """Return cumulative token usage across all provider calls in this run."""
    with _usage_lock:
        return dict(_usage_total)


def _jittered_seconds(base: float, *, cap: float | None = None) -> float:
    """Cap ``base`` then apply ±25% random jitter to spread out retry storms."""
    if cap is not None and base > cap:
        base = cap
    return base * random.uniform(0.75, 1.25)


def _retry_after_seconds(exc: HTTPError) -> float | None:
    """Parse the Retry-After header (int seconds or HTTP-date) from an HTTPError."""
    headers = exc.headers
    raw = headers.get("Retry-After") if headers else None
    if not raw:
        return None
    raw = raw.strip()
    try:
        seconds = float(raw)
    except ValueError:
        try:
            dt = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if dt is None:
            return None
        seconds = (dt - datetime.now(dt.tzinfo)).total_seconds()
    if seconds < 0:
        seconds = 0.0
    return _jittered_seconds(seconds, cap=60.0)


class AIProvider(Protocol):
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
        ...


def _post_with_retry(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    *,
    attempts: int = 3,
    timeout: float = 180.0,
) -> str:
    body = json.dumps(payload).encode("utf-8")
    last_error: Exception | None = None
    end_time = time.monotonic() + max(timeout, 0.1)
    # Backoff cap scales with the call timeout so a long reduce call doesn't
    # retry-storm: a 600s-timeout call shouldn't retry after only 2s. Cap at
    # 25% of the timeout, clamped to [8, 120]s.
    backoff_cap = max(8.0, min(120.0, timeout * 0.25))
    for attempt in range(attempts):
        remaining = end_time - time.monotonic()
        if remaining <= 0:
            break
        req = Request(url, data=body, headers={**headers, "Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(req, timeout=remaining) as resp:
                text = resp.read().decode("utf-8")
        except HTTPError as exc:
            if exc.code not in _RETRYABLE_STATUS:
                raise ProviderError(
                    f"provider returned HTTP {exc.code}",
                    hint=_redact_secrets(exc.read()[:300].decode("utf-8", "replace")) or None,
                ) from exc
            last_error = exc
            retry_after = _retry_after_seconds(exc)
            if retry_after is not None:
                sleep_for = retry_after
            else:
                sleep_for = _jittered_seconds(float(min(2**attempt, backoff_cap)))
        except (URLError, TimeoutError, http.client.HTTPException) as exc:
            last_error = exc
            sleep_for = _jittered_seconds(float(min(2**attempt, backoff_cap)))
        else:
            return text
        if attempt + 1 < attempts:
            remaining = end_time - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(sleep_for, remaining))
    raise ProviderError(
        "provider request failed after retries",
        hint=_redact_secrets(str(last_error)) if last_error else None,
    )


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        # Opening fence: "```" optionally followed by a language tag and/or
        # inline content. A pure opener ("```" or "```json") drops the line;
        # an opener sharing its line with JSON ("```json {"a": 1}") keeps the
        # JSON instead of discarding it.
        if re.fullmatch(r"```[A-Za-z0-9+\-.]*", lines[0].strip()):
            lines = lines[1:]
        else:
            lines[0] = re.sub(r"^```[A-Za-z0-9+\-.]*\s*", "", lines[0])
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _candidate_json_objects(text: str) -> list[str]:
    """Return balanced ``{...}`` substrings via a single forward scan.

    Uses a stack of opening-brace positions; each closing brace pops its matching
    opener and records the balanced substring. Results are ordered by opening-brace
    position so outer objects precede their nested children, matching the previous
    (O(n^2)) implementation's behavior.
    """
    found: list[tuple[int, str]] = []
    stack: list[int] = []
    in_string = False
    escape = False
    for idx, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            stack.append(idx)
        elif char == "}" and stack:
            start = stack.pop()
            found.append((start, text[start : idx + 1]))
    found.sort(key=lambda item: item[0])
    return [substr for _, substr in found]


def extract_json(text: str) -> dict[str, Any]:
    """Robustly extract a JSON object from model output."""
    if not isinstance(text, str) or not text.strip():
        raise InvalidAIResponseError(
            "provider did not return JSON",
            hint="Model output was empty. The response may have been filtered, "
            "truncated by max_tokens, or returned as a tool call.",
        )
    text = _strip_code_fence(text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as first_error:
        last_error = first_error
        for candidate in _candidate_json_objects(text):
            try:
                parsed = json.loads(_strip_code_fence(candidate))
                break
            except json.JSONDecodeError as exc:
                last_error = exc
        else:
            if "{" not in text:
                raise InvalidAIResponseError(
                    "provider did not return JSON",
                    hint="Model output could not be parsed as JSON.",
                ) from None
            raise InvalidAIResponseError(f"invalid JSON from provider: {last_error}") from last_error
    if not isinstance(parsed, dict):
        raise InvalidAIResponseError("provider returned non-object JSON")
    return parsed
