"""Tests for .env auto-loading (meg.config.load_env_files)."""

from __future__ import annotations

from pathlib import Path

from meg.config import _parse_env_file, load_env_files


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


# --- parsing --------------------------------------------------------------


def test_parse_env_file_basic(tmp_path: Path) -> None:
    env = _write(
        tmp_path / ".env",
        "\n".join(
            [
                "# comment",
                "",
                "ANTHROPIC_API_KEY=sk-test-123",
                "export MEG_PROVIDER=anthropic",
                'QUOTED="hello world"',
                "SINGLE='single quoted'",
                "SPACED = padded ",
                "not a valid line",
                "=nokey",
                "BAD KEY=x",
            ]
        ),
    )
    parsed = _parse_env_file(env)
    assert parsed == {
        "ANTHROPIC_API_KEY": "sk-test-123",
        "MEG_PROVIDER": "anthropic",
        "QUOTED": "hello world",
        "SINGLE": "single quoted",
        "SPACED": "padded",
    }


def test_parse_env_file_missing_returns_empty(tmp_path: Path) -> None:
    assert _parse_env_file(tmp_path / "nope.env") == {}


# --- loading & precedence -------------------------------------------------


def test_load_env_files_applies_to_environ(tmp_path: Path) -> None:
    env_file = _write(tmp_path / ".env", "ANTHROPIC_API_KEY=from-dotenv")
    environ: dict[str, str] = {}
    applied = load_env_files([env_file], environ=environ)
    assert environ["ANTHROPIC_API_KEY"] == "from-dotenv"
    assert applied == {"ANTHROPIC_API_KEY": "from-dotenv"}


def test_real_env_vars_always_win(tmp_path: Path) -> None:
    env_file = _write(tmp_path / ".env", "ANTHROPIC_API_KEY=from-dotenv")
    environ = {"ANTHROPIC_API_KEY": "from-real-env"}
    applied = load_env_files([env_file], environ=environ)
    assert environ["ANTHROPIC_API_KEY"] == "from-real-env"
    assert applied == {}


def test_cwd_env_wins_over_home_env(tmp_path: Path) -> None:
    cwd_env = _write(tmp_path / "cwd.env", "MEG_PROVIDER=openai")
    home_env = _write(
        tmp_path / "home.env", "MEG_PROVIDER=anthropic\nOPENAI_API_KEY=sk-home"
    )
    environ: dict[str, str] = {}
    load_env_files([cwd_env, home_env], environ=environ)
    assert environ["MEG_PROVIDER"] == "openai"  # first file wins
    assert environ["OPENAI_API_KEY"] == "sk-home"  # unique keys still merge


def test_missing_files_are_skipped(tmp_path: Path) -> None:
    environ: dict[str, str] = {}
    applied = load_env_files(
        [tmp_path / "absent.env", tmp_path / "also-absent.env"], environ=environ
    )
    assert applied == {}
    assert environ == {}


# --- end to end through the CLI ------------------------------------------


def test_cli_picks_up_dotenv_key(monkeypatch, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from meg.cli import app

    _write(tmp_path / ".env", "ANTHROPIC_API_KEY=sk-from-dotenv")
    monkeypatch.chdir(tmp_path)
    # setenv-then-delenv guarantees monkeypatch restores the pre-test state
    # even though load_env_files writes into the real os.environ.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MEG_PROVIDER", raising=False)

    captured: dict[str, str] = {}

    class FakeProvider:
        def complete(self, system: str, user: str) -> str:
            _ = system, user
            return "COMMAND:\nffmpeg -i input.mkv output.mp4\nEXPLANATION:\n- ok"

    def fake_create_provider(config, override=None, model_override=None):
        captured["key"] = config.anthropic_api_key
        return FakeProvider()

    monkeypatch.setattr("meg.cli.create_provider", fake_create_provider)

    result = CliRunner().invoke(app, ["convert mkv to mp4"])

    assert result.exit_code == 0
    assert captured["key"] == "sk-from-dotenv"
