"""Typer CLI — entry point for the ``meg`` command."""

from __future__ import annotations

import re
import shutil
import sys
import threading
from dataclasses import dataclass
from typing import Callable, NoReturn, Optional

import typer
from meg import ui
from meg.config import ConfigError, load_config, load_env_files
from meg.exec import (
    CommandValidationError,
    _is_concrete_output_path,
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
    build_interpret_prompt,
    build_revise_prompt,
    parse_explain_response,
    parse_generate_response,
    parse_interpret_response,
)
from meg.preflight import (
    PreflightFinding,
    has_preflight_errors,
    preflight_from_command,
)
from meg.presets import (
    Preset,
    PresetError,
    add_preset,
    delete_preset,
    get_preset,
    load_presets,
    render_preset_command,
    search_presets,
)
from meg.providers import create_provider
from meg.providers.base import AIProvider

class _MegGroup(typer.core.TyperGroup):
    """Give subcommands priority over the optional [REQUEST] argument.

    Without this, ``meg preset list`` parses ``preset`` as the generate
    request instead of routing to the subcommand.
    """

    def parse_args(self, ctx: typer.Context, args: list[str]) -> list[str]:
        if args and args[0] in self.commands:
            original = self.params
            self.params = [p for p in original if p.name != "request"]
            try:
                result = super().parse_args(ctx, args)
            finally:
                self.params = original
            ctx.params.setdefault("request", None)
            return result
        return super().parse_args(ctx, args)


app = typer.Typer(
    name="meg",
    help="AI-powered FFmpeg assistant for the terminal.",
    no_args_is_help=False,
    cls=_MegGroup,
)

preset_app = typer.Typer(
    name="preset",
    help="Save, search, and reuse ffmpeg commands from your preset vault.",
    no_args_is_help=True,
)
app.add_typer(preset_app)

_read_line: Callable[[], str] = input


@dataclass(frozen=True)
class _FailedRun:
    """Details of the most recent execution failure, for the fix flow."""

    command: str
    returncode: int
    stderr_excerpt: str


_last_failed_run: Optional[_FailedRun] = None

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


def _show_command(command: str) -> None:
    """Display a generated command: Rich panel on a TTY, plain line otherwise."""
    if ui.rich_active():
        ui.print_command_panel(command)
    else:
        _echo(command)


def _print_preset_collection(presets: list[Preset]) -> None:
    """Display presets: Rich table on a TTY, plain lines otherwise."""
    if ui.rich_active():
        ui.print_preset_table(
            [(p.nickname, p.description, p.display) for p in presets]
        )
        return
    for preset in presets:
        _echo(_format_preset_line(preset))


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
    """Return ``run``, ``edit``, ``save``, or ``exit`` from the post-explain menu."""
    _echo("")
    _echo("[r]un  [e]dit  [s]ave preset  [q]uit")
    while True:
        try:
            raw = _read_line().strip().lower()
        except EOFError:
            return "exit"
        if raw in {"r", "run"}:
            return "run"
        if raw in {"e", "edit"}:
            return "edit"
        if raw in {"s", "save"}:
            return "save"
        if raw in {"q", "quit", "exit"}:
            return "exit"
        _echo("Enter r (run), e (edit), s (save preset), or q (quit).")


def _prompt_fix_edit_exit() -> str:
    """Return ``fix``, ``edit``, or ``exit`` from the post-failure menu."""
    _echo("")
    _echo("[f]ix with AI  [e]dit  [q]uit")
    while True:
        try:
            raw = _read_line().strip().lower()
        except EOFError:
            return "exit"
        if raw in {"f", "fix"}:
            return "fix"
        if raw in {"e", "edit"}:
            return "edit"
        if raw in {"q", "quit", "exit"}:
            return "exit"
        _echo("Enter f (fix with AI), e (edit), or q (quit).")


def _prompt_save_preset(command: str) -> None:
    """Interactively save the current command to the preset vault."""
    _echo("")
    _echo("Nickname for this preset (blank to cancel):")
    try:
        nickname = _read_line().strip()
    except EOFError:
        return
    if not nickname:
        return
    _echo("Optional description (blank for none):")
    try:
        description = _read_line().strip()
    except EOFError:
        description = ""
    try:
        preset = add_preset(nickname, command, description=description)
    except PresetError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        return
    _echo(f"Saved preset '{preset.nickname}': {preset.display}")
    _echo(f"Reuse it with: meg preset run {preset.nickname} <input-file>")


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


def _extract_progress_fields(line: str) -> dict[str, str]:
    """Pull frame/fps/time/speed/bitrate tokens from an ffmpeg stderr line."""
    fields: dict[str, str] = {}
    for match in _FFMPEG_PROGRESS_FIELD.finditer(line):
        for key, value in match.groupdict().items():
            if value is not None:
                fields[key] = value
    return fields


def _format_progress_status(
    line: str,
    *,
    duration_seconds: float | None = None,
) -> str:
    """Turn an ffmpeg stderr progress line into a short status string."""
    fields = _extract_progress_fields(line)

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
        self._rich: ui.EncodeProgress | None = None

    def start(self) -> None:
        self._active = self._use_tty
        if self._use_tty and ui.rich_active():
            self._rich = ui.EncodeProgress(self._duration_seconds)
            self._rich.start()

    def update(self, line: str) -> None:
        if self._rich is not None:
            fields = _extract_progress_fields(line)
            if not fields:
                return
            seconds = (
                _parse_ffmpeg_time_value(fields["time"])
                if "time" in fields
                else None
            )
            detail_parts: list[str] = []
            if self._duration_seconds is None and "time" in fields:
                detail_parts.append(f"time={fields['time']}")
            for key in ("speed", "fps", "bitrate"):
                if key in fields:
                    detail_parts.append(f"{key}={fields[key]}")
            self._rich.update(seconds, " ".join(detail_parts))
            return
        status = _format_progress_status(
            line,
            duration_seconds=self._duration_seconds,
        )
        if self._active:
            _write_tty_status(status)
        else:
            _echo_line(status)

    def finish(self) -> None:
        if self._rich is not None:
            self._rich.finish()
            self._rich = None
            self._active = False
            return
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
    allow_stdin_cancel: bool = True,
) -> ExecutionResult:
    """Run an approved argv.

    ``allow_stdin_cancel`` must be False inside the REPL: the q-to-cancel
    listener blocks on stdin and would otherwise swallow the next line the
    user types at the ``meg>`` prompt after the encode finishes.
    """
    if _executable_basename(argv[0]) != "ffmpeg":
        return run_command(argv)

    cancel_event = threading.Event()
    listener: threading.Thread | None = None
    progress = _LiveProgressDisplay(
        duration_seconds=source_duration_seconds,
        use_tty=interactive and _stdout_is_interactive(),
    )
    use_listener = interactive and allow_stdin_cancel
    if use_listener:
        _echo("Press q to cancel, Ctrl+C to interrupt.")
        listener = _start_cancel_listener(cancel_event)
    elif interactive:
        _echo("Press Ctrl+C to interrupt.")

    progress.start()
    try:
        return run_managed_command(
            argv,
            stall_timeout_s=exec_stall_timeout_s(),
            on_progress=progress.update if interactive else None,
            should_cancel=(lambda: cancel_event.is_set()) if use_listener else None,
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


def _print_preflight_findings(findings: tuple[PreflightFinding, ...]) -> None:
    """Show pre-flight warnings and errors after a generated command."""
    if not findings:
        return
    _echo("")
    _echo("Pre-flight:")
    for finding in findings:
        if finding.severity == "error":
            typer.secho(f"  Error: {finding.message}", fg=typer.colors.RED, err=True)
        else:
            typer.secho(f"  Warning: {finding.message}", fg=typer.colors.YELLOW)


def _execute_approved_command(
    command: str,
    *,
    user_request: str | None = None,
    probed_paths: list[str] | None = None,
    allow_stdin_cancel: bool = True,
) -> bool:
    """Parse, validate, and run an approved ffmpeg/ffprobe command.

    Returns True on success. On validation or execution failure, prints a
    message and returns False so the user can edit or retry. Execution
    failures (as opposed to declined approvals or validation errors) are
    recorded in ``_last_failed_run`` for the AI fix flow.
    """
    global _last_failed_run
    _last_failed_run = None
    preflight = preflight_from_command(
        command,
        user_request=user_request,
        probed_paths=probed_paths,
    )
    if has_preflight_errors(preflight):
        typer.secho(
            "Pre-flight errors must be fixed before running. Choose edit to revise the command.",
            fg=typer.colors.RED,
            err=True,
        )
        return False

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
        allow_stdin_cancel=allow_stdin_cancel,
    )
    if result.returncode == 0:
        done_paths = [
            path
            for path in (safety.output_paths if safety is not None else ())
            if _is_concrete_output_path(path)
        ]
        if done_paths:
            for path in done_paths:
                _echo(f"Done: {path}")
        else:
            _echo("Done.")
        return True

    _warn_incomplete_output(safety, result)
    summary = summarize_execution_failure(result)
    typer.secho(
        f"Command failed (exit {result.returncode}): {summary}",
        fg=typer.colors.RED,
        err=True,
    )
    if not (result.cancelled or result.stalled):
        _last_failed_run = _FailedRun(
            command=command,
            returncode=result.returncode,
            stderr_excerpt="\n".join(stderr_tail(result.stderr)),
        )
    if _stdin_is_interactive():
        _prompt_show_stderr_tail(stderr_tail(result.stderr))
    return False


def _interpret_failure(
    ai_provider: AIProvider,
    *,
    failed: _FailedRun,
    request: str | None,
    source_context: str | None,
):
    """Ask the model to diagnose a failed run; print the diagnosis.

    Returns the InterpretedFailure (command may be None when no command
    change can fix it), or None when the model call itself failed — in both
    cases the caller should return to the menu.
    """
    prompt = build_interpret_prompt(
        failed.command,
        failed.stderr_excerpt,
        request=request,
        source_context=source_context,
    )
    try:
        with ui.thinking("Diagnosing failure…"):
            raw_response = ai_provider.complete(prompt.system, prompt.user)
        parsed = parse_interpret_response(raw_response)
    except PromptParseError as exc:
        typer.secho(
            f"Could not parse model output: {exc}",
            fg=typer.colors.RED,
            err=True,
        )
        return None
    except Exception as exc:
        typer.secho(_format_provider_error(exc), fg=typer.colors.RED, err=True)
        return None

    _echo("")
    _echo(parsed.diagnosis)
    if parsed.command is None:
        _echo("")
        _echo("No command change can fix this failure; see the diagnosis above.")
    return parsed


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
        label = "Revising command…"
    else:
        prompt = build_generate_prompt(
            request=request,
            verbose=verbose,
            source_context=source_context,
        )
        label = "Generating command…"
    with ui.thinking(label):
        raw_response = ai_provider.complete(prompt.system, prompt.user)
    parsed = parse_generate_response(raw_response)
    return parsed.command, parsed.explanation


def _run_generate_confirm_loop(
    ai_provider: AIProvider,
    *,
    request: str,
    verbose: bool,
    source_context: str | None,
    allow_stdin_cancel: bool = True,
) -> str | None:
    """Generate → explain → run | edit | save | exit until the user is done.

    Returns the last generated command (for callers such as the REPL that
    let the user save it as a preset afterwards), or None on parse failure.
    """
    previous_command: str | None = None
    feedback: str | None = None
    command: str | None = None
    explanation: str = ""
    probed_paths = extract_media_paths(request)

    while True:
        if command is None:
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

        _show_command(command)
        preflight = preflight_from_command(
            command,
            user_request=request,
            probed_paths=probed_paths,
        )
        _print_preflight_findings(preflight)
        _echo("")
        _echo(explanation)

        if not _stdin_is_interactive():
            return command

        next_step = "edit"
        while True:
            choice = _prompt_run_edit_exit()
            if choice == "exit":
                return command
            if choice == "edit":
                next_step = "edit"
                break
            if choice == "save":
                _prompt_save_preset(command)
                continue
            if choice == "run":
                if has_preflight_errors(preflight):
                    typer.secho(
                        "Fix pre-flight errors before running (choose edit).",
                        fg=typer.colors.RED,
                        err=True,
                    )
                    continue
                if _execute_approved_command(
                    command,
                    user_request=request,
                    probed_paths=probed_paths,
                    allow_stdin_cancel=allow_stdin_cancel,
                ):
                    return command
                if _last_failed_run is not None:
                    fix_choice = _prompt_fix_edit_exit()
                    if fix_choice == "exit":
                        return command
                    if fix_choice == "edit":
                        next_step = "edit"
                        break
                    corrected = _interpret_failure(
                        ai_provider,
                        failed=_last_failed_run,
                        request=request,
                        source_context=source_context,
                    )
                    if corrected is None or corrected.command is None:
                        # Diagnosis (or error) already printed; menu again.
                        continue
                    previous_command = command
                    command = corrected.command
                    explanation = corrected.diagnosis
                    next_step = "fixed"
                    break
                continue

        if next_step == "edit":
            feedback = _prompt_revision_feedback()
            previous_command = command
            command = None
        # next_step == "fixed": corrected command is already set; the outer
        # loop re-displays it (with preflight) without regenerating.


def _explain_once(
    ai_provider: AIProvider,
    command: str,
    *,
    verbose: bool,
) -> None:
    """Run one explain request and print the result."""
    try:
        source_context = build_source_context(extract_ffmpeg_input_paths(command))
        prompt = build_explain_prompt(
            command=command,
            verbose=verbose,
            source_context=source_context,
        )
        with ui.thinking("Explaining command…"):
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


def _format_preset_line(preset: Preset) -> str:
    """One-line vault listing: nickname, description, command."""
    description = f" — {preset.description}" if preset.description else ""
    return f"{preset.nickname}{description}\n    {preset.display}"


def _run_preset_by_nickname(
    nickname: str,
    inputs: list[str],
    *,
    allow_stdin_cancel: bool = True,
) -> bool:
    """Render a preset against real input files and run the approval flow."""
    try:
        preset = get_preset(nickname)
        command = render_preset_command(preset, inputs)
    except PresetError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        return False

    _echo(f"Preset '{preset.nickname}':")
    _show_command(command)
    preflight = preflight_from_command(command, probed_paths=list(inputs))
    _print_preflight_findings(preflight)
    if has_preflight_errors(preflight):
        typer.secho(
            "Pre-flight errors must be fixed before running.",
            fg=typer.colors.RED,
            err=True,
        )
        return False
    return _execute_approved_command(
        command,
        probed_paths=list(inputs),
        allow_stdin_cancel=allow_stdin_cancel,
    )


_REPL_HELP = """Commands:
  <plain-English request>          generate an ffmpeg command
  explain <ffmpeg command>         explain an existing command
  save <nickname> [description]   save the last generated command as a preset
  presets                          list saved presets
  preset search <text>            search the preset vault
  preset run <nickname> <file>    apply a preset to a new input file
  preset delete <nickname>        remove a preset
  help                             show this help
  exit                             leave the session"""


def _handle_repl_preset_command(raw: str) -> None:
    """Dispatch ``preset ...`` / ``presets`` lines typed inside the REPL.

    Anything that starts with ``preset`` is handled here — incomplete or
    unknown subcommands get a usage message rather than falling through to
    the AI generator.
    """
    import shlex as _shlex

    try:
        tokens = _shlex.split(raw, posix=True)
    except ValueError as exc:
        typer.secho(f"Could not parse input: {exc}", fg=typer.colors.RED, err=True)
        return

    sub = tokens[1].lower() if len(tokens) > 1 else "list"
    # Drop PowerShell call-operator artifacts from pasted commands.
    args = [token for token in tokens[2:] if token != "&"]

    if tokens[0].lower() == "presets" and len(tokens) == 1:
        sub, args = "list", []

    if sub == "list":
        presets = load_presets()
        if not presets:
            _echo("No presets saved yet. Use 'save <nickname>' after generating.")
        else:
            _print_preset_collection(
                [presets[nickname] for nickname in sorted(presets)]
            )
        return

    if sub == "search":
        if not args:
            _echo("Usage: preset search <text>")
            return
        results = search_presets(" ".join(args))
        if not results:
            _echo("No presets matched.")
        else:
            _print_preset_collection(list(results))
        return

    if sub == "delete":
        if len(args) != 1:
            _echo("Usage: preset delete <nickname>")
            return
        try:
            delete_preset(args[0])
            _echo("Preset deleted.")
        except PresetError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
        return

    if sub == "run":
        if len(args) < 2:
            _echo("Usage: preset run <nickname> <input-file>")
            return
        _run_preset_by_nickname(args[0], args[1:], allow_stdin_cancel=False)
        return

    if sub == "save":
        _echo(
            "Inside the session, use 'save <nickname>' after generating a "
            "command. (meg preset save works from the shell.)"
        )
        return

    _echo(f"Unknown preset command '{sub}'. Type 'help' for commands.")


def _run_repl(
    *,
    provider_override: str | None,
    model_override: str | None,
    verbose: bool,
) -> None:
    """Interactive conversational session on bare ``meg``."""
    _echo("meg — interactive session. Type a request, 'help', or 'exit'.")
    ai_provider: AIProvider | None = None
    last_command: str | None = None

    def ensure_provider() -> AIProvider | None:
        nonlocal ai_provider
        if ai_provider is not None:
            return ai_provider
        try:
            config = load_config()
            ai_provider = create_provider(
                config,
                override=provider_override,
                model_override=model_override,
            )
        except ConfigError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            return None
        return ai_provider

    while True:
        try:
            sys.stdout.write("meg> ")
            sys.stdout.flush()
        except (OSError, ValueError):
            return
        try:
            raw = _read_line().strip()
        except (EOFError, KeyboardInterrupt):
            _echo("")
            return

        if not raw:
            continue

        # Users often paste shell-style commands ("meg preset run ...",
        # PowerShell's "& 'path'") into the session; normalize them.
        if raw.lower() == "meg" or raw.lower().startswith("meg "):
            raw = raw[3:].strip()
            if not raw:
                continue
        lowered = raw.lower()

        if lowered in {"exit", "quit", "q"}:
            return
        if lowered in {"help", "?", "--help", "-h"}:
            _echo(_REPL_HELP)
            continue

        if lowered == "presets" or lowered == "preset" or lowered.startswith("preset "):
            _handle_repl_preset_command(raw)
            continue

        if lowered.startswith("save"):
            parts = raw.split(None, 2)
            if last_command is None:
                _echo("Nothing to save yet — generate a command first.")
                continue
            if len(parts) < 2:
                _echo("Usage: save <nickname> [description]")
                continue
            description = parts[2] if len(parts) > 2 else ""
            try:
                preset = add_preset(parts[1], last_command, description=description)
                _echo(f"Saved preset '{preset.nickname}': {preset.display}")
            except PresetError as exc:
                typer.secho(str(exc), fg=typer.colors.RED, err=True)
            continue

        if lowered.startswith("explain "):
            command = raw[len("explain "):].strip()
            if "ffmpeg" not in command.lower():
                _echo("Explain input should include an ffmpeg command.")
                continue
            provider = ensure_provider()
            if provider is None:
                continue
            try:
                _explain_once(provider, command, verbose=verbose)
            except typer.Exit:
                pass
            continue

        provider = ensure_provider()
        if provider is None:
            continue
        source_context = build_source_context(extract_media_paths(raw))
        try:
            result = _run_generate_confirm_loop(
                provider,
                request=raw,
                verbose=verbose,
                source_context=source_context,
                allow_stdin_cancel=False,
            )
        except typer.Exit:
            continue
        except Exception as exc:
            typer.secho(_format_provider_error(exc), fg=typer.colors.RED, err=True)
            continue
        if result is not None:
            last_command = result
            _echo("")
            _echo("Type 'save <nickname>' to keep this command, or enter a new request.")


@preset_app.command("save")
def preset_save(
    nickname: str = typer.Argument(..., help="Short name to recall the preset by."),
    command: str = typer.Argument(..., help="Full ffmpeg command to save."),
    description: str = typer.Option("", "--description", "-d", help="Searchable note."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace an existing preset."),
) -> None:
    """Save an ffmpeg command as a reusable preset."""
    _configure_terminal_utf8()
    try:
        preset = add_preset(
            nickname,
            command,
            description=description,
            overwrite=overwrite,
        )
    except PresetError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    _echo(f"Saved preset '{preset.nickname}': {preset.display}")
    _echo(f"Reuse it with: meg preset run {preset.nickname} <input-file>")


@preset_app.command("list")
def preset_list() -> None:
    """List all saved presets."""
    _configure_terminal_utf8()
    presets = load_presets()
    if not presets:
        _echo("No presets saved yet. Save one with: meg preset save <nickname> \"<command>\"")
        return
    _print_preset_collection([presets[nickname] for nickname in sorted(presets)])


@preset_app.command("search")
def preset_search(
    query: str = typer.Argument(..., help="Text to match against nickname, description, or command."),
) -> None:
    """Search the preset vault."""
    _configure_terminal_utf8()
    results = search_presets(query)
    if not results:
        _echo("No presets matched.")
        raise typer.Exit(code=1)
    _print_preset_collection(list(results))


@preset_app.command("delete")
def preset_delete(
    nickname: str = typer.Argument(..., help="Preset to remove."),
) -> None:
    """Delete a preset from the vault."""
    _configure_terminal_utf8()
    try:
        delete_preset(nickname)
    except PresetError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    _echo(f"Deleted preset '{nickname.strip()}'.")


@preset_app.command("run")
def preset_run(
    nickname: str = typer.Argument(..., help="Preset to apply."),
    inputs: list[str] = typer.Argument(..., help="Input media file(s) for this run."),
) -> None:
    """Apply a preset to new input file(s) and run it after approval."""
    _configure_terminal_utf8()
    if not _run_preset_by_nickname(nickname, list(inputs)):
        raise typer.Exit(code=1)


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
    load_env_files()  # ./.env then ~/.meg/.env; real env vars always win

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
        if _stdin_is_interactive():
            _run_repl(
                provider_override=provider,
                model_override=model,
                verbose=verbose,
            )
        else:
            _echo(ctx.get_help())
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
