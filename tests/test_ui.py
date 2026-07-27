"""Tests for Rich UI rendering and its plain-text fallbacks."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from meg import cli, ui


@pytest.fixture()
def tty_console(monkeypatch) -> io.StringIO:
    """Swap the ui console for one that pretends to be an interactive TTY."""
    buffer = io.StringIO()
    monkeypatch.setattr(
        ui, "console", Console(force_terminal=True, width=100, file=buffer)
    )
    return buffer


def test_rich_active_false_under_capture() -> None:
    # pytest captures stdout, so the default console is not a terminal.
    assert ui.rich_active() is False


def test_show_command_plain_when_not_tty(capsys) -> None:
    cli._show_command("ffmpeg -i in.mov out.mp4")
    captured = capsys.readouterr()
    assert captured.out.strip() == "ffmpeg -i in.mov out.mp4"
    # No panel borders on the plain path.
    assert "─" not in captured.out


def test_show_command_panel_on_tty(tty_console: io.StringIO) -> None:
    cli._show_command("ffmpeg -i in.mov out.mp4")
    output = tty_console.getvalue()
    assert "ffmpeg" in output
    assert "command" in output  # panel title
    assert "─" in output  # panel border


def test_thinking_is_noop_when_not_tty(capsys) -> None:
    with ui.thinking("Generating command…"):
        pass
    assert capsys.readouterr().out == ""


def test_thinking_renders_status_on_tty(tty_console: io.StringIO) -> None:
    with ui.thinking("Generating command…"):
        pass
    assert "Generating command…" in tty_console.getvalue()


def test_preset_table_on_tty(tty_console: io.StringIO) -> None:
    ui.print_preset_table(
        [
            ("gif", "video to gif", "ffmpeg -i {input} out.gif"),
            ("mp3", "", "ffmpeg -i {input} out.mp3"),
        ]
    )
    output = tty_console.getvalue()
    assert "Nickname" in output
    assert "gif" in output
    assert "video to gif" in output
    assert "mp3" in output


def test_encode_progress_with_duration_shows_percent(
    tty_console: io.StringIO,
) -> None:
    progress = ui.EncodeProgress(10.0)
    progress.start()
    progress.update(5.0, "speed=2x")
    progress.finish()
    output = tty_console.getvalue()
    assert "Encoding" in output
    assert "50%" in output
    assert "speed=2x" in output


def test_encode_progress_without_duration_shows_detail(
    tty_console: io.StringIO,
) -> None:
    progress = ui.EncodeProgress(None)
    progress.start()
    progress.update(None, "time=00:00:04.00 speed=2.1x")
    progress.finish()
    output = tty_console.getvalue()
    assert "Encoding" in output
    assert "time=00:00:04.00" in output


def test_encode_progress_finish_complete_fills_bar(
    tty_console: io.StringIO,
) -> None:
    # Short encodes: last ffmpeg progress line lands well before the end.
    progress = ui.EncodeProgress(10.0)
    progress.start()
    progress.update(2.4, "speed=357x")
    progress.finish(complete=True)
    assert "100%" in tty_console.getvalue()


def test_encode_progress_finish_complete_with_no_updates(
    tty_console: io.StringIO,
) -> None:
    # Sub-second encodes may emit no progress lines at all.
    progress = ui.EncodeProgress(1.0)
    progress.start()
    progress.finish(complete=True)
    assert "100%" in tty_console.getvalue()


def test_encode_progress_finish_incomplete_keeps_partial_bar(
    tty_console: io.StringIO,
) -> None:
    # Cancel/stall/failure must not pretend the encode completed.
    progress = ui.EncodeProgress(10.0)
    progress.start()
    progress.update(2.4, "speed=1x")
    progress.finish()
    output = tty_console.getvalue()
    assert "24%" in output
    assert "100%" not in output


def test_live_progress_display_finish_complete_fills_bar(
    tty_console: io.StringIO,
) -> None:
    display = cli._LiveProgressDisplay(duration_seconds=60.0, use_tty=True)
    display.start()
    display.update("frame= 1 fps=1 time=00:00:30.00 speed=1x")
    display.finish(complete=True)
    assert "100%" in tty_console.getvalue()


def test_live_progress_display_uses_rich_on_tty(
    tty_console: io.StringIO,
) -> None:
    display = cli._LiveProgressDisplay(duration_seconds=60.0, use_tty=True)
    display.start()
    display.update("frame= 1 fps=1 time=00:00:30.00 speed=1x")
    display.finish()
    output = tty_console.getvalue()
    assert "Encoding" in output
    assert "50%" in output


def test_live_progress_display_ignores_non_progress_lines_on_rich(
    tty_console: io.StringIO,
) -> None:
    display = cli._LiveProgressDisplay(duration_seconds=60.0, use_tty=True)
    display.start()
    display.update("Stream mapping:")
    display.finish()
    # No crash, bar rendered without bogus completion.
    assert "Encoding" in tty_console.getvalue()
