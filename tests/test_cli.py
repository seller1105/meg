"""Smoke tests for the CLI."""

from __future__ import annotations

from typer.testing import CliRunner

from meg.cli import app

runner = CliRunner()


def test_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "FFmpeg" in result.stdout


def test_version_import() -> None:
    from meg import __version__

    assert __version__ == "0.1.0"


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    # Typer/Click use exit code 2 when showing help via no_args_is_help.
    assert result.exit_code == 2
    assert "FFmpeg" in result.stdout


def test_request_without_api_keys_fails_with_actionable_error(
    monkeypatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MEG_PROVIDER", raising=False)

    result = runner.invoke(app, ["convert mkv to mp4"])

    assert result.exit_code == 1
    assert "No API key found" in result.stderr


def test_generate_request_prints_command_and_explanation(monkeypatch) -> None:
    class FakeProvider:
        def complete(self, system: str, user: str) -> str:
            _ = system, user
            return "\n".join(
                [
                    "COMMAND:",
                    "ffmpeg -i input.mkv -c:v libx264 -c:a aac output.mp4",
                    "EXPLANATION:",
                    "- Uses H.264 for broad playback compatibility.",
                    "- Uses AAC audio for MP4 compatibility.",
                ]
            )

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        "meg.cli.create_provider",
        lambda config, override=None, model_override=None: FakeProvider(),
    )

    result = runner.invoke(app, ["convert mkv to h264 mp4"])

    assert result.exit_code == 0
    assert "ffmpeg -i input.mkv -c:v libx264 -c:a aac output.mp4" in result.stdout
    assert "Uses H.264" in result.stdout


def test_generate_request_handles_parse_errors(monkeypatch) -> None:
    class BadProvider:
        def complete(self, system: str, user: str) -> str:
            _ = system, user
            return "not in required format"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        "meg.cli.create_provider",
        lambda config, override=None, model_override=None: BadProvider(),
    )

    result = runner.invoke(app, ["convert mkv to h264 mp4"])

    assert result.exit_code == 1
    assert "Could not parse model output" in result.stderr


def test_generate_request_rejects_empty_input(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    result = runner.invoke(app, ["   "])
    assert result.exit_code == 1
    assert "Request must not be empty" in result.stderr


def test_explain_flag_prints_breakdown(monkeypatch) -> None:
    class FakeProvider:
        def complete(self, system: str, user: str) -> str:
            _ = system, user
            return "\n".join(
                [
                    "EXPLANATION:",
                    "- `-i input.mp4` sets the input file.",
                    "- `-c copy` remuxes streams without re-encoding.",
                ]
            )

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        "meg.cli.create_provider",
        lambda config, override=None, model_override=None: FakeProvider(),
    )

    result = runner.invoke(
        app,
        ["--explain", "ffmpeg -i input.mp4 -c copy output.mp4"],
    )

    assert result.exit_code == 0
    assert "remuxes streams" in result.stdout
    assert "ffmpeg -i input.mp4" not in result.stdout


def test_explain_flag_rejects_empty_input(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    result = runner.invoke(app, ["--explain", "   "])
    assert result.exit_code == 1
    assert "Explain input must not be empty" in result.stderr


def test_explain_prints_unicode_without_encode_error(monkeypatch) -> None:
    class UnicodeProvider:
        def complete(self, system: str, user: str) -> str:
            _ = system, user
            return "\n".join(
                [
                    "EXPLANATION:",
                    "- Scale 1920×1080 → libx264 output.",
                    "- Requires stream ≥ 1 for mapping.",
                ]
            )

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        "meg.cli.create_provider",
        lambda config, override=None, model_override=None: UnicodeProvider(),
    )

    result = runner.invoke(
        app,
        ["--explain", "ffmpeg -i input.mp4 -vf scale=1920:1080 -c:v libx264 out.mp4"],
    )

    assert result.exit_code == 0
    assert "1920" in result.stdout


def test_explain_and_request_together_are_rejected(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    result = runner.invoke(
        app,
        ["--explain", "ffmpeg -i input.mp4 -c copy output.mp4", "convert mkv to mp4"],
    )
    assert result.exit_code == 1
    assert "not both" in result.stderr


def test_explain_and_request_together_rejected_before_api_key_check(
    monkeypatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MEG_PROVIDER", raising=False)

    result = runner.invoke(app, ["--explain", "x", "y"])

    assert result.exit_code == 1
    assert "not both" in result.stderr
    assert "No API key found" not in result.stderr


def test_empty_generate_request_rejected_before_api_key_check(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MEG_PROVIDER", raising=False)

    result = runner.invoke(app, ["   "])

    assert result.exit_code == 1
    assert "Request must not be empty" in result.stderr
    assert "No API key found" not in result.stderr


def test_invalid_input_does_not_create_provider(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def fail_create_provider(*args, **kwargs):
        raise AssertionError("create_provider should not run for invalid input")

    monkeypatch.setattr("meg.cli.create_provider", fail_create_provider)

    result = runner.invoke(
        app,
        ["--explain", "ffmpeg -i input.mp4 -c copy output.mp4", "convert mkv to mp4"],
    )

    assert result.exit_code == 1
    assert "not both" in result.stderr


def test_generate_request_reports_timeout_with_actionable_message(monkeypatch) -> None:
    class TimeoutProvider:
        def complete(self, system: str, user: str) -> str:
            _ = system, user
            raise TimeoutError("Request timed out after 60 seconds")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        "meg.cli.create_provider",
        lambda config, override=None, model_override=None: TimeoutProvider(),
    )

    result = runner.invoke(app, ["convert mkv to h264 mp4"])

    assert result.exit_code == 1
    assert "timed out" in result.stderr.lower()
    assert "retry" in result.stderr.lower()
