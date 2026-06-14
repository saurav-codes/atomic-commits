"""Provider package: AI backends for atc."""

from __future__ import annotations

from ..config import RunConfig
from ..errors import ProviderError
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
    if cfg.provider == "anthropic":
        return AnthropicProvider(
            api_key=cfg.api_key,
            model=cfg.model,
            base_url=cfg.base_url or "https://api.anthropic.com",
        )
    return OpenAICompatibleProvider(
        api_key=cfg.api_key,
        model=cfg.model,
        base_url=cfg.base_url or "https://api.openai.com/v1",
    )
