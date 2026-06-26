"""Tests for argv-only ffmpeg/ffprobe execution."""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from meg.exec import (
    CommandValidationError,
    EXEC_CANCELLED_RC,
    EXEC_STALLED_RC,
    ExecutionResult,
    analyze_ffmpeg_safety,
    ensure_nostdin,
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


def test_run_command_missing_executable(monkeypatch) -> None:
    def raise_not_found(argv, **kwargs):
        _ = argv, kwargs
        raise FileNotFoundError(2, "No such file or directory", "ffmpeg")

    result = run_command(["ffmpeg", "-version"], subprocess_run=raise_not_found)

    assert result.returncode == 127
    assert "ffmpeg was not found" in result.stderr
    assert "PATH" in result.stderr


def test_summarize_execution_failure_falls_back_to_exit_code() -> None:
    result = ExecutionResult(returncode=2, stderr="")
    assert summarize_execution_failure(result) == "Command exited with code 2."


def test_analyze_ffmpeg_safety_extracts_single_input_and_output() -> None:
    argv = ["ffmpeg", "-i", "input.mkv", "-c", "copy", "output.mp4"]
    report = analyze_ffmpeg_safety(argv)
    assert report is not None
    assert report.input_paths == ("input.mkv",)
    assert report.output_paths == ("output.mp4",)
    assert report.ambiguous is False
    assert report.has_y_flag is False


def test_analyze_ffmpeg_safety_detects_y_flag() -> None:
    argv = ["ffmpeg", "-y", "-i", "input.mkv", "output.mp4"]
    report = analyze_ffmpeg_safety(argv)
    assert report is not None
    assert report.has_y_flag is True


def test_validate_ffmpeg_safety_rejects_input_output_collision() -> None:
    argv = ["ffmpeg", "-i", "clip.mov", "-c", "copy", "clip.mov"]
    report = analyze_ffmpeg_safety(argv)
    assert report is not None
    with pytest.raises(CommandValidationError, match="matches input"):
        validate_ffmpeg_safety(report)


def test_validate_ffmpeg_safety_rejects_blind_y() -> None:
    argv = ["ffmpeg", "-y", "-i", "input.mkv", "output.mp4"]
    report = analyze_ffmpeg_safety(argv)
    assert report is not None
    with pytest.raises(CommandValidationError, match="includes -y"):
        validate_ffmpeg_safety(report)


def test_validate_ffmpeg_safety_rejects_ambiguous_multiple_outputs() -> None:
    argv = ["ffmpeg", "-i", "a.mkv", "-i", "b.mkv", "out1.mp4", "out2.mp4"]
    report = analyze_ffmpeg_safety(argv)
    assert report is not None
    assert report.ambiguous is True
    with pytest.raises(CommandValidationError, match="Cannot verify input/output"):
        validate_ffmpeg_safety(report)


def test_analyze_ffmpeg_safety_detects_existing_output(tmp_path) -> None:
    output = tmp_path / "output.mp4"
    output.write_bytes(b"exists")
    argv = ["ffmpeg", "-i", "input.mkv", "-c", "copy", str(output)]
    report = analyze_ffmpeg_safety(argv)
    assert report is not None
    assert report.existing_outputs == (str(output),)


def test_prepare_execution_argv_strips_y_without_confirmation() -> None:
    argv = ("ffmpeg", "-y", "-i", "input.mkv", "-c", "copy", "output.mp4")
    prepared = prepare_execution_argv(argv, overwrite_confirmed=False)
    assert prepared == (
        "ffmpeg",
        "-nostdin",
        "-i",
        "input.mkv",
        "-c",
        "copy",
        "output.mp4",
    )


def test_prepare_execution_argv_injects_y_after_overwrite_confirm() -> None:
    argv = ("ffmpeg", "-i", "input.mkv", "-c", "copy", "output.mp4")
    prepared = prepare_execution_argv(argv, overwrite_confirmed=True)
    assert prepared == (
        "ffmpeg",
        "-nostdin",
        "-y",
        "-i",
        "input.mkv",
        "-c",
        "copy",
        "output.mp4",
    )


def test_ensure_nostdin_adds_flag_for_ffmpeg_only() -> None:
    assert ensure_nostdin(("ffmpeg", "-i", "in.mp4", "out.mp4"))[1] == "-nostdin"
    assert ensure_nostdin(("ffprobe", "-version")) == ("ffprobe", "-version")


def test_summarize_execution_failure_reports_cancel_and_stall() -> None:
    cancelled = ExecutionResult(returncode=EXEC_CANCELLED_RC, stderr="", cancelled=True)
    stalled = ExecutionResult(returncode=EXEC_STALLED_RC, stderr="", stalled=True)
    assert summarize_execution_failure(cancelled) == "Encode cancelled by user."
    assert "possible hang" in summarize_execution_failure(stalled)


def _python_stderr_popen_factory(script: str):
    def factory(argv, **kwargs):
        _ = argv
        return subprocess.Popen(
            [sys.executable, "-c", script],
            stderr=kwargs.get("stderr", subprocess.PIPE),
            stdout=kwargs.get("stdout"),
            text=kwargs.get("text", True),
            start_new_session=kwargs.get("start_new_session", False),
            creationflags=kwargs.get("creationflags", 0),
        )

    return factory


def test_run_managed_command_reports_progress() -> None:
    script = """
import sys, time
print("ffmpeg version", file=sys.stderr, flush=True)
for i in range(3):
    print(f"frame= {i} fps=1 time=00:00:0{i}.00 speed=1x", file=sys.stderr, flush=True)
    time.sleep(0.05)
"""
    seen: list[str] = []

    result = run_managed_command(
        ["ffmpeg", "-i", "in.mp4", "out.mp4"],
        stall_timeout_s=30.0,
        poll_interval_s=0.05,
        on_progress=seen.append,
        popen_factory=_python_stderr_popen_factory(script),
    )

    assert result.returncode == 0
    assert seen
    assert any("frame=" in line for line in seen)


def test_run_managed_command_stops_on_stall() -> None:
    script = "import time\ntime.sleep(10)\n"
    result = run_managed_command(
        ["ffmpeg", "-i", "in.mp4", "out.mp4"],
        stall_timeout_s=0.3,
        poll_interval_s=0.05,
        popen_factory=_python_stderr_popen_factory(script),
    )

    assert result.stalled is True
    assert result.returncode == EXEC_STALLED_RC
    assert "possible hang" in result.stderr


def test_run_managed_command_honours_cancel() -> None:
    script = "import time\ntime.sleep(30)\n"
    cancel_at = time.monotonic() + 0.2

    def should_cancel() -> bool:
        return time.monotonic() >= cancel_at

    result = run_managed_command(
        ["ffmpeg", "-i", "in.mp4", "out.mp4"],
        stall_timeout_s=30.0,
        poll_interval_s=0.05,
        should_cancel=should_cancel,
        popen_factory=_python_stderr_popen_factory(script),
    )

    assert result.cancelled is True
    assert result.returncode == EXEC_CANCELLED_RC
    assert "cancelled by user" in result.stderr.lower()


def test_run_managed_command_allows_long_jobs_while_progress_flows() -> None:
    script = """
import sys, time
for i in range(8):
    print(f"frame= {i} fps=1 time=00:00:0{i}.00 speed=1x", file=sys.stderr, flush=True)
    time.sleep(0.15)
"""
    result = run_managed_command(
        ["ffmpeg", "-i", "in.mp4", "out.mp4"],
        stall_timeout_s=1.0,
        poll_interval_s=0.05,
        popen_factory=_python_stderr_popen_factory(script),
    )

    assert result.returncode == 0
    assert result.stalled is False


def test_analyze_ffmpeg_safety_accepts_hdr_color_and_tag_flags() -> None:
    command = (
        'ffmpeg -i "d:\\media\\shot.mov" -vf scale=1280:720 -c:v libx265 -pix_fmt yuv420p10le '
        "-color_primaries smpte432 -color_trc smpte2084 -colorspace bt709 -tag:v hvc1 "
        '-c:a aac -movflags +faststart "d:\\media\\shot_out.mp4"'
    )
    parsed = parse_command_line(command)
    report = analyze_ffmpeg_safety(parsed.argv)
    assert report is not None
    assert report.ambiguous is False
    assert report.input_paths == ("d:\\media\\shot.mov",)
    assert report.output_paths == ("d:\\media\\shot_out.mp4",)


def test_analyze_ffmpeg_safety_accepts_x265_params_and_spaced_paths() -> None:
    command = (
        'ffmpeg -i "d:\\projects\\otrm\\test_media\\DeepConvergence\\Event_Version 1_0001_0009\\'
        '3D_test_Shot_01529627V0_V1-0009.mov" -vf scale=1280:720:flags=lanczos -c:v libx265 '
        "-preset medium -crf 23 -pix_fmt yuv420p10le -colorspace bt709 -color_primaries smpte432 "
        '-color_trc smpte2084 -x265-params "hdr-opt=1:repeat-headers=1:colorprim=bt2020:'
        'transfer=smpte2084:colormatrix=bt709" -c:a aac -b:a 192k '
        '"d:\\projects\\otrm\\test_media\\DeepConvergence\\Event_Version 1_0001_0009\\'
        '3D_test_Shot_01529627V0_V1-0009_out.mp4"'
    )
    parsed = parse_command_line(command)
    report = analyze_ffmpeg_safety(parsed.argv)
    assert report is not None
    assert report.ambiguous is False
    assert report.output_paths == (
        "d:\\projects\\otrm\\test_media\\DeepConvergence\\Event_Version 1_0001_0009\\"
        "3D_test_Shot_01529627V0_V1-0009_out.mp4",
    )


def test_analyze_ffmpeg_safety_returns_none_for_ffprobe() -> None:
    assert analyze_ffmpeg_safety(["ffprobe", "-version"]) is None
