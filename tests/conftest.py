"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_preset_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep every test away from the user's real ``~/.meg/presets.toml``."""
    path = tmp_path / "presets.toml"
    monkeypatch.setattr("meg.presets.presets_path", lambda: path)
    return path
