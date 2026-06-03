"""Tests for provider factory wiring."""

from __future__ import annotations

from unittest.mock import patch

from meg.config import MegConfig
from meg.providers import create_provider


def test_create_provider_passes_resolved_anthropic_model() -> None:
    config = MegConfig(
        anthropic_api_key="anthropic-key",
        openai_api_key=None,
        default_provider=None,
        anthropic_model="config-claude",
    )

    with patch("meg.providers.AnthropicProvider") as provider_cls:
        create_provider(config)

    provider_cls.assert_called_once_with(
        api_key="anthropic-key",
        model="config-claude",
    )


def test_create_provider_passes_cli_model_override() -> None:
    config = MegConfig(
        anthropic_api_key="anthropic-key",
        openai_api_key=None,
        default_provider=None,
        anthropic_model="config-claude",
    )

    with patch("meg.providers.AnthropicProvider") as provider_cls:
        create_provider(config, model_override="cli-claude")

    provider_cls.assert_called_once_with(
        api_key="anthropic-key",
        model="cli-claude",
    )


def test_create_provider_passes_resolved_openai_model() -> None:
    config = MegConfig(
        anthropic_api_key=None,
        openai_api_key="openai-key",
        default_provider=None,
        openai_model="config-gpt",
    )

    with patch("meg.providers.OpenAIProvider") as provider_cls:
        create_provider(config)

    provider_cls.assert_called_once_with(
        api_key="openai-key",
        model="config-gpt",
    )
