"""Tests for argv-only ffmpeg/ffprobe execution."""

from __future__ import annotations

import subprocess

import pytest

from meg.exec import (
    CommandValidationError,
    ExecutionResult,
    format_argv_display,
    parse_command_line,
    run_command,
    stderr_tail,
    summarize_execution_failure,
    validate_allowed_executable,
)


def test_parse_command_preserves_quoted_paths_with_spaces() -> None:
    parsed = parse_command_line('ffmpeg -i "my clip.mov" -c copy "out file.mp4"')
    assert parsed.argv[0] == "ffmpeg"
    assert parsed.argv[2] == "my clip.mov"
    assert parsed.argv[-1] == "out file.mp4"
    assert "my clip.mov" in parsed.display


def test_parse_command_handles_drawtext_filter_quotes() -> None:
    command = (
        'ffmpeg -i in.mp4 -vf "drawtext=text=Hello\\: World:fontsize=24" out.mp4'
    )
    parsed = parse_command_line(command)
    assert parsed.argv[0] == "ffmpeg"
    assert any("drawtext=" in arg for arg in parsed.argv)


def test_validate_rejects_non_ffmpeg_executables() -> None:
    with pytest.raises(CommandValidationError, match="Only ffmpeg and ffprobe"):
        validate_allowed_executable(["bash", "-c", "rm -rf /"])

    with pytest.raises(CommandValidationError, match="Only ffmpeg and ffprobe"):
        validate_allowed_executable(["/usr/bin/curl", "http://evil"])


def test_validate_accepts_ffmpeg_and_ffprobe_names() -> None:
    validate_allowed_executable(["ffmpeg", "-version"])
    validate_allowed_executable(["ffprobe", "-version"])
    validate_allowed_executable([r"C:\ffmpeg\bin\ffmpeg.exe", "-version"])


def test_format_argv_display_round_trips_simple_command() -> None:
    argv = ["ffmpeg", "-i", "input.mkv", "-c", "copy", "output.mp4"]
    assert format_argv_display(argv) == "ffmpeg -i input.mkv -c copy output.mp4"


def test_run_command_uses_argv_without_shell(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("meg.exec.subprocess.run", fake_run)

    result = run_command(["ffmpeg", "-i", "in.mp4", "out.mp4"])

    assert result.returncode == 0
    assert captured["argv"] == ["ffmpeg", "-i", "in.mp4", "out.mp4"]
    assert captured["kwargs"]["stdout"] is None
    assert captured["kwargs"]["stderr"] == subprocess.PIPE


def test_stderr_tail_strips_progress_noise() -> None:
    stderr = "\n".join(
        [
            "ffmpeg version 6.0",
            "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'in.mp4':",
            "frame=  120 fps= 30 q=28.0 size=    1024kB time=00:00:04.00",
            "Error opening output file out.mp4: Permission denied",
        ]
    )
    tail = stderr_tail(stderr)
    assert tail[-1] == "Error opening output file out.mp4: Permission denied"
    assert not any(line.startswith("frame=") for line in tail)


def test_summarize_execution_failure_prefers_error_line() -> None:
    result = ExecutionResult(
        returncode=1,
        stderr="frame= 10 fps=0.0\nInvalid argument\nConversion failed!",
    )
    summary = summarize_execution_failure(result)
    assert summary == "Conversion failed!"


def test_summarize_execution_failure_falls_back_to_exit_code() -> None:
    result = ExecutionResult(returncode=2, stderr="")
    assert summarize_execution_failure(result) == "Command exited with code 2."
