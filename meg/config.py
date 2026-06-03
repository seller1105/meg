"""Configuration loading and provider selection."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import tomllib

ProviderName = Literal["anthropic", "openai"]
VALID_PROVIDERS: tuple[ProviderName, ProviderName] = ("anthropic", "openai")

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-5"
DEFAULT_OPENAI_MODEL = "gpt-5"


class ConfigError(ValueError):
    """Raised when Meg configuration is missing or invalid."""


@dataclass(frozen=True)
class MegConfig:
    """Runtime configuration loaded from env vars and optional TOML."""

    anthropic_api_key: str | None
    openai_api_key: str | None
    default_provider: ProviderName | None
    anthropic_model: str = DEFAULT_ANTHROPIC_MODEL
    openai_model: str = DEFAULT_OPENAI_MODEL



def _read_config_file(config_path: Path) -> dict[str, Any]:
    """Load a TOML config file if it exists; return an empty dict otherwise."""
    if not config_path.exists():
        return {}
    with config_path.open("rb") as handle:
        loaded = tomllib.load(handle)
    if not isinstance(loaded, dict):
        raise ConfigError(f"Invalid config structure in '{config_path}'.")
    return loaded



def _normalize_provider_name(provider_name: str | None) -> ProviderName | None:
    """Validate and normalize provider names to a supported value."""
    if provider_name is None:
        return None
    normalized = provider_name.strip().lower()
    if normalized in VALID_PROVIDERS:
        return normalized  # type: ignore[return-value]
    supported = ", ".join(VALID_PROVIDERS)
    raise ConfigError(f"Unknown provider '{provider_name}'. Supported values: {supported}.")



def load_config(config_path: Path | None = None) -> MegConfig:
    """Load Meg configuration from env vars and optional TOML.

    Environment variables always override values from the config file.
    """
    path = config_path or Path.home() / ".meg" / "config.toml"
    file_config = _read_config_file(path)

    file_anthropic = file_config.get("anthropic_api_key")
    file_openai = file_config.get("openai_api_key")
    file_provider = file_config.get("provider")
    file_anthropic_model = file_config.get("anthropic_model")
    file_openai_model = file_config.get("openai_model")

    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY") or file_anthropic
    openai_api_key = os.getenv("OPENAI_API_KEY") or file_openai
    anthropic_model = (
        os.getenv("MEG_ANTHROPIC_MODEL") or file_anthropic_model or DEFAULT_ANTHROPIC_MODEL
    )
    openai_model = os.getenv("MEG_OPENAI_MODEL") or file_openai_model or DEFAULT_OPENAI_MODEL

    if anthropic_api_key is not None and not isinstance(anthropic_api_key, str):
        raise ConfigError("'anthropic_api_key' must be a string.")
    if openai_api_key is not None and not isinstance(openai_api_key, str):
        raise ConfigError("'openai_api_key' must be a string.")
    if not isinstance(anthropic_model, str) or not anthropic_model.strip():
        raise ConfigError("'anthropic_model' must be a non-empty string.")
    if not isinstance(openai_model, str) or not openai_model.strip():
        raise ConfigError("'openai_model' must be a non-empty string.")

    anthropic_model = anthropic_model.strip()
    openai_model = openai_model.strip()

    env_provider = os.getenv("MEG_PROVIDER")
    if env_provider is not None:
        default_provider = _normalize_provider_name(env_provider)
    elif isinstance(file_provider, str):
        default_provider = _normalize_provider_name(file_provider)
    elif file_provider is None:
        default_provider = None
    else:
        raise ConfigError("'provider' in config.toml must be a string.")

    return MegConfig(
        anthropic_api_key=anthropic_api_key,
        openai_api_key=openai_api_key,
        default_provider=default_provider,
        anthropic_model=anthropic_model,
        openai_model=openai_model,
    )



def resolve_provider_name(config: MegConfig, override: str | None = None) -> ProviderName:
    """Resolve provider using override, configured default, then key availability.

    Resolution order:
    1) CLI override
    2) Configured default provider
    3) Key-based auto-detection (Anthropic preferred if both keys exist)
    """
    requested = _normalize_provider_name(override) if override else config.default_provider

    if requested == "anthropic":
        if config.anthropic_api_key:
            return "anthropic"
        raise ConfigError(
            "Provider 'anthropic' selected but ANTHROPIC_API_KEY is missing."
        )
    if requested == "openai":
        if config.openai_api_key:
            return "openai"
        raise ConfigError("Provider 'openai' selected but OPENAI_API_KEY is missing.")

    if config.anthropic_api_key:
        return "anthropic"
    if config.openai_api_key:
        return "openai"

    raise ConfigError(
        "No API key found. Set ANTHROPIC_API_KEY or OPENAI_API_KEY."
    )


def resolve_model(
    config: MegConfig,
    provider_name: ProviderName,
    override: str | None = None,
) -> str:
    """Resolve model for the active provider.

    Resolution order:
    1) CLI override
    2) Configured provider-specific model (env or config.toml)
    3) Built-in default for that provider
    """
    if override is not None:
        model = override.strip()
        if not model:
            raise ConfigError("Model name must not be empty.")
        return model

    if provider_name == "anthropic":
        return config.anthropic_model
    return config.openai_model
