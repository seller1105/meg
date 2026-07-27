"""Rich-rendered terminal output for interactive sessions.

Every helper here is only used when stdout is a real interactive terminal
(``rich_active()``); callers in ``cli.py`` keep the existing plain-text
paths for pipes, redirects, and tests. That keeps Meg's non-interactive
output contract unchanged: paste-able plain text, no ANSI, no boxes.

Rich also honors ``NO_COLOR`` and legacy Windows consoles natively, which
removes the need for manual encoding fallbacks on the styled paths.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Sequence

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.syntax import Syntax
from rich.table import Table

# Module-level console so tests can swap in Console(force_terminal=True, file=...).
console = Console()


def rich_active() -> bool:
    """True when output should use Rich rendering (interactive terminal)."""
    return console.is_terminal


def print_command_panel(command: str) -> None:
    """Render an ffmpeg command in a bordered panel with bash highlighting."""
    syntax = Syntax(
        command,
        "bash",
        word_wrap=True,
        background_color="default",
    )
    console.print(Panel(syntax, title="command", border_style="cyan", expand=False))


@contextmanager
def thinking(message: str) -> Iterator[None]:
    """Show a spinner while a blocking call (AI request) runs.

    No-op when stdout is not an interactive terminal.
    """
    if not rich_active():
        yield
        return
    with console.status(message, spinner="dots"):
        yield


def print_preset_table(rows: Sequence[tuple[str, str, str]]) -> None:
    """Render presets as a table of (nickname, description, command)."""
    table = Table(show_header=True, header_style="bold")
    table.add_column("Nickname", style="cyan", no_wrap=True)
    table.add_column("Description")
    table.add_column("Command", overflow="fold")
    for nickname, description, command in rows:
        table.add_row(nickname, description or "—", command)
    console.print(table)


class EncodeProgress:
    """Live progress for a managed ffmpeg encode.

    With a known source duration: label + bar + percent + ETA + detail.
    Without a duration: label + detail text (time/speed as reported).
    """

    def __init__(self, duration_seconds: float | None) -> None:
        self._duration = (
            duration_seconds
            if duration_seconds is not None and duration_seconds > 0
            else None
        )
        self._progress: Progress | None = None
        self._task_id: TaskID | None = None

    def start(self) -> None:
        if self._duration is not None:
            columns = (
                TextColumn("Encoding"),
                BarColumn(),
                TaskProgressColumn(),
                TimeRemainingColumn(),
                TextColumn("{task.fields[detail]}"),
            )
        else:
            columns = (
                TextColumn("Encoding"),
                TextColumn("{task.fields[detail]}"),
            )
        self._progress = Progress(*columns, console=console)
        self._progress.start()
        self._task_id = self._progress.add_task(
            "encode", total=self._duration, detail=""
        )

    def update(self, seconds: float | None, detail: str) -> None:
        if self._progress is None or self._task_id is None:
            return
        if seconds is not None and self._duration is not None:
            self._progress.update(
                self._task_id,
                completed=min(seconds, self._duration),
                detail=detail,
            )
        else:
            self._progress.update(self._task_id, detail=detail)

    def finish(self, *, complete: bool = False) -> None:
        """Stop rendering. ``complete=True`` fills the bar to 100% first.

        Short encodes often end before ffmpeg emits a final progress line
        (or emit none at all), so on success the caller should pass
        ``complete=True``; on cancel/stall/failure the bar stays where the
        last real update left it.
        """
        if self._progress is not None:
            if (
                complete
                and self._task_id is not None
                and self._duration is not None
            ):
                self._progress.update(self._task_id, completed=self._duration)
            self._progress.stop()
            self._progress = None
            self._task_id = None
