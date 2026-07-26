"""Provider package: AI backends for atc."""

from __future__ import annotations

from ..config import (
    ANTHROPIC_BASE_URL,
    DEFAULT_OPENAI_BASE_URL,
    DEFAULT_PROVIDER_TIMEOUT,
    DEFAULT_RETRY_ATTEMPTS,
    RunConfig,
)
from ..errors import ProviderError
from ..output import live, note
from .anthropic import AnthropicProvider
from .base import AIProvider
from .openai_compatible import OpenAICompatibleProvider

__all__ = ["AIProvider", "AnthropicProvider", "OpenAICompatibleProvider", "build_provider"]


def build_provider(cfg: RunConfig) -> AIProvider:
    """Construct the configured provider, validating required credentials."""
    if not cfg.model:
        raise ProviderError(
            f"no model configured for provider '{cfg.provider}'",
            hint="Set --model or the ATC_*_MODEL env var.",
        )
    if not cfg.api_key:
        raise ProviderError(
            f"no API key found for provider '{cfg.provider}'",
            hint="Set the provider API key env var, or pass --api-key-env.",
        )
    timeout = cfg.provider_timeout if cfg.provider_timeout is not None else DEFAULT_PROVIDER_TIMEOUT
    attempts = cfg.retry_attempts if cfg.retry_attempts is not None else DEFAULT_RETRY_ATTEMPTS

    def progress(message: str) -> None:
        if cfg.json_output:
            return
        live(message)
        if "retry" in message and not message.startswith("model output:"):
            note(f"Provider: {message}", style="yellow")

    if cfg.provider == "anthropic":
        base_url = cfg.base_url or ANTHROPIC_BASE_URL
        return AnthropicProvider(
            api_key=cfg.api_key,
            model=cfg.model,
            base_url=base_url,
            timeout=timeout,
            attempts=attempts,
            progress=progress,
        )
    base_url = cfg.base_url or DEFAULT_OPENAI_BASE_URL
    return OpenAICompatibleProvider(
        api_key=cfg.api_key,
        model=cfg.model,
        base_url=base_url,
        timeout=timeout,
        attempts=attempts,
        progress=progress,
    )
