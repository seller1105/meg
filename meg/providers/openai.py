"""OpenAI (GPT) provider implementation."""

from __future__ import annotations

from openai import OpenAI

from meg.config import DEFAULT_OPENAI_MODEL
from meg.providers.base import AIProvider

DEFAULT_TIMEOUT_S = 60.0


class OpenAIProvider(AIProvider):
    """GPT backend via ``OPENAI_API_KEY``."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_OPENAI_MODEL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        """Create an OpenAI provider client."""
        self._client = OpenAI(api_key=api_key, timeout=timeout_s)
        self._model = model

    def complete(self, system: str, user: str) -> str:
        """Run a GPT completion and return text content only."""
        response = self._client.responses.create(
            model=self._model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        output_text = getattr(response, "output_text", None)
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()
        return str(response)
