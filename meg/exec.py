"""Parse and run ffmpeg/ffprobe commands without a shell."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import re
import shlex
import subprocess
from pathlib import Path


ALLOWED_EXECUTABLES = frozenset({"ffmpeg", "ffprobe"})

_PROGRESS_LINE = re.compile(
    r"^(frame=\s*\d+|size=\s*\d+|time=\S+|speed=\S+|bitrate=\s*\S+)",
    re.IGNORECASE,
)
_ERROR_HINT = re.compile(
    r"(error|invalid|failed|no such file|not found|permission denied|"
    r"does not exist|cannot find|unable to|conversion failed|"
    r"no encoder|unknown encoder|matches no streams)",
    re.IGNORECASE,
)


class CommandValidationError(ValueError):
    """Raised when a command cannot be parsed or is not allowed to run."""


@dataclass(frozen=True)
class ParsedCommand:
    """Shell-free argv plus a readable display string."""

    argv: tuple[str, ...]
    display: str


@dataclass(frozen=True)
class ExecutionResult:
    """Outcome of running ffmpeg or ffprobe."""

    returncode: int
    stderr: str


def _split_posix() -> bool:
    """Use POSIX quoting rules so quoted paths split consistently."""
    return True


def _executable_name(argv0: str) -> str:
    """Return the normalized basename of the executable (without .exe)."""
    name = Path(argv0).name.lower()
    if name.endswith(".exe"):
        return name[:-4]
    return name


def format_argv_display(argv: list[str] | tuple[str, ...]) -> str:
    """Format an argv sequence as a readable command string."""
    return shlex.join(list(argv))


def parse_command_line(command: str) -> ParsedCommand:
    """Split a command string into argv without invoking a shell."""
    stripped = command.strip()
    if not stripped:
        raise CommandValidationError("Command is empty.")

    try:
        argv = shlex.split(stripped, posix=_split_posix())
    except ValueError as exc:
        raise CommandValidationError(f"Could not parse command: {exc}") from exc

    if not argv:
        raise CommandValidationError("Command is empty.")

    return ParsedCommand(argv=tuple(argv), display=format_argv_display(argv))


def validate_allowed_executable(argv: list[str] | tuple[str, ...]) -> None:
    """Ensure the command invokes only ffmpeg or ffprobe."""
    if not argv:
        raise CommandValidationError("Command is empty.")

    exe = _executable_name(argv[0])
    if exe not in ALLOWED_EXECUTABLES:
        raise CommandValidationError(
            f"Only ffmpeg and ffprobe commands can be run (got {argv[0]!r})."
        )


def _stderr_lines(stderr: str) -> list[str]:
    return [line.strip() for line in stderr.splitlines() if line.strip()]


def _looks_like_progress(line: str) -> bool:
    return bool(_PROGRESS_LINE.match(line))


def _looks_like_error(line: str) -> bool:
    return bool(_ERROR_HINT.search(line))


def stderr_tail(stderr: str, *, max_lines: int = 12) -> list[str]:
    """Return the last non-empty stderr lines, excluding progress noise."""
    lines = _stderr_lines(stderr)
    filtered = [line for line in lines if not _looks_like_progress(line)]
    source = filtered if filtered else lines
    return source[-max_lines:]


def summarize_execution_failure(
    result: ExecutionResult,
    *,
    tail_lines: int = 8,
) -> str:
    """Turn captured stderr and exit code into a concise failure message."""
    tail = stderr_tail(result.stderr, max_lines=tail_lines)
    error_lines = [line for line in tail if _looks_like_error(line)]
    if error_lines:
        return error_lines[-1]
    if tail:
        return tail[-1]
    if result.returncode != 0:
        return f"Command exited with code {result.returncode}."
    return "Command failed."


def run_command(
    argv: list[str] | tuple[str, ...],
    *,
    subprocess_run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> ExecutionResult:
    """Run ffmpeg/ffprobe via argv array (no shell). Stdout is inherited."""
    argv_list = list(argv)
    validate_allowed_executable(argv_list)

    run = subprocess_run or subprocess.run
    completed = run(
        argv_list,
        stdout=None,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return ExecutionResult(
        returncode=completed.returncode,
        stderr=completed.stderr or "",
    )
