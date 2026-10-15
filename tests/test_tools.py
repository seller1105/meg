"""Tests for finding ffmpeg/ffprobe: PATH first, then Meg's downloaded copy."""

from __future__ import annotations

from pathlib import Path

import pytest

from meg import tools
from meg.exec import EXEC_MISSING_RC, run_command
from meg.ffprobe import run_ffprobe


@pytest.fixture
def no_path_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend nothing is on PATH."""
    monkeypatch.setattr("meg.tools.shutil.which", lambda name: None)


def _install_managed(managed: Path, *names: str) -> dict[str, str]:
    bin_dir = managed / "bin"
    bin_dir.mkdir(parents=True)
    paths = {}
    for name in names:
        path = bin_dir / tools.executable_filename(name)
        path.write_bytes(b"")
        paths[name] = str(path)
    return paths


def test_find_tool_prefers_the_users_own_install(monkeypatch, _isolated_ffmpeg_tools) -> None:
    _install_managed(_isolated_ffmpeg_tools, "ffmpeg")
    monkeypatch.setattr("meg.tools.shutil.which", lambda name: f"/usr/bin/{name}")
    assert tools.find_tool("ffmpeg") == "/usr/bin/ffmpeg"


def test_find_tool_falls_back_to_megs_copy(no_path_tools, _isolated_ffmpeg_tools) -> None:
    paths = _install_managed(_isolated_ffmpeg_tools, "ffmpeg", "ffprobe")
    assert tools.find_tool("ffmpeg") == paths["ffmpeg"]
    assert tools.find_tool("ffprobe") == paths["ffprobe"]


def test_find_tool_is_none_when_neither_exists(no_path_tools) -> None:
    assert tools.find_tool("ffmpeg") is None


def test_resolve_executable_uses_megs_copy_only_for_bare_missing_names(
    no_path_tools, _isolated_ffmpeg_tools
) -> None:
    paths = _install_managed(_isolated_ffmpeg_tools, "ffmpeg")
    assert tools.resolve_executable("ffmpeg") == paths["ffmpeg"]
    assert tools.resolve_executable("FFMPEG.EXE") == paths["ffmpeg"]
    # An explicit path is the user's choice; a missing ffprobe stays missing.
    assert tools.resolve_executable(r"C:\tools\ffmpeg.exe") == r"C:\tools\ffmpeg.exe"
    assert tools.resolve_executable("/opt/ffmpeg") == "/opt/ffmpeg"
    assert tools.resolve_executable("ffprobe") == "ffprobe"
    assert tools.resolve_executable("rm") == "rm"


def test_resolve_executable_leaves_path_installs_alone(monkeypatch, _isolated_ffmpeg_tools) -> None:
    _install_managed(_isolated_ffmpeg_tools, "ffmpeg")
    monkeypatch.setattr("meg.tools.shutil.which", lambda name: f"/usr/bin/{name}")
    assert tools.resolve_executable("ffmpeg") == "ffmpeg"


def test_run_command_starts_megs_copy(no_path_tools, _isolated_ffmpeg_tools) -> None:
    paths = _install_managed(_isolated_ffmpeg_tools, "ffmpeg")
    seen: list[list[str]] = []

    def fake_run(argv, **kwargs):
        seen.append(argv)
        raise FileNotFoundError

    result = run_command(["ffmpeg", "-version"], subprocess_run=fake_run)

    assert seen == [[paths["ffmpeg"], "-version"]]
    # Messages still name the tool, not Meg's internal path.
    assert result.returncode == EXEC_MISSING_RC
    assert result.stderr.startswith("ffmpeg was not found")


def test_run_ffprobe_starts_megs_copy(monkeypatch, no_path_tools, _isolated_ffmpeg_tools) -> None:
    paths = _install_managed(_isolated_ffmpeg_tools, "ffprobe")
    seen: list[list[str]] = []

    class Done:
        returncode = 0
        stdout = "{}"
        stderr = ""

    def fake_run(argv, **kwargs):
        seen.append(argv)
        return Done()

    monkeypatch.setattr("meg.ffprobe.subprocess.run", fake_run)
    run_ffprobe("clip.mov")
    assert seen[0][0] == paths["ffprobe"]
