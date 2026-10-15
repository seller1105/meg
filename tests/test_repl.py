"""Tests for the interactive REPL session and preset CLI commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import meg.cli as cli
import meg.presets as presets_module
from meg.cli import app
from meg.presets import add_preset, load_presets

runner = CliRunner()

_FAKE_RESPONSE = "\n".join(
    [
        "COMMAND:",
        "ffmpeg -i input.mkv -c:v libx264 -c:a aac output.mp4",
        "EXPLANATION:",
        "- Uses H.264 for broad playback compatibility.",
    ]
)


class FakeProvider:
    def complete(self, system: str, user: str) -> str:
        _ = system, user
        return _FAKE_RESPONSE


@pytest.fixture()
def vault(monkeypatch, tmp_path: Path) -> Path:
    """Point the preset vault at a temp file for the duration of a test."""
    path = tmp_path / "presets.toml"
    monkeypatch.setattr(presets_module, "presets_path", lambda: path)
    return path


def _scripted_repl(monkeypatch, lines: list[str]) -> None:
    """Feed the REPL a fixed sequence of user input lines."""
    inputs = iter(lines)

    def fake_read_line() -> str:
        try:
            return next(inputs)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr(cli, "_read_line", fake_read_line)


def _repl(**kwargs) -> None:
    cli._run_repl(
        provider_override=kwargs.get("provider_override"),
        model_override=kwargs.get("model_override"),
        verbose=kwargs.get("verbose", False),
    )


# --- REPL basics ---------------------------------------------------------


def test_repl_exits_on_exit(monkeypatch, capsys) -> None:
    _scripted_repl(monkeypatch, ["exit"])
    _repl()
    out = capsys.readouterr().out
    assert "interactive session" in out


def test_repl_exits_on_eof(monkeypatch, capsys) -> None:
    _scripted_repl(monkeypatch, [])
    _repl()
    assert "interactive session" in capsys.readouterr().out


def test_repl_help_lists_commands(monkeypatch, capsys) -> None:
    _scripted_repl(monkeypatch, ["help", "exit"])
    _repl()
    out = capsys.readouterr().out
    assert "preset run" in out
    assert "save <nickname>" in out


def test_repl_dash_dash_help_shows_help_not_generate(monkeypatch, capsys) -> None:
    _scripted_repl(monkeypatch, ["--help", "exit"])
    _repl()
    assert "preset run" in capsys.readouterr().out


def test_repl_incomplete_preset_commands_show_usage(
    monkeypatch, capsys, vault: Path
) -> None:
    # None of these may fall through to the AI generator.
    _scripted_repl(
        monkeypatch,
        ["preset search", "preset run", "preset run onlyname", "preset delete", "preset bogus", "exit"],
    )
    _repl()
    out = capsys.readouterr().out
    assert "Usage: preset search <text>" in out
    assert out.count("Usage: preset run <nickname> <input-file>") == 2
    assert "Usage: preset delete <nickname>" in out
    assert "Unknown preset command 'bogus'" in out


def test_repl_preset_save_hint(monkeypatch, capsys, vault: Path) -> None:
    _scripted_repl(monkeypatch, ["preset save x", "exit"])
    _repl()
    assert "use 'save <nickname>'" in capsys.readouterr().out


def test_repl_strips_pasted_meg_prefix(monkeypatch, capsys, vault: Path) -> None:
    add_preset(
        "loudnorm",
        "ffmpeg -i in.wav -af loudnorm out.wav",
        description="podcast",
        path=vault,
    )
    # Pasting shell-style commands must not fall through to the AI.
    _scripted_repl(monkeypatch, ["meg preset search podcast", "meg preset list", "exit"])
    _repl()
    out = capsys.readouterr().out
    assert out.count("loudnorm") >= 2


def test_repl_preset_run_ignores_powershell_call_operator(
    monkeypatch, capsys, vault: Path
) -> None:
    add_preset(
        "proxy",
        "ffmpeg -i input.mov -c:v libx264 output.mp4",
        path=vault,
    )
    # "& 'path'" paste artifact: the & must be dropped, not counted as an input.
    _scripted_repl(monkeypatch, ["preset run proxy & 'missing clip.mov'", "exit"])
    _repl()
    captured = capsys.readouterr()
    assert "expects 1 input" not in captured.err
    assert "missing clip.mov" in captured.out  # rendered against the real path


def test_successful_run_prints_done_with_output_path(
    monkeypatch, vault: Path, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    source = tmp_path / "clip.mov"
    source.write_bytes(b"fake")
    runner.invoke(
        app,
        ["preset", "save", "proxy", "ffmpeg -i input.mov -c:v libx264 output.mp4"],
    )
    monkeypatch.setattr(
        cli,
        "_run_approved_argv",
        lambda argv, **kwargs: SimpleNamespace(
            returncode=0, cancelled=False, stalled=False, stderr=""
        ),
    )

    result = runner.invoke(app, ["preset", "run", "proxy", str(source)], input="y\n")

    assert result.exit_code == 0
    assert "Done:" in result.stdout
    assert "clip_out.mp4" in result.stdout


def test_repl_empty_lines_ignored(monkeypatch, capsys) -> None:
    _scripted_repl(monkeypatch, ["", "   ", "exit"])
    _repl()
    assert "interactive session" in capsys.readouterr().out


# --- REPL generate + save -----------------------------------------------


def test_repl_generate_then_save_preset(monkeypatch, capsys, vault: Path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        cli,
        "create_provider",
        lambda config, override=None, model_override=None: FakeProvider(),
    )
    _scripted_repl(
        monkeypatch,
        [
            "convert mkv to h264 mp4",  # request (menu skipped: no TTY)
            "save proxy editorial proxy",  # save last command
            "presets",  # list
            "exit",
        ],
    )
    _repl()
    out = capsys.readouterr().out

    assert "ffmpeg -i input.mkv -c:v libx264 -c:a aac output.mp4" in out
    assert "Saved preset 'proxy'" in out

    saved = load_presets(vault)
    assert "proxy" in saved
    assert saved["proxy"].description == "editorial proxy"
    assert "{input}" in saved["proxy"].argv
    assert "{output}" in saved["proxy"].argv


def test_repl_save_without_generate_warns(monkeypatch, capsys, vault: Path) -> None:
    _scripted_repl(monkeypatch, ["save proxy", "exit"])
    _repl()
    assert "Nothing to save yet" in capsys.readouterr().out


def test_repl_preset_search_and_delete(monkeypatch, capsys, vault: Path) -> None:
    add_preset(
        "loudnorm",
        "ffmpeg -i in.wav -af loudnorm out.wav",
        description="podcast",
        path=vault,
    )
    _scripted_repl(
        monkeypatch,
        ["preset search podcast", "preset delete loudnorm", "presets", "exit"],
    )
    _repl()
    out = capsys.readouterr().out
    assert "loudnorm" in out
    assert "Preset deleted." in out
    assert "No presets saved yet" in out


def test_repl_unknown_preset_run_reports_error(monkeypatch, capsys, vault: Path) -> None:
    _scripted_repl(monkeypatch, ["preset run nope clip.mov", "exit"])
    _repl()
    err = capsys.readouterr().err
    assert "No preset named 'nope'" in err


def test_repl_provider_error_keeps_session_alive(monkeypatch, capsys) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MEG_PROVIDER", raising=False)
    _scripted_repl(monkeypatch, ["convert mkv to mp4", "help", "exit"])
    _repl()
    captured = capsys.readouterr()
    assert "No API key found" in captured.err
    assert "preset run" in captured.out  # help still worked afterwards


# --- generate menu save option ------------------------------------------


def test_generate_menu_save_option(monkeypatch, vault: Path) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        "meg.cli.create_provider",
        lambda config, override=None, model_override=None: FakeProvider(),
    )
    monkeypatch.setattr(cli, "_stdin_is_interactive", lambda: True)

    result = runner.invoke(
        app,
        ["convert mkv to h264 mp4"],
        input="s\nproxy\nquick proxy\nq\n",
    )

    assert result.exit_code == 0
    assert "[s]ave" in result.stdout
    assert "Saved preset 'proxy'" in result.stdout
    saved = load_presets(vault)
    assert saved["proxy"].description == "quick proxy"


# --- preset CLI subcommands ----------------------------------------------


def test_preset_save_list_search_delete_cli(vault: Path) -> None:
    save = runner.invoke(
        app,
        [
            "preset",
            "save",
            "proxy",
            "ffmpeg -i input.mov -c:v libx264 output.mp4",
            "--description",
            "editorial",
        ],
    )
    assert save.exit_code == 0
    assert "Saved preset 'proxy'" in save.stdout

    listing = runner.invoke(app, ["preset", "list"])
    assert listing.exit_code == 0
    assert "proxy" in listing.stdout
    assert "{input}" in listing.stdout

    found = runner.invoke(app, ["preset", "search", "editorial"])
    assert found.exit_code == 0
    assert "proxy" in found.stdout

    missing = runner.invoke(app, ["preset", "search", "nomatch"])
    assert missing.exit_code == 1

    deleted = runner.invoke(app, ["preset", "delete", "proxy"])
    assert deleted.exit_code == 0
    empty = runner.invoke(app, ["preset", "list"])
    assert "No presets saved yet" in empty.stdout


def test_preset_save_duplicate_fails_cli(vault: Path) -> None:
    command = "ffmpeg -i input.mov -c:v libx264 output.mp4"
    assert runner.invoke(app, ["preset", "save", "p", command]).exit_code == 0
    duplicate = runner.invoke(app, ["preset", "save", "p", command])
    assert duplicate.exit_code == 1
    assert "already exists" in duplicate.stderr


def test_preset_run_renders_command_for_new_input(
    vault: Path, tmp_path: Path
) -> None:
    source = tmp_path / "new clip.mov"
    source.write_bytes(b"fake")
    runner.invoke(
        app,
        ["preset", "save", "proxy", "ffmpeg -i input.mov -c:v libx264 output.mp4"],
    )

    # Decline the run approval; we only verify rendering + approval prompt.
    result = runner.invoke(app, ["preset", "run", "proxy", str(source)], input="n\n")

    assert "new clip.mov" in result.stdout
    assert "new clip_out.mp4" in result.stdout
    assert "Run this command?" in result.stdout
    assert result.exit_code == 1  # declined, nothing executed


def test_preset_run_unknown_nickname_fails(vault: Path) -> None:
    result = runner.invoke(app, ["preset", "run", "nope", "clip.mov"])
    assert result.exit_code == 1
    assert "No preset named 'nope'" in result.stderr
