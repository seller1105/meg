"""Parse and run ffmpeg/ffprobe commands without a shell."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import os
import re
import shlex
import signal
import subprocess
import threading
import time
from pathlib import Path


ALLOWED_EXECUTABLES = frozenset({"ffmpeg", "ffprobe"})

DEFAULT_EXEC_STALL_TIMEOUT_S = 180.0
EXEC_CANCELLED_RC = 130
EXEC_STALLED_RC = 124
_PROGRESS_ECHO_INTERVAL_S = 2.0
_TERMINATE_GRACE_S = 5.0

_BOOLEAN_FLAGS = frozenset({
    "-y",
    "-n",
    "-xerror",
    "-hide_banner",
    "-nostdin",
    "-nostats",
    "-stats",
    "-benchmark",
    "-copyts",
    "-shortest",
    "-vn",
    "-an",
    "-sn",
    "-dn",
})

_VALUE_FLAGS = frozenset({
    "-i",
    "-c",
    "-codec",
    "-vcodec",
    "-acodec",
    "-scodec",
    "-c:v",
    "-c:a",
    "-c:s",
    "-b:v",
    "-b:a",
    "-b:s",
    "-vf",
    "-af",
    "-filter:v",
    "-filter:a",
    "-filter_complex",
    "-filter_complex_script",
    "-map",
    "-metadata",
    "-preset",
    "-crf",
    "-qscale",
    "-qscale:v",
    "-qscale:a",
    "-ss",
    "-sseof",
    "-to",
    "-t",
    "-f",
    "-r",
    "-pix_fmt",
    "-movflags",
    "-color_primaries",
    "-color_trc",
    "-colorspace",
    "-color_range",
    "-tag",
    "-tag:v",
    "-tag:a",
    "-tag:s",
    "-disposition",
    "-disposition:v",
    "-disposition:a",
    "-disposition:s",
    "-profile",
    "-profile:v",
    "-level",
    "-threads",
    "-max_muxing_queue_size",
    "-passlogfile",
    "-aspect",
    "-video_track_timescale",
    "-attach",
})

# Suffixes that usually indicate a file output target in ffmpeg commands.
_OUTPUT_SUFFIXES = frozenset({
    "aac",
    "avi",
    "flac",
    "m2ts",
    "m4a",
    "m4v",
    "mkv",
    "mov",
    "mp3",
    "mp4",
    "mpeg",
    "mpg",
    "mts",
    "mxf",
    "ogv",
    "ts",
    "vob",
    "wav",
    "webm",
    "wmv",
})

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
class FfmpegSafetyReport:
    """Input/output paths inferred from a simple ffmpeg command."""

    input_paths: tuple[str, ...]
    output_paths: tuple[str, ...]
    existing_outputs: tuple[str, ...]
    has_y_flag: bool
    ambiguous: bool
    colliding_paths: tuple[str, ...]


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
    cancelled: bool = False
    stalled: bool = False


@dataclass
class _ManagedRunState:
    stderr_lines: list[str] = field(default_factory=list)
    last_activity_at: float = 0.0
    latest_progress: str = ""
    last_progress_echo_at: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)


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


def _is_flag(token: str) -> bool:
    return token.startswith("-")


def _flag_consumes_next_arg(token: str, next_token: str | None = None) -> bool:
    if token in _BOOLEAN_FLAGS:
        return False
    if "=" in token:
        return False
    if token in _VALUE_FLAGS:
        return True
    if re.match(r"^-[a-z]+:", token, re.IGNORECASE):
        prefix = token.split(":", 1)[0].lower()
        return prefix in {
            "-c",
            "-b",
            "-q",
            "-filter",
            "-tag",
            "-disposition",
            "-profile",
        }
    if re.match(r"^-(?:x265|x264)-", token, re.IGNORECASE):
        return next_token is not None
    if token in {"-x264opts"}:
        return next_token is not None
    # Most unknown switches take a value; the next token is only skipped when
    # present and not itself another switch.
    if next_token is not None and not next_token.startswith("-"):
        return True
    return False


def _looks_like_output_target(token: str) -> bool:
    """Return True when a bare argv token is likely an output file/path."""
    if token in {"-", "pipe:", "pipe:0", "pipe:1"}:
        return True
    if token.startswith("pipe:"):
        return True
    if "%" in token:
        return True
    if "/" in token or "\\" in token:
        return True
    suffix = Path(token).suffix.lower().lstrip(".")
    return suffix in _OUTPUT_SUFFIXES


def _resolve_output_paths(
    positionals: Sequence[str],
) -> tuple[tuple[str, ...], bool]:
    """Pick the output path(s) from bare argv tokens after option parsing."""
    path_like = [token for token in positionals if _looks_like_output_target(token)]
    if len(path_like) > 1:
        return tuple(path_like), True
    if len(path_like) == 1:
        return (path_like[0],), False
    if positionals:
        return (positionals[-1],), False
    return (), False


def _normalize_path(path: str) -> str:
    """Normalize a filesystem path for safe input/output comparison."""
    candidate = Path(path)
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        resolved = candidate
    return os.path.normcase(os.path.normpath(str(resolved)))


def _is_concrete_output_path(path: str) -> bool:
    """Return True when path refers to a single local file on disk."""
    if path in {"-", "pipe:", "pipe:0", "pipe:1"}:
        return False
    if path.startswith("pipe:"):
        return False
    if "%" in path:
        return False
    return True


def analyze_ffmpeg_safety(argv: Sequence[str]) -> FfmpegSafetyReport | None:
    """Infer ffmpeg input/output paths for simple single-output commands."""
    if not argv or _executable_name(argv[0]) != "ffmpeg":
        return None

    inputs: list[str] = []
    positionals: list[str] = []
    has_y_flag = False
    has_filter_complex = False
    index = 1

    while index < len(argv):
        token = argv[index]
        if token == "-y":
            has_y_flag = True
            index += 1
            continue
        if token == "-i":
            if index + 1 >= len(argv):
                return FfmpegSafetyReport(
                    input_paths=tuple(inputs),
                    output_paths=(),
                    existing_outputs=(),
                    has_y_flag=has_y_flag,
                    ambiguous=True,
                    colliding_paths=(),
                )
            inputs.append(argv[index + 1])
            index += 2
            continue
        if token.startswith("-filter_complex"):
            has_filter_complex = True
        if _is_flag(token):
            next_token = argv[index + 1] if index + 1 < len(argv) else None
            if _flag_consumes_next_arg(token, next_token):
                index += 2
            else:
                index += 1
            continue
        positionals.append(token)
        index += 1

    output_paths, multi_output = _resolve_output_paths(positionals)
    ambiguous = multi_output
    if not output_paths and inputs:
        ambiguous = True
    if has_filter_complex and len([p for p in positionals if _looks_like_output_target(p)]) > 1:
        ambiguous = True

    concrete_outputs = tuple(
        path for path in output_paths if _is_concrete_output_path(path)
    )
    existing_outputs = tuple(
        path
        for path in concrete_outputs
        if Path(path).is_file()
    )

    normalized_inputs = {_normalize_path(path) for path in inputs}
    colliding_paths = tuple(
        path
        for path in concrete_outputs
        if _normalize_path(path) in normalized_inputs
    )

    return FfmpegSafetyReport(
        input_paths=tuple(inputs),
        output_paths=output_paths,
        existing_outputs=existing_outputs,
        has_y_flag=has_y_flag,
        ambiguous=ambiguous,
        colliding_paths=colliding_paths,
    )


def validate_ffmpeg_safety(report: FfmpegSafetyReport) -> None:
    """Reject commands that would clobber sources or bypass overwrite checks."""
    if report.ambiguous:
        raise CommandValidationError(
            "Cannot verify input/output paths for this command. "
            "Use a single output file with one -i input per run, or run it manually."
        )
    if report.colliding_paths:
        collision = report.colliding_paths[0]
        raise CommandValidationError(
            f"Output path matches input ({collision!r}). Refusing to overwrite the source."
        )
    if report.has_y_flag:
        raise CommandValidationError(
            "Command includes -y (overwrite without prompting). "
            "Remove -y; Meg confirms before overwriting existing files."
        )


def prepare_execution_argv(
    argv: Sequence[str],
    *,
    overwrite_confirmed: bool = False,
) -> tuple[str, ...]:
    """Return argv ready to run: strip model -y, inject -y only after user confirms."""
    cleaned = [token for token in argv if token != "-y"]
    if overwrite_confirmed:
        cleaned = [cleaned[0], "-y", *cleaned[1:]]
    return ensure_nostdin(tuple(cleaned))


def ensure_nostdin(argv: Sequence[str]) -> tuple[str, ...]:
    """Ensure ffmpeg runs with -nostdin so it never waits on terminal input."""
    if not argv or _executable_name(argv[0]) != "ffmpeg":
        return tuple(argv)
    if "-nostdin" in argv:
        return tuple(argv)
    return (argv[0], "-nostdin", *argv[1:])


def exec_stall_timeout_s() -> float:
    """Return stall timeout from MEG_EXEC_STALL_TIMEOUT_S or the default."""
    raw = os.getenv("MEG_EXEC_STALL_TIMEOUT_S")
    if raw is None:
        return DEFAULT_EXEC_STALL_TIMEOUT_S
    try:
        value = float(raw.strip())
    except ValueError:
        return DEFAULT_EXEC_STALL_TIMEOUT_S
    if value <= 0:
        return DEFAULT_EXEC_STALL_TIMEOUT_S
    return value


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
    if result.cancelled:
        return "Encode cancelled by user."
    if result.stalled:
        return (
            f"No ffmpeg output for {exec_stall_timeout_s():g}s; "
            "encode stopped (possible hang)."
        )
    tail = stderr_tail(result.stderr, max_lines=tail_lines)
    error_lines = [line for line in tail if _looks_like_error(line)]
    if error_lines:
        return error_lines[-1]
    if tail:
        return tail[-1]
    if result.returncode != 0:
        return f"Command exited with code {result.returncode}."
    return "Command failed."


def _popen_kwargs() -> dict[str, object]:
    """Build kwargs for spawning ffmpeg without a shell."""
    kwargs: dict[str, object] = {
        "stdout": None,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return kwargs


def _terminate_process(proc: subprocess.Popen[str]) -> None:
    """Terminate a running ffmpeg process and its group when possible."""
    if proc.poll() is not None:
        return
    if os.name != "nt":
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            proc.terminate()
    else:
        proc.terminate()
    try:
        proc.wait(timeout=_TERMINATE_GRACE_S)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                proc.kill()
        else:
            proc.kill()
        proc.wait(timeout=_TERMINATE_GRACE_S)


def _stderr_reader(proc: subprocess.Popen[str], state: _ManagedRunState) -> None:
    """Read ffmpeg stderr and track liveness for stall detection."""
    if proc.stderr is None:
        return
    now = time.monotonic()
    with state.lock:
        state.last_activity_at = now
    for line in proc.stderr:
        now = time.monotonic()
        with state.lock:
            state.stderr_lines.append(line)
            state.last_activity_at = now
            stripped = line.strip()
            if stripped and _looks_like_progress(stripped):
                state.latest_progress = stripped


def _maybe_emit_progress(
    state: _ManagedRunState,
    on_progress: Callable[[str], None] | None,
) -> None:
    if on_progress is None:
        return
    now = time.monotonic()
    with state.lock:
        if not state.latest_progress:
            return
        if now - state.last_progress_echo_at < _PROGRESS_ECHO_INTERVAL_S:
            return
        line = state.latest_progress
        state.last_progress_echo_at = now
    on_progress(line)


def _activity_age(state: _ManagedRunState) -> float:
    with state.lock:
        return time.monotonic() - state.last_activity_at


def _build_managed_result(
    proc: subprocess.Popen[str],
    state: _ManagedRunState,
    *,
    cancelled: bool = False,
    stalled: bool = False,
    extra_stderr: str = "",
) -> ExecutionResult:
    with state.lock:
        stderr = "".join(state.stderr_lines)
    if extra_stderr:
        stderr = f"{stderr}{extra_stderr}"
    returncode = proc.returncode if proc.returncode is not None else 1
    if cancelled:
        returncode = EXEC_CANCELLED_RC
    elif stalled:
        returncode = EXEC_STALLED_RC
    return ExecutionResult(
        returncode=returncode,
        stderr=stderr,
        cancelled=cancelled,
        stalled=stalled,
    )


def run_managed_command(
    argv: Sequence[str],
    *,
    stall_timeout_s: float | None = None,
    poll_interval_s: float = 0.5,
    on_progress: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_interrupt: Callable[[], bool] | None = None,
    popen_factory: Callable[..., subprocess.Popen[str]] | None = None,
) -> ExecutionResult:
    """Run ffmpeg with progress monitoring, stall detection, and cancel support."""
    argv_list = list(argv)
    validate_allowed_executable(argv_list)
    stall_limit = stall_timeout_s if stall_timeout_s is not None else exec_stall_timeout_s()
    spawn = popen_factory or subprocess.Popen

    try:
        proc = spawn(argv_list, **_popen_kwargs())
    except FileNotFoundError:
        return ExecutionResult(
            returncode=127,
            stderr=missing_executable_message(argv_list[0]),
        )

    state = _ManagedRunState(last_activity_at=time.monotonic())
    reader = threading.Thread(target=_stderr_reader, args=(proc, state), daemon=True)
    reader.start()

    try:
        while proc.poll() is None:
            if should_cancel is not None and should_cancel():
                _terminate_process(proc)
                proc.wait()
                return _build_managed_result(
                    proc,
                    state,
                    cancelled=True,
                    extra_stderr="\nEncode cancelled by user.\n",
                )

            if _activity_age(state) > stall_limit:
                _terminate_process(proc)
                proc.wait()
                return _build_managed_result(
                    proc,
                    state,
                    stalled=True,
                    extra_stderr=(
                        f"\nNo ffmpeg output for {stall_limit:g}s; "
                        "encode stopped (possible hang).\n"
                    ),
                )

            _maybe_emit_progress(state, on_progress)

            try:
                time.sleep(poll_interval_s)
            except KeyboardInterrupt:
                if on_interrupt is not None and on_interrupt():
                    _terminate_process(proc)
                    proc.wait()
                    return _build_managed_result(
                        proc,
                        state,
                        cancelled=True,
                        extra_stderr="\nEncode cancelled by user.\n",
                    )
    finally:
        reader.join(timeout=1.0)

    return _build_managed_result(proc, state)


def missing_executable_message(argv0: str) -> str:
    """Human-readable message when ffmpeg/ffprobe is not on PATH."""
    name = _executable_name(argv0)
    return (
        f"{name} was not found. Install FFmpeg and ensure {name} is on your PATH."
    )


def run_command(
    argv: list[str] | tuple[str, ...],
    *,
    subprocess_run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> ExecutionResult:
    """Run a short ffmpeg/ffprobe command via argv array (no shell)."""
    argv_list = list(argv)
    validate_allowed_executable(argv_list)

    run = subprocess_run or subprocess.run
    try:
        completed = run(
            argv_list,
            stdout=None,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return ExecutionResult(
            returncode=127,
            stderr=missing_executable_message(argv_list[0]),
        )
    return ExecutionResult(
        returncode=completed.returncode,
        stderr=completed.stderr or "",
    )
