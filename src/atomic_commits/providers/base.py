"""Provider interface (implementation.md section 12).

Providers return validated JSON objects. Implementations must extract JSON
robustly and never let the rest of the system trust raw model text.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from ..errors import InvalidAIResponseError


@runtime_checkable
class AIProvider(Protocol):
    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        ...


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _candidate_json_objects(text: str) -> list[str]:
    candidates: list[str] = []
    for start, char in enumerate(text):
        if char != "{":
            continue
        stack = 0
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            current = text[idx]
            if in_string:
                if escape:
                    escape = False
                elif current == "\\":
                    escape = True
                elif current == '"':
                    in_string = False
                continue

            if current == '"':
                in_string = True
            elif current == "{":
                stack += 1
            elif current == "}":
                stack -= 1
                if stack == 0:
                    candidates.append(text[start : idx + 1])
                    break
    return candidates


def extract_json(text: str) -> dict[str, Any]:
    """Robustly extract a JSON object from model output."""
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
