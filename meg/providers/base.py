"""Abstract interface for AI providers."""

from __future__ import annotations

from abc import ABC, abstractmethod


class AIProvider(ABC):
    """Contract for Claude and GPT backends."""

    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        """Send a prompt and return the model response text."""
        ...
