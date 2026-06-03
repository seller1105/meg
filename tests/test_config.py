"""Tests for configuration loading and provider selection."""

from __future__ import annotations

from pathlib import Path

import pytest

from meg.config import ConfigError, MegConfig, load_config, resolve_model, resolve_provider_name



def test_auto_detect_prefers_anthropic_when_both_keys_present() -> None:
    config = MegConfig(
        anthropic_api_key="anthropic-key",
        openai_api_key="openai-key",
        default_provider=None,
    )
    assert resolve_provider_name(config) == "anthropic"



def test_auto_detect_falls_back_to_openai() -> None:
    config = MegConfig(
        anthropic_api_key=None,
        openai_api_key="openai-key",
        default_provider=None,
    )
    assert resolve_provider_name(config) == "openai"



def test_override_provider_requires_matching_key() -> None:
    config = MegConfig(
        anthropic_api_key="anthropic-key",
        openai_api_key=None,
        default_provider=None,
    )
    with pytest.raises(ConfigError):
        resolve_provider_name(config, override="openai")



def test_missing_keys_raises_clear_error() -> None:
    config = MegConfig(
        anthropic_api_key=None,
        openai_api_key=None,
        default_provider=None,
    )
    with pytest.raises(ConfigError, match="No API key found"):
        resolve_provider_name(config)



def test_load_config_prefers_env_over_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "\n".join(
            [
                'anthropic_api_key = "file-anthropic"',
                'openai_api_key = "file-openai"',
                'provider = "openai"',
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-anthropic")
    monkeypatch.setenv("MEG_PROVIDER", "anthropic")

    config = load_config(config_file)

    assert config.anthropic_api_key == "env-anthropic"
    assert config.openai_api_key == "file-openai"
    assert config.default_provider == "anthropic"


def test_load_config_reads_models_from_file_and_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "\n".join(
            [
                'anthropic_model = "file-claude"',
                'openai_model = "file-gpt"',
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("MEG_OPENAI_MODEL", "env-gpt")

    config = load_config(config_file)

    assert config.anthropic_model == "file-claude"
    assert config.openai_model == "env-gpt"


def test_resolve_model_prefers_cli_override() -> None:
    config = MegConfig(
        anthropic_api_key="key",
        openai_api_key=None,
        default_provider=None,
        anthropic_model="config-claude",
    )
    assert resolve_model(config, "anthropic", override="cli-claude") == "cli-claude"


def test_resolve_model_uses_provider_specific_config() -> None:
    config = MegConfig(
        anthropic_api_key="anthropic-key",
        openai_api_key="openai-key",
        default_provider=None,
        anthropic_model="config-claude",
        openai_model="config-gpt",
    )
    assert resolve_model(config, "anthropic") == "config-claude"
    assert resolve_model(config, "openai") == "config-gpt"


def test_resolve_model_rejects_empty_override() -> None:
    config = MegConfig(
        anthropic_api_key="key",
        openai_api_key=None,
        default_provider=None,
    )
    with pytest.raises(ConfigError, match="must not be empty"):
        resolve_model(config, "anthropic", override="   ")
