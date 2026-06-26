"""Smoke tests for the CLI."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from meg.cli import app

runner = CliRunner()


def test_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "FFmpeg" in result.stdout


def test_version_import() -> None:
    from meg import __version__

    assert __version__ == "0.2.0"


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
    captured: dict[str, str] = {}

    class FakeProvider:
        def complete(self, system: str, user: str) -> str:
            captured["user"] = user
            _ = system
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
    assert "Request: convert mkv to h264 mp4" in captured["user"]


def test_generate_request_includes_ffprobe_context_for_local_file(
    monkeypatch, tmp_path
) -> None:
    media = tmp_path / "source.mkv"
    media.write_bytes(b"fake")
    captured: dict[str, str] = {}

    class FakeProvider:
        def complete(self, system: str, user: str) -> str:
            captured["user"] = user
            _ = system
            return "\n".join(
                [
                    "COMMAND:",
                    f"ffmpeg -i {media} -c:v libx264 output.mp4",
                    "EXPLANATION:",
                    "- Scales from probed 3840x2160 source.",
                ]
            )

    def fake_build_source_context(paths, *, ffprobe_bin=None):
        assert str(media) in paths
        return "Verified source metadata\nSource: source.mkv\n  video[0]: prores, 3840x2160"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("meg.cli.create_provider", lambda *args, **kwargs: FakeProvider())
    monkeypatch.setattr("meg.cli.build_source_context", fake_build_source_context)

    result = runner.invoke(app, [f'convert "{media}" to h264 mp4'])

    assert result.exit_code == 0
    assert "Verified source metadata" in captured["user"]
    assert "3840x2160" in captured["user"]


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


def _fake_generate_response() -> str:
    return "\n".join(
        [
            "COMMAND:",
            "ffmpeg -i input.mkv -c:v libx264 -c:a aac output.mp4",
            "EXPLANATION:",
            "- Uses H.264 for broad playback compatibility.",
        ]
    )


def test_generate_skips_confirm_loop_when_stdin_not_tty(monkeypatch) -> None:
    class FakeProvider:
        def complete(self, system: str, user: str) -> str:
            _ = system, user
            return _fake_generate_response()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("meg.cli.create_provider", lambda *args, **kwargs: FakeProvider())
    monkeypatch.setattr("meg.cli._stdin_is_interactive", lambda: False)

    result = runner.invoke(app, ["convert mkv to h264 mp4"])

    assert result.exit_code == 0
    assert "ffmpeg -i input.mkv" in result.stdout
    assert "[r]un" not in result.stdout


def test_generate_confirm_loop_quit(monkeypatch) -> None:
    class FakeProvider:
        def complete(self, system: str, user: str) -> str:
            _ = system, user
            return _fake_generate_response()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("meg.cli.create_provider", lambda *args, **kwargs: FakeProvider())
    monkeypatch.setattr("meg.cli._stdin_is_interactive", lambda: True)

    result = runner.invoke(app, ["convert mkv to h264 mp4"], input="q\n")

    assert result.exit_code == 0
    assert "[r]un" in result.stdout


def test_generate_confirm_loop_runs_approved_command(monkeypatch) -> None:
    class FakeProvider:
        def complete(self, system: str, user: str) -> str:
            _ = system, user
            return _fake_generate_response()

    captured: dict[str, object] = {}

    def fake_run(argv, *, interactive, source_duration_seconds=None):
        captured["argv"] = argv
        from meg.exec import ExecutionResult

        return ExecutionResult(returncode=0, stderr="")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("meg.cli.create_provider", lambda *args, **kwargs: FakeProvider())
    monkeypatch.setattr("meg.cli._stdin_is_interactive", lambda: True)
    monkeypatch.setattr("meg.cli._run_approved_argv", fake_run)

    result = runner.invoke(app, ["convert mkv to h264 mp4"], input="r\ny\n")

    assert result.exit_code == 0
    assert "Running:" in result.stdout
    assert captured["argv"][0] == "ffmpeg"
    assert captured["argv"][-1] == "output.mp4"


def test_generate_confirm_loop_edit_regenerates_with_feedback(monkeypatch) -> None:
    calls: list[str] = []

    class FakeProvider:
        def complete(self, system: str, user: str) -> str:
            calls.append(user)
            if len(calls) == 1:
                return _fake_generate_response()
            return "\n".join(
                [
                    "COMMAND:",
                    "ffmpeg -i input.mkv -c:v libx264 -crf 18 -c:a aac output.mp4",
                    "EXPLANATION:",
                    "- Adds CRF 18 for higher quality.",
                ]
            )

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("meg.cli.create_provider", lambda *args, **kwargs: FakeProvider())
    monkeypatch.setattr("meg.cli._stdin_is_interactive", lambda: True)

    result = runner.invoke(
        app,
        ["convert mkv to h264 mp4"],
        input="e\nuse crf 18\nq\n",
    )

    assert result.exit_code == 0
    assert len(calls) == 2
    assert "User feedback: use crf 18" in calls[1]
    assert "crf 18" in result.stdout


def test_generate_run_failure_shows_summary_and_optional_tail(monkeypatch) -> None:
    class FakeProvider:
        def complete(self, system: str, user: str) -> str:
            _ = system, user
            return _fake_generate_response()

    def fake_run(argv, *, interactive, source_duration_seconds=None):
        _ = interactive, source_duration_seconds
        from meg.exec import ExecutionResult

        return ExecutionResult(
            returncode=1,
            stderr="frame= 0 fps=0.0\nError opening output file: Permission denied\n",
        )

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("meg.cli.create_provider", lambda *args, **kwargs: FakeProvider())
    monkeypatch.setattr("meg.cli._stdin_is_interactive", lambda: True)
    monkeypatch.setattr("meg.cli._run_approved_argv", fake_run)

    result = runner.invoke(app, ["convert mkv to h264 mp4"], input="r\ny\nn\n")

    assert result.exit_code == 0
    assert "Command failed" in result.stderr
    assert "Permission denied" in result.stderr
    assert "Show stderr tail?" in result.stdout
    assert "Run this command?" in result.stdout


def test_generate_confirm_run_declined_returns_to_menu(monkeypatch) -> None:
    class FakeProvider:
        def complete(self, system: str, user: str) -> str:
            _ = system, user
            return _fake_generate_response()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("meg.cli.create_provider", lambda *args, **kwargs: FakeProvider())
    monkeypatch.setattr("meg.cli._stdin_is_interactive", lambda: True)

    result = runner.invoke(app, ["convert mkv to h264 mp4"], input="r\nn\nq\n")

    assert result.exit_code == 0
    assert "Run this command?" in result.stdout
    assert "Running:" not in result.stdout
    assert result.stdout.count("[r]un") >= 2


def test_generate_edit_then_run_requires_fresh_confirm(monkeypatch) -> None:
    calls: list[str] = []

    class FakeProvider:
        def complete(self, system: str, user: str) -> str:
            calls.append(user)
            if len(calls) == 1:
                return _fake_generate_response()
            return "\n".join(
                [
                    "COMMAND:",
                    "ffmpeg -i input.mkv -c:v libx264 -crf 18 -c:a aac output.mp4",
                    "EXPLANATION:",
                    "- Adds CRF 18 for higher quality.",
                ]
            )

    captured: list[tuple[str, ...]] = []

    def fake_run(argv, *, interactive, source_duration_seconds=None):
        _ = interactive, source_duration_seconds
        captured.append(tuple(argv))
        from meg.exec import ExecutionResult

        return ExecutionResult(returncode=0, stderr="")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("meg.cli.create_provider", lambda *args, **kwargs: FakeProvider())
    monkeypatch.setattr("meg.cli._stdin_is_interactive", lambda: True)
    monkeypatch.setattr("meg.cli._run_approved_argv", fake_run)

    result = runner.invoke(
        app,
        ["convert mkv to h264 mp4"],
        input="e\nuse crf 18\nr\ny\n",
    )

    assert result.exit_code == 0
    assert len(captured) == 1
    assert any("crf" in arg for arg in captured[0])
    assert result.stdout.count("Run this command?") == 1


def test_generate_run_missing_ffmpeg_shows_clear_message(monkeypatch) -> None:
    class FakeProvider:
        def complete(self, system: str, user: str) -> str:
            _ = system, user
            return _fake_generate_response()

    def fake_run(argv, *, interactive, source_duration_seconds=None):
        _ = interactive, source_duration_seconds
        from meg.exec import ExecutionResult

        return ExecutionResult(
            returncode=127,
            stderr="ffmpeg was not found. Install FFmpeg and ensure ffmpeg is on your PATH.",
        )

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("meg.cli.create_provider", lambda *args, **kwargs: FakeProvider())
    monkeypatch.setattr("meg.cli._stdin_is_interactive", lambda: True)
    monkeypatch.setattr("meg.cli._run_approved_argv", fake_run)

    result = runner.invoke(app, ["convert mkv to h264 mp4"], input="r\ny\nn\nq\n")

    assert result.exit_code == 0
    assert "ffmpeg was not found" in result.stderr
    assert "Running:" in result.stdout


def test_execute_approved_command_rejects_disallowed_executable(capsys) -> None:
    from meg.cli import _execute_approved_command

    assert _execute_approved_command("bash -c 'rm -rf /'") is False
    captured = capsys.readouterr()
    assert "Only ffmpeg and ffprobe" in captured.err


def test_execute_approved_command_rejects_input_output_collision(capsys) -> None:
    from meg.cli import _execute_approved_command

    assert _execute_approved_command("ffmpeg -i clip.mov -c copy clip.mov") is False
    captured = capsys.readouterr()
    assert "matches input" in captured.err


def test_execute_approved_command_rejects_blind_y(capsys) -> None:
    from meg.cli import _execute_approved_command

    assert _execute_approved_command("ffmpeg -y -i input.mkv -c copy output.mp4") is False
    captured = capsys.readouterr()
    assert "includes -y" in captured.err


def test_generate_run_warns_and_injects_y_for_existing_output(
    tmp_path, monkeypatch
) -> None:
    output = tmp_path / "output.mp4"
    output.write_bytes(b"existing")

    class FakeProvider:
        def complete(self, system: str, user: str) -> str:
            _ = system, user
            return "\n".join(
                [
                    "COMMAND:",
                    f'ffmpeg -i input.mkv -c copy "{output}"',
                    "EXPLANATION:",
                    "- Copies streams to a new file.",
                ]
            )

    captured: list[tuple[str, ...]] = []

    def fake_run(argv, *, interactive, source_duration_seconds=None):
        _ = interactive, source_duration_seconds
        captured.append(tuple(argv))
        from meg.exec import ExecutionResult

        return ExecutionResult(returncode=0, stderr="")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("meg.cli.create_provider", lambda *args, **kwargs: FakeProvider())
    monkeypatch.setattr("meg.cli._stdin_is_interactive", lambda: True)
    monkeypatch.setattr("meg.cli._run_approved_argv", fake_run)

    result = runner.invoke(app, ["convert mkv to mp4"], input="r\ny\n")

    assert result.exit_code == 0
    assert "already exists and will be overwritten" in result.stdout
    assert captured
    assert captured[0][1] == "-nostdin"
    assert captured[0][2] == "-y"
