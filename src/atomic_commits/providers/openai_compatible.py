"""OpenAI-compatible provider over plain HTTP (implementation.md section 12.1).

Uses httpx directly (no SDK) so any OpenAI-compatible endpoint works. Requests
JSON object output and retries transient errors with backoff.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ..errors import ProviderError
from .base import extract_json

_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


class OpenAICompatibleProvider:
    def __init__(self, *, api_key: str, model: str, base_url: str, timeout: float = 60.0) -> None:
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
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
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
                            return data["choices"][0]["message"]["content"]
                        except (KeyError, IndexError, TypeError) as exc:
                            raise ProviderError(
                                "unexpected response shape from OpenAI-compatible provider"
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
