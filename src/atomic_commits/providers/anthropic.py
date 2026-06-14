"""Anthropic provider over plain HTTP (implementation.md section 12.2).

Uses the Messages API. Asks for JSON-only output and extracts JSON robustly.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ..errors import ProviderError
from .base import extract_json

_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}
_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider:
    def __init__(self, *, api_key: str, model: str, base_url: str = "https://api.anthropic.com", timeout: float = 60.0) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
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
            "Content-Type": "application/json",
        }
        text = await self._post_with_retry(url, payload, headers)
        return extract_json(text)

    async def _post_with_retry(
        self, url: str, payload: dict[str, Any], headers: dict[str, str], attempts: int = 3
    ) -> str:
        last_error: Exception | None = None
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(attempts):
                try:
                    resp = await client.post(url, json=payload, headers=headers)
                except httpx.HTTPError as exc:
                    last_error = exc
                else:
                    if resp.status_code == 200:
                        data = resp.json()
                        try:
                            blocks = data["content"]
                            return "".join(
                                b.get("text", "") for b in blocks if b.get("type") == "text"
                            )
                        except (KeyError, TypeError) as exc:
                            raise ProviderError(
                                "unexpected response shape from Anthropic provider"
                            ) from exc
                    if resp.status_code not in _RETRYABLE_STATUS:
                        raise ProviderError(
                            f"provider returned HTTP {resp.status_code}",
                            hint=resp.text[:300] or None,
                        )
                    last_error = ProviderError(f"HTTP {resp.status_code}")
                await asyncio.sleep(min(2**attempt, 8))
        raise ProviderError(
            "provider request failed after retries",
            hint=str(last_error) if last_error else None,
        )
