"""Typer CLI — entry point for the ``meg`` command."""

from __future__ import annotations

import sys
from typing import NoReturn, Optional

import typer
from meg.config import ConfigError, load_config
from meg.prompt import (
    PromptParseError,
    build_explain_prompt,
    build_generate_prompt,
    parse_explain_response,
    parse_generate_response,
)
from meg.providers import create_provider

app = typer.Typer(
    name="meg",
    help="AI-powered FFmpeg assistant for the terminal.",
    no_args_is_help=True,
)


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
            prompt = build_explain_prompt(command=explain, verbose=verbose)
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
        try:
            prompt = build_generate_prompt(request=request, verbose=verbose)
            raw_response = ai_provider.complete(prompt.system, prompt.user)
            parsed = parse_generate_response(raw_response)
        except PromptParseError as exc:
            typer.secho(
                f"Could not parse model output: {exc}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1) from exc
        except Exception as exc:
            _exit_provider_error(exc)

        _echo(parsed.command)
        _echo("")
        _echo(parsed.explanation)
