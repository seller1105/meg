"""Anthropic (Claude) provider implementation."""

from __future__ import annotations

from anthropic import Anthropic

from meg.config import DEFAULT_ANTHROPIC_MODEL
from meg.providers.base import AIProvider

DEFAULT_TIMEOUT_S = 60.0


class AnthropicProvider(AIProvider):
    """Claude backend via ``ANTHROPIC_API_KEY``."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_ANTHROPIC_MODEL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        """Create a Claude provider client."""
        self._client = Anthropic(api_key=api_key, timeout=timeout_s)
        self._model = model

    def complete(self, system: str, user: str) -> str:
        """Run a Claude completion and return text content only."""
        response = self._client.messages.create(
            model=self._model,
            max_tokens=1200,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        parts: list[str] = []
        for block in response.content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
