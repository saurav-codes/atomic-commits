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
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import tomli_w

ProviderName = Literal["openai-compatible", "anthropic"]
HookMode = Literal["once", "each", "skip"]

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

    max_chunk_tokens: int = 32000
    max_reducer_tokens: int = 16000
    # Use one globally informed planner call while the complete prompt fits.
    direct_max_tokens: int = 120000
    temperature: float = 0.0

    # Per-request socket timeout for provider HTTP calls, in seconds. The
    # reduce/synthesis phase auto-scales this upward for large plans (see
    # planner._reduce_once), so this is the floor, not a hard ceiling.
    provider_timeout: float = 180.0

    no_verify: bool = False
    hook_mode: HookMode = "each"
    yes: bool = False
    json_output: bool = False
    debug: bool = False

    # Optional commit-message template; the planner fills ${scope}, ${verb}, ${object}.
    message_template: str | None = None

    # Caps the planner's per-run thread pool (2.3). None lets the planner pick.
    max_parallel: int | None = None

    # Per-request retry count for provider HTTP calls (1.5). Default matches the
    # prior hardcoded value so existing behavior is unchanged.
    retry_attempts: int = 3

    # Total planning budget. Completed work is cached when this expires.
    deadline: float = 600.0

    # One review call tries to split every valid plan as far as meaning allows.
    review_plan: bool = True


def _load_config_file() -> dict[str, Any]:
    for loc in CONFIG_LOCATIONS:
        if loc.is_file():
            try:
                with loc.open("rb") as fh:
                    return tomllib.load(fh)
            except OSError:
                # File unreadable (permissions, IO error) — not a user typo; stay quiet.
                return {}
            except tomllib.TOMLDecodeError as exc:
                # A malformed config is a user typo; surface it so the silent fall-back
                # to "no config" doesn't hide the problem.
                print(f"atc: warning: failed to parse config file {loc}: {exc}", file=sys.stderr)
                return {}
    return {}


def resolve_provider_credentials(cfg: RunConfig) -> RunConfig:
    """Fill provider model/base_url/api_key from env/config when not set via CLI."""
    file_cfg = _load_config_file()
    # Try the documented hyphenated key first (e.g. "openai-compatible"), then fall
    # back to the underscore form (e.g. "openai_compatible") so both spellings work.
    provider_cfg = (
        file_cfg.get(cfg.provider, file_cfg.get(cfg.provider.replace("-", "_"), {}))
        if file_cfg
        else {}
    )

    if cfg.provider == "openai-compatible":
        cfg.model = cfg.model or os.getenv("ATC_OPENAI_MODEL") or provider_cfg.get("model")
        cfg.base_url = (
            cfg.base_url
            or os.getenv("ATC_OPENAI_BASE_URL")
            or provider_cfg.get("base_url")
            or DEFAULT_OPENAI_BASE_URL
        )
        # Read api_key_env from the config file before computing the env var name
        # so the documented `[openai-compatible].api_key_env` knob actually takes.
        cfg.api_key_env = cfg.api_key_env or provider_cfg.get("api_key_env")
        env_name = cfg.api_key_env or "ATC_OPENAI_API_KEY"
        cfg.api_key = os.getenv(env_name) or provider_cfg.get("api_key")
    else:  # anthropic
        cfg.model = cfg.model or os.getenv("ATC_ANTHROPIC_MODEL") or provider_cfg.get("model")
        # Honor the documented `[anthropic].base_url` override; CLI flag and env
        # var still win, then the file, then the hard-coded default.
        cfg.base_url = (
            cfg.base_url
            or os.getenv("ATC_ANTHROPIC_BASE_URL")
            or provider_cfg.get("base_url")
            or ANTHROPIC_BASE_URL
        )
        cfg.api_key_env = cfg.api_key_env or provider_cfg.get("api_key_env")
        env_name = cfg.api_key_env or "ATC_ANTHROPIC_API_KEY"
        cfg.api_key = os.getenv(env_name) or provider_cfg.get("api_key")

    # The optional commit-message template is documented for both providers;
    # only fill it from the file when the caller hasn't already set one.
    cfg.message_template = cfg.message_template or provider_cfg.get("message_template")

    # Per-request timeout (seconds). CLI flag wins, then env, then config file,
    # then the dataclass default (180s). Applies to every provider HTTP call;
    # the planner auto-scales it upward for large reduce/synthesis passes.
    if cfg.provider_timeout == 180.0:
        env_timeout = os.getenv("ATC_PROVIDER_TIMEOUT")
        file_timeout = provider_cfg.get("timeout")
        if env_timeout is not None:
            try:
                cfg.provider_timeout = float(env_timeout)
            except ValueError:
                pass
        elif file_timeout is not None:
            try:
                cfg.provider_timeout = float(file_timeout)
            except (TypeError, ValueError):
                pass

    # Retry attempts: env/config override only when the caller left the default.
    if cfg.retry_attempts == 3:
        env_attempts = os.getenv("ATC_RETRY_ATTEMPTS")
        file_attempts = provider_cfg.get("retry_attempts")
        if env_attempts is not None:
            try:
                cfg.retry_attempts = int(env_attempts)
            except ValueError:
                pass
        elif file_attempts is not None:
            try:
                cfg.retry_attempts = int(file_attempts)
            except (TypeError, ValueError):
                pass

    if cfg.direct_max_tokens == 120000:
        value = os.getenv("ATC_FULL_CHANGE_LIMIT") or provider_cfg.get("full_change_limit")
        if value is not None:
            try:
                cfg.direct_max_tokens = int(value)
            except (TypeError, ValueError):
                pass

    if cfg.deadline == 600.0:
        value = os.getenv("ATC_TIME_LIMIT") or provider_cfg.get("time_limit")
        if value is not None:
            try:
                cfg.deadline = float(value)
            except (TypeError, ValueError):
                pass

    review = os.getenv("ATC_REVIEW")
    if review is not None:
        cfg.review_plan = review.strip().lower() not in {"0", "false", "no", "off"}

    return cfg


def init_config(
    provider: str,
    model: str,
    *,
    api_key_env: str | None = None,
    base_url: str | None = None,
    path: Path | None = None,
) -> Path:
    """Write a minimal TOML config file and return its path.

    Writes a ``[<provider>]`` table with ``model`` and, when supplied, ``base_url``
    and ``api_key_env``. The parent directory is created (``parents=True``) and the
    file is written with mode ``0o600`` so it is not world-readable. Pure writer --
    does not prompt or interact.
    """
    target = path if path is not None else Path.home() / ".config" / "atc" / "config.toml"
    target.parent.mkdir(parents=True, exist_ok=True)
    table: dict[str, Any] = {"model": model}
    if base_url is not None:
        table["base_url"] = base_url
    if api_key_env is not None:
        table["api_key_env"] = api_key_env
    target.write_bytes(tomli_w.dumps({provider: table}).encode("utf-8"))
    os.chmod(target, 0o600)
    return target
