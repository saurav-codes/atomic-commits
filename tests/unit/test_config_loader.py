"""Tests for the config-file loader.

Covers `resolve_provider_credentials` reading `.atc.toml` / `config.toml`,
including the 1.1 hyphen-vs-underscore table-key fix and the 1.4
malformed-TOML warning path.
"""

import pytest

from atomic_commits.config import (
    ANTHROPIC_BASE_URL,
    DEFAULT_OPENAI_BASE_URL,
    RunConfig,
    resolve_provider_credentials,
)

# Every provider env var that could leak in from the host shell and skew
# config-file resolution. Cleared per-test via an autouse fixture below.
_PROVIDER_ENV = (
    "ATC_OPENAI_MODEL",
    "ATC_OPENAI_BASE_URL",
    "ATC_OPENAI_API_KEY",
    "ATC_ANTHROPIC_MODEL",
    "ATC_ANTHROPIC_BASE_URL",
    "ATC_ANTHROPIC_API_KEY",
)


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch):
    """Ensure no host env vars sway config-file resolution under test."""
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)


def _point_config_at(monkeypatch, path):
    """Replace CONFIG_LOCATIONS with a single temp TOML path."""
    monkeypatch.setattr("atomic_commits.config.CONFIG_LOCATIONS", [path])


def _write_toml(path, body):
    path.write_text(body, encoding="utf-8")


def test_openai_compatible_hyphen_key_read(monkeypatch, tmp_path):
    # 1.1 fix: the documented hyphenated table name must be honored.
    cfg_path = tmp_path / ".atc.toml"
    _write_toml(
        cfg_path,
        "[openai-compatible]\n"
        'model = "gpt-4o"\n'
        'base_url = "https://example.com/v1"\n'
        'api_key = "file-key"\n',
    )
    _point_config_at(monkeypatch, cfg_path)

    cfg = resolve_provider_credentials(RunConfig(provider="openai-compatible"))
    assert cfg.model == "gpt-4o"
    assert cfg.base_url == "https://example.com/v1"
    assert cfg.api_key == "file-key"


def test_openai_compatible_underscore_key_also_works(monkeypatch, tmp_path):
    # The legacy underscore spelling still resolves for backwards compatibility.
    cfg_path = tmp_path / ".atc.toml"
    _write_toml(
        cfg_path,
        "[openai_compatible]\n"
        'model = "gpt-4o-mini"\n'
        'base_url = "https://example.com/v2"\n'
        'api_key = "file-key-2"\n',
    )
    _point_config_at(monkeypatch, cfg_path)

    cfg = resolve_provider_credentials(RunConfig(provider="openai-compatible"))
    assert cfg.model == "gpt-4o-mini"
    assert cfg.base_url == "https://example.com/v2"
    assert cfg.api_key == "file-key-2"


def test_anthropic_table_read(monkeypatch, tmp_path):
    cfg_path = tmp_path / "config.toml"
    _write_toml(
        cfg_path,
        "[anthropic]\n"
        'model = "claude-3-5-sonnet"\n'
        'api_key = "anthro-key"\n',
    )
    _point_config_at(monkeypatch, cfg_path)

    cfg = resolve_provider_credentials(RunConfig(provider="anthropic"))
    assert cfg.model == "claude-3-5-sonnet"
    # Anthropic base_url is hard-coded; the config has no override path for it.
    assert cfg.base_url == ANTHROPIC_BASE_URL
    assert cfg.api_key == "anthro-key"


def test_env_vars_override_config_file(monkeypatch, tmp_path):
    cfg_path = tmp_path / ".atc.toml"
    _write_toml(
        cfg_path,
        "[openai-compatible]\n"
        'model = "file-model"\n'
        'base_url = "https://file.example/v1"\n'
        'api_key = "file-key"\n',
    )
    _point_config_at(monkeypatch, cfg_path)
    monkeypatch.setenv("ATC_OPENAI_MODEL", "env-model")
    monkeypatch.setenv("ATC_OPENAI_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("ATC_OPENAI_API_KEY", "env-key")

    cfg = resolve_provider_credentials(RunConfig(provider="openai-compatible"))
    assert cfg.model == "env-model"
    assert cfg.base_url == "https://env.example/v1"
    assert cfg.api_key == "env-key"


def test_malformed_toml_warns_without_crashing(monkeypatch, tmp_path, capsys):
    # 1.4 fix: a malformed config surfaces a warning on stderr and falls back to
    # defaults rather than crashing or silently behaving as "no config".
    cfg_path = tmp_path / ".atc.toml"
    _write_toml(cfg_path, "[openai-compatible]\nmodel = \n")
    _point_config_at(monkeypatch, cfg_path)

    cfg = resolve_provider_credentials(RunConfig(provider="openai-compatible"))
    # No crash; model stays unset and the default base_url applies.
    assert cfg.model is None
    assert cfg.base_url == DEFAULT_OPENAI_BASE_URL
    assert cfg.api_key is None
    # The parse failure is surfaced on stderr (capsys patches sys.stderr).
    err = capsys.readouterr().err
    assert "warning" in err.lower()
    assert "parse" in err.lower()
