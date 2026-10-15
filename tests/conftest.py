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


@pytest.fixture(autouse=True)
def _isolated_ffmpeg_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep every test away from an FFmpeg the user let Meg download (``~/.meg/tools``)."""
    path = tmp_path / "tools" / "ffmpeg"
    monkeypatch.setattr("meg.tools.managed_dir", lambda: path)
    return path
