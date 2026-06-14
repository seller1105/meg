"""Typer CLI — entry point for the ``meg`` command."""

from __future__ import annotations

import sys
from typing import Callable, NoReturn, Optional

import typer
from meg.config import ConfigError, load_config
from meg.exec import (
    CommandValidationError,
    parse_command_line,
    run_command,
    stderr_tail,
    summarize_execution_failure,
    validate_allowed_executable,
)
from meg.ffprobe import (
    build_source_context,
    extract_ffmpeg_input_paths,
    extract_media_paths,
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


def _execute_approved_command(command: str) -> None:
    """Parse, validate, and run an approved ffmpeg/ffprobe command."""
    try:
        parsed = parse_command_line(command)
        validate_allowed_executable(parsed.argv)
    except CommandValidationError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    _echo("")
    _echo(f"Running: {parsed.display}")

    result = run_command(parsed.argv)
    if result.returncode == 0:
        return

    summary = summarize_execution_failure(result)
    typer.secho(
        f"Command failed (exit {result.returncode}): {summary}",
        fg=typer.colors.RED,
        err=True,
    )
    if _stdin_is_interactive():
        _prompt_show_stderr_tail(stderr_tail(result.stderr))
    raise typer.Exit(code=result.returncode)


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

        choice = _prompt_run_edit_exit()
        if choice == "exit":
            return
        if choice == "run":
            _execute_approved_command(command)
            return

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
