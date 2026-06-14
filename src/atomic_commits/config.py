"""Configuration resolution for atc.

Priority (implementation.md section 12.3):
1. CLI flags
2. Environment variables
3. Config file (.atc.toml or ~/.config/atc/config.toml)
4. Error with helpful setup instructions

Config files are optional in v1; env vars are sufficient.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ProviderName = Literal["openai-compatible", "anthropic"]

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
ANTHROPIC_BASE_URL = "https://api.anthropic.com"

CONFIG_LOCATIONS = [
    Path(".atc.toml"),
    Path.home() / ".config" / "atc" / "config.toml",
]


@dataclass
class RunConfig:
    """Resolved runtime configuration for a single atc invocation."""

    mode: Literal["compact", "verbose"] = "compact"
    repo: Path = field(default_factory=Path.cwd)
    paths: list[str] = field(default_factory=list)
    include_staged: bool = False
    allow_binary: bool = False

    provider: ProviderName = "openai-compatible"
    model: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    api_key: str | None = None

    max_chunk_tokens: int = 6000
    max_reducer_tokens: int = 4000
    temperature: float = 0.0

    no_verify: bool = False
    yes: bool = False
    json_output: bool = False
    debug: bool = False


def _load_config_file() -> dict[str, Any]:
    for loc in CONFIG_LOCATIONS:
        if loc.is_file():
            try:
                with loc.open("rb") as fh:
                    return tomllib.load(fh)
            except (OSError, tomllib.TOMLDecodeError):
                return {}
    return {}


def resolve_provider_credentials(cfg: RunConfig) -> RunConfig:
    """Fill provider model/base_url/api_key from env/config when not set via CLI."""
    file_cfg = _load_config_file()
    provider_cfg = file_cfg.get(cfg.provider.replace("-", "_"), {}) if file_cfg else {}

    if cfg.provider == "openai-compatible":
        cfg.model = cfg.model or os.getenv("ATC_OPENAI_MODEL") or provider_cfg.get("model")
        cfg.base_url = (
            cfg.base_url
            or os.getenv("ATC_OPENAI_BASE_URL")
            or provider_cfg.get("base_url")
            or DEFAULT_OPENAI_BASE_URL
        )
        env_name = cfg.api_key_env or "ATC_OPENAI_API_KEY"
        cfg.api_key = os.getenv(env_name) or provider_cfg.get("api_key")
    else:  # anthropic
        cfg.model = cfg.model or os.getenv("ATC_ANTHROPIC_MODEL") or provider_cfg.get("model")
        cfg.base_url = cfg.base_url or ANTHROPIC_BASE_URL
        env_name = cfg.api_key_env or "ATC_ANTHROPIC_API_KEY"
        cfg.api_key = os.getenv(env_name) or provider_cfg.get("api_key")

    return cfg
