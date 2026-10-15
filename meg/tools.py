"""Find ffmpeg and ffprobe: the user's own install first, then Meg's copy.

Meg never ships FFmpeg. A front end may download a build into
``managed_bin_dir()`` (``~/.meg/tools/ffmpeg/bin``); everything that runs
ffmpeg or ffprobe resolves the program here, so that copy is used whenever
PATH has none. A user who installs FFmpeg themselves always wins.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path, PureWindowsPath

TOOL_NAMES = ("ffmpeg", "ffprobe")


def managed_dir() -> Path:
    """Folder that holds the FFmpeg build Meg downloaded, if any."""
    return Path.home() / ".meg" / "tools" / "ffmpeg"


def managed_bin_dir() -> Path:
    """Folder with the downloaded ``ffmpeg`` and ``ffprobe`` executables."""
    return managed_dir() / "bin"


def executable_filename(name: str) -> str:
    """The file name of a tool on this OS (``ffmpeg.exe`` on Windows)."""
    return f"{name}.exe" if os.name == "nt" else name


def _managed_tool(name: str) -> str | None:
    candidate = managed_bin_dir() / executable_filename(name)
    return str(candidate) if candidate.is_file() else None


def find_tool(name: str) -> str | None:
    """Full path to ``ffmpeg`` or ``ffprobe``, or None when neither copy exists."""
    return shutil.which(name) or _managed_tool(name)


def resolve_executable(argv0: str) -> str:
    """The program to start for ``argv0``.

    A bare ``ffmpeg`` / ``ffprobe`` that is not on PATH becomes Meg's copy
    when one is installed. Anything else, including an explicit path, is
    returned unchanged so the OS reports a missing program as before.
    """
    if PureWindowsPath(argv0).name != argv0 or "/" in argv0:
        return argv0
    if shutil.which(argv0):
        return argv0
    name = argv0.lower()
    if name.endswith(".exe"):
        name = name[:-4]
    if name not in TOOL_NAMES:
        return argv0
    return _managed_tool(name) or argv0
