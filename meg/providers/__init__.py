"""AI provider factory and exports."""

from __future__ import annotations

from meg.config import ConfigError, MegConfig, ProviderName, resolve_model, resolve_provider_name
from meg.providers.anthropic import AnthropicProvider
from meg.providers.base import AIProvider
from meg.providers.openai import OpenAIProvider



def create_provider(
    config: MegConfig,
    override: str | None = None,
    model_override: str | None = None,
) -> AIProvider:
    """Build a provider instance based on config and optional overrides."""
    provider_name: ProviderName = resolve_provider_name(config, override)
    model = resolve_model(config, provider_name, override=model_override)

    if provider_name == "anthropic":
        if not config.anthropic_api_key:
            raise ConfigError("ANTHROPIC_API_KEY is required for Anthropic provider.")
        return AnthropicProvider(api_key=config.anthropic_api_key, model=model)

    if not config.openai_api_key:
        raise ConfigError("OPENAI_API_KEY is required for OpenAI provider.")
    return OpenAIProvider(api_key=config.openai_api_key, model=model)


__all__ = ["AIProvider", "create_provider"]
