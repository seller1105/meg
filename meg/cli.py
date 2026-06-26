"""Typer CLI — entry point for the ``meg`` command."""

from __future__ import annotations

import re
import shutil
import sys
import threading
from typing import Callable, NoReturn, Optional

import typer
from meg.config import ConfigError, load_config
from meg.exec import (
    CommandValidationError,
    ExecutionResult,
    FfmpegSafetyReport,
    analyze_ffmpeg_safety,
    exec_stall_timeout_s,
    format_argv_display,
    parse_command_line,
    prepare_execution_argv,
    run_command,
    run_managed_command,
    stderr_tail,
    summarize_execution_failure,
    validate_allowed_executable,
    validate_ffmpeg_safety,
)
from meg.ffprobe import (
    build_source_context,
    extract_ffmpeg_input_paths,
    extract_media_paths,
    probe_media_summary,
)
from meg.prompt import (
    PromptParseError,
    build_explain_prompt,
    build_generate_prompt,
    build_revise_prompt,
    parse_explain_response,
    parse_generate_response,
)
from meg.providers import create_provider
from meg.providers.base import AIProvider

app = typer.Typer(
    name="meg",
    help="AI-powered FFmpeg assistant for the terminal.",
    no_args_is_help=True,
)

_read_line: Callable[[], str] = input

_FFMPEG_PROGRESS_FIELD = re.compile(
    r"(?:frame=\s*(?P<frame>\d+)|fps=\s*(?P<fps>[\d.]+)|time=(?P<time>[\d:.]+)|"
    r"speed=\s*(?P<speed>\S+)|bitrate=\s*(?P<bitrate>\S+))",
    re.IGNORECASE,
)


def _stdout_is_interactive() -> bool:
    isatty = getattr(sys.stdout, "isatty", None)
    return bool(isatty and isatty())


def _executable_basename(argv0: str) -> str:
    from pathlib import Path

    name = Path(argv0).name.lower()
    if name.endswith(".exe"):
        return name[:-4]
    return name


def _configure_terminal_utf8() -> None:
    """Prefer UTF-8 on stdout/stderr (avoids cp1252 crashes on Windows)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def _echo(text: str) -> None:
    """Write user-facing output with UTF-8-safe fallback."""
    try:
        typer.echo(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))


def _format_provider_error(exc: Exception) -> str:
    """Turn provider exceptions into actionable CLI messages."""
    message = str(exc).strip()
    name = exc.__class__.__name__
    lowered = message.lower()

    if "timeout" in lowered or name in {
        "APITimeoutError",
        "Timeout",
        "TimeoutError",
        "ReadTimeout",
    }:
        return "Request timed out. Check your network and retry."
    if (
        "401" in message
        or "authentication" in lowered
        or "invalid api key" in lowered
        or "incorrect api key" in lowered
    ):
        return "API authentication failed. Verify your API key is valid."
    if "429" in message or "rate limit" in lowered:
        return "Rate limit reached. Wait a moment and retry."
    if any(token in lowered for token in ("connection", "network", "connect")):
        return f"Network error contacting the API. Check connectivity and retry. ({message})"
    if message:
        return f"Provider request failed: {message}"
    return "Provider request failed. Check your API key, network, and retry."


def _exit_provider_error(exc: Exception) -> NoReturn:
    typer.secho(_format_provider_error(exc), fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1) from exc


def _stdin_is_interactive() -> bool:
    """True when Meg can prompt for run/edit/exit."""
    isatty = getattr(sys.stdin, "isatty", None)
    return bool(isatty and isatty())


def _prompt_run_edit_exit() -> str:
    """Return ``run``, ``edit``, or ``exit`` from the post-explain menu."""
    _echo("")
    _echo("[r]un  [e]dit  [q]uit")
    while True:
        try:
            raw = _read_line().strip().lower()
        except EOFError:
            return "exit"
        if raw in {"r", "run"}:
            return "run"
        if raw in {"e", "edit"}:
            return "edit"
        if raw in {"q", "quit", "exit"}:
            return "exit"
        _echo("Enter r (run), e (edit), or q (quit).")


def _prompt_revision_feedback() -> str:
    """Read non-empty feedback for command revision."""
    _echo("")
    _echo("What should change?")
    while True:
        try:
            feedback = _read_line().strip()
        except EOFError:
            raise typer.Exit(code=0)
        if feedback:
            return feedback
        _echo("Feedback must not be empty.")


def _prompt_show_stderr_tail(tail: list[str]) -> None:
    """Offer the raw stderr tail after a failed run."""
    _echo("")
    _echo("Show stderr tail? [y/N]")
    try:
        answer = _read_line().strip().lower()
    except EOFError:
        return
    if answer not in {"y", "yes"}:
        return
    _echo("")
    for line in tail:
        _echo(line)


def _prompt_cancel_encode() -> bool:
    """Confirm an interrupt-driven cancel request."""
    _echo("")
    _echo("Cancel encode? [y/N]")
    try:
        answer = _read_line().strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def _start_cancel_listener(cancel_event: threading.Event) -> threading.Thread:
    """Watch stdin for ``q``/``quit`` while an encode is running."""

    def listen() -> None:
        while not cancel_event.is_set():
            try:
                line = _read_line()
            except EOFError:
                return
            if line.strip().lower() in {"q", "quit"}:
                cancel_event.set()
                return

    thread = threading.Thread(target=listen, daemon=True)
    thread.start()
    return thread


def _echo_line(text: str) -> None:
    """Write a single line of user-facing output."""
    _echo(text)


def _write_tty_status(text: str) -> None:
    """Overwrite the current terminal line with a live status."""
    try:
        width = shutil.get_terminal_size(fallback=(80, 24)).columns
    except OSError:
        width = 80
    clipped = text[:width]
    padding = " " * max(0, width - len(clipped))
    try:
        sys.stdout.write(f"\r{clipped}{padding}")
        sys.stdout.flush()
    except UnicodeEncodeError:
        sys.stdout.buffer.write(f"\r{clipped}{padding}".encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()


def _parse_ffmpeg_time_value(value: str) -> float | None:
    """Parse ffmpeg ``time=`` values like 00:01:23.45 into seconds."""
    parts = value.split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        if len(parts) == 2:
            minutes, seconds = parts
            return int(minutes) * 60 + float(seconds)
    except ValueError:
        return None
    return None


def _format_clock(seconds: float) -> str:
    """Format seconds as M:SS.mmm or H:MM:SS.mmm."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    if hours:
        return f"{hours}:{minutes:02d}:{secs:06.3f}"
    return f"{minutes}:{secs:06.3f}"


def _format_progress_status(
    line: str,
    *,
    duration_seconds: float | None = None,
) -> str:
    """Turn an ffmpeg stderr progress line into a short status string."""
    fields: dict[str, str] = {}
    for match in _FFMPEG_PROGRESS_FIELD.finditer(line):
        for key, value in match.groupdict().items():
            if value is not None:
                fields[key] = value

    parts = ["Encoding…"]
    if "time" in fields:
        current = _format_clock(_parse_ffmpeg_time_value(fields["time"]) or 0.0)
        if duration_seconds is not None and duration_seconds > 0:
            total = _format_clock(duration_seconds)
            parts.append(f"{current} / {total}")
        else:
            parts.append(f"time={fields['time']}")
    if "speed" in fields:
        parts.append(f"speed={fields['speed']}")
    if "frame" in fields:
        parts.append(f"frame={fields['frame']}")
    if "fps" in fields:
        parts.append(f"fps={fields['fps']}")
    if "bitrate" in fields:
        parts.append(f"bitrate={fields['bitrate']}")

    if len(parts) == 1:
        return line.strip()
    return " ".join(parts)


class _LiveProgressDisplay:
    """Render ffmpeg progress inline on a TTY or as periodic lines elsewhere."""

    def __init__(
        self,
        *,
        duration_seconds: float | None,
        use_tty: bool,
    ) -> None:
        self._duration_seconds = duration_seconds
        self._use_tty = use_tty
        self._active = False

    def start(self) -> None:
        self._active = self._use_tty

    def update(self, line: str) -> None:
        status = _format_progress_status(
            line,
            duration_seconds=self._duration_seconds,
        )
        if self._active:
            _write_tty_status(status)
        else:
            _echo_line(status)

    def finish(self) -> None:
        if not self._active:
            return
        sys.stdout.write("\n")
        sys.stdout.flush()
        self._active = False


def _source_duration_seconds(input_paths: tuple[str, ...]) -> float | None:
    """Return probed duration for the first known source input path."""
    for path in input_paths:
        summary = probe_media_summary(path)
        if summary is not None and summary.duration_seconds is not None:
            return summary.duration_seconds
    return None


def _warn_incomplete_output(
    safety: FfmpegSafetyReport | None,
    result: ExecutionResult,
) -> None:
    if not (result.cancelled or result.stalled):
        return
    if safety is None:
        return
    for path in safety.output_paths:
        if path in {"-", "pipe:", "pipe:0", "pipe:1"} or path.startswith("pipe:"):
            continue
        if "%" in path:
            continue
        _echo(f"Output may be incomplete: {path}")


def _run_approved_argv(
    argv: tuple[str, ...],
    *,
    interactive: bool,
    source_duration_seconds: float | None = None,
) -> ExecutionResult:
    if _executable_basename(argv[0]) != "ffmpeg":
        return run_command(argv)

    cancel_event = threading.Event()
    listener: threading.Thread | None = None
    progress = _LiveProgressDisplay(
        duration_seconds=source_duration_seconds,
        use_tty=interactive and _stdout_is_interactive(),
    )
    if interactive:
        _echo("Press q to cancel, Ctrl+C to interrupt.")
        listener = _start_cancel_listener(cancel_event)

    progress.start()
    try:
        return run_managed_command(
            argv,
            stall_timeout_s=exec_stall_timeout_s(),
            on_progress=progress.update if interactive else None,
            should_cancel=(lambda: cancel_event.is_set()) if interactive else None,
            on_interrupt=_prompt_cancel_encode if interactive else None,
        )
    finally:
        progress.finish()
        if listener is not None:
            cancel_event.set()
            listener.join(timeout=0.2)


def _prompt_confirm_run(command: str, *, existing_outputs: tuple[str, ...] = ()) -> bool:
    """Ask the user to approve running this specific command."""
    _echo("")
    _echo("Run this command?")
    _echo(command)
    if existing_outputs:
        _echo("")
        for path in existing_outputs:
            _echo(f"Warning: {path} already exists and will be overwritten.")
    _echo("")
    _echo("[y]es  [n]o")
    while True:
        try:
            raw = _read_line().strip().lower()
        except EOFError:
            return False
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no", ""}:
            return False
        _echo("Enter y (yes) or n (no).")


def _execute_approved_command(command: str) -> bool:
    """Parse, validate, and run an approved ffmpeg/ffprobe command.

    Returns True on success. On validation or execution failure, prints a
    message and returns False so the user can edit or retry.
    """
    safety = None
    try:
        parsed = parse_command_line(command)
        validate_allowed_executable(parsed.argv)
        safety = analyze_ffmpeg_safety(parsed.argv)
        if safety is not None:
            validate_ffmpeg_safety(safety)
    except CommandValidationError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        return False

    existing_outputs: tuple[str, ...] = ()
    if safety is not None:
        existing_outputs = safety.existing_outputs
        if existing_outputs and not _stdin_is_interactive():
            typer.secho(
                "Output file already exists; refusing to run without confirmation.",
                fg=typer.colors.RED,
                err=True,
            )
            return False

    if not _prompt_confirm_run(command, existing_outputs=existing_outputs):
        return False

    argv = prepare_execution_argv(
        parsed.argv,
        overwrite_confirmed=bool(existing_outputs),
    )

    _echo("")
    _echo(f"Running: {format_argv_display(argv)}")

    source_duration = (
        _source_duration_seconds(safety.input_paths)
        if safety is not None
        else None
    )
    result = _run_approved_argv(
        argv,
        interactive=_stdin_is_interactive(),
        source_duration_seconds=source_duration,
    )
    if result.returncode == 0:
        return True

    _warn_incomplete_output(safety, result)
    summary = summarize_execution_failure(result)
    typer.secho(
        f"Command failed (exit {result.returncode}): {summary}",
        fg=typer.colors.RED,
        err=True,
    )
    if _stdin_is_interactive():
        _prompt_show_stderr_tail(stderr_tail(result.stderr))
    return False


def _generate_once(
    ai_provider: AIProvider,
    *,
    request: str,
    verbose: bool,
    source_context: str | None,
    previous_command: str | None = None,
    feedback: str | None = None,
) -> tuple[str, str]:
    """Call the model once for initial generate or a revision."""
    if previous_command is not None and feedback is not None:
        prompt = build_revise_prompt(
            request=request,
            previous_command=previous_command,
            feedback=feedback,
            verbose=verbose,
            source_context=source_context,
        )
    else:
        prompt = build_generate_prompt(
            request=request,
            verbose=verbose,
            source_context=source_context,
        )
    raw_response = ai_provider.complete(prompt.system, prompt.user)
    parsed = parse_generate_response(raw_response)
    return parsed.command, parsed.explanation


def _run_generate_confirm_loop(
    ai_provider: AIProvider,
    *,
    request: str,
    verbose: bool,
    source_context: str | None,
) -> None:
    """Generate → explain → run | edit | exit until the user is done."""
    previous_command: str | None = None
    feedback: str | None = None

    while True:
        try:
            command, explanation = _generate_once(
                ai_provider,
                request=request,
                verbose=verbose,
                source_context=source_context,
                previous_command=previous_command,
                feedback=feedback,
            )
        except PromptParseError as exc:
            typer.secho(
                f"Could not parse model output: {exc}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1) from exc
        except Exception as exc:
            _exit_provider_error(exc)

        _echo(command)
        _echo("")
        _echo(explanation)

        if not _stdin_is_interactive():
            return

        while True:
            choice = _prompt_run_edit_exit()
            if choice == "exit":
                return
            if choice == "edit":
                break
            if choice == "run":
                if _execute_approved_command(command):
                    return
                continue

        feedback = _prompt_revision_feedback()
        previous_command = command


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    request: Optional[str] = typer.Argument(
        None,
        help="Plain-English description of the FFmpeg operation you want.",
    ),
    explain: Optional[str] = typer.Option(
        None,
        "--explain",
        help="Explain an existing FFmpeg command.",
    ),
    provider: Optional[str] = typer.Option(
        None,
        "--provider",
        help="AI provider: anthropic or openai.",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help="Model ID for the selected provider (overrides config defaults).",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="Request a longer explanation with more detail on codecs, filters, and mapping.",
    ),
) -> None:
    """Generate or explain FFmpeg commands from plain English."""
    _configure_terminal_utf8()

    if ctx.invoked_subcommand is not None:
        return

    if explain is not None and request is not None:
        typer.secho(
            "Use either a generate request or --explain, not both.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    if explain is not None:
        if not explain.strip():
            typer.secho(
                "Explain input must not be empty. Provide an FFmpeg command.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1)
        if "ffmpeg" not in explain.lower():
            typer.secho(
                "Explain input should include an ffmpeg command.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1)

    if request is not None and not request.strip():
        typer.secho(
            "Request must not be empty. Provide a plain-English FFmpeg operation.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    if explain is None and request is None:
        return

    try:
        config = load_config()
        ai_provider = create_provider(
            config,
            override=provider,
            model_override=model,
        )
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    if explain is not None:
        try:
            source_context = build_source_context(extract_ffmpeg_input_paths(explain))
            prompt = build_explain_prompt(
                command=explain,
                verbose=verbose,
                source_context=source_context,
            )
            raw_response = ai_provider.complete(prompt.system, prompt.user)
            parsed = parse_explain_response(raw_response)
        except PromptParseError as exc:
            typer.secho(
                f"Could not parse model output: {exc}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1) from exc
        except Exception as exc:
            _exit_provider_error(exc)

        _echo(parsed.explanation)
        return

    if request is not None:
        source_context = build_source_context(extract_media_paths(request))
        try:
            _run_generate_confirm_loop(
                ai_provider,
                request=request,
                verbose=verbose,
                source_context=source_context,
            )
        except typer.Exit:
            raise
        except Exception as exc:
            _exit_provider_error(exc)
