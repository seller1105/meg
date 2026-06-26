"""Tests for live ffmpeg progress rendering."""

from __future__ import annotations

from meg.cli import (
    _LiveProgressDisplay,
    _format_progress_status,
    _write_tty_status,
)


def test_format_progress_status_includes_duration_ratio() -> None:
    line = "frame= 120 fps= 30 q=28.0 time=00:01:00.00 speed=1.03x bitrate= 1200kbits/s"
    status = _format_progress_status(line, duration_seconds=125.5)
    assert status.startswith("Encoding…")
    assert "1:00.000 / 2:05.500" in status
    assert "speed=1.03x" in status
    assert "frame=120" in status
    assert "fps=30" in status
    assert "bitrate=1200kbits/s" in status


def test_format_progress_status_without_duration_keeps_time_token() -> None:
    line = "frame= 10 fps=0.0 time=00:00:04.00 speed=2.1x"
    status = _format_progress_status(line)
    assert "time=00:00:04.00" in status
    assert "speed=2.1x" in status


def test_live_progress_display_uses_tty_writer(monkeypatch) -> None:
    writes: list[str] = []

    monkeypatch.setattr("meg.cli._write_tty_status", writes.append)

    display = _LiveProgressDisplay(duration_seconds=60.0, use_tty=True)
    display.start()
    display.update("frame= 1 fps=1 time=00:00:01.00 speed=1x")
    display.finish()

    assert len(writes) == 1
    assert "Encoding…" in writes[0]
    assert "1:00.000" in writes[0]


def test_live_progress_display_falls_back_to_lines_when_not_tty(monkeypatch) -> None:
    lines: list[str] = []

    monkeypatch.setattr("meg.cli._echo_line", lines.append)

    display = _LiveProgressDisplay(duration_seconds=None, use_tty=False)
    display.start()
    display.update("frame= 1 fps=1 time=00:00:01.00 speed=1x")
    display.finish()

    assert len(lines) == 1
    assert "Encoding…" in lines[0]


def test_write_tty_status_uses_carriage_return(capsys) -> None:
    _write_tty_status("Encoding… time=00:00:01.00")
    captured = capsys.readouterr()
    assert captured.out.startswith("\rEncoding…")
