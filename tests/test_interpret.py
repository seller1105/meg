"""Tests for the Error Interpreter (failed-run diagnosis + fix flow)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

import meg.cli as cli
from meg.cli import app
from meg.exec import ExecutionResult
from meg.prompt import (
    PromptParseError,
    build_interpret_prompt,
    parse_interpret_response,
)

runner = CliRunner()

_GENERATE_RESPONSE = "\n".join(
    [
        "COMMAND:",
        "ffmpeg -i input.mkv -c:v libx265 -tag:v hvc1 output.mp4",
        "EXPLANATION:",
        "- Encodes H.265 with QuickTime-compatible tag.",
    ]
)

_INTERPRET_RESPONSE = "\n".join(
    [
        "DIAGNOSIS:",
        "- Your ffmpeg build has no libx265 encoder (\"Unknown encoder 'libx265'\").",
        "- Falling back to libx264 keeps the command working everywhere.",
        "COMMAND:",
        "ffmpeg -i input.mkv -c:v libx264 output.mp4",
    ]
)

_INTERPRET_NONE_RESPONSE = "\n".join(
    [
        "DIAGNOSIS:",
        "- The input file does not exist; no flag change can fix a missing file.",
        "COMMAND:",
        "NONE",
    ]
)


# --- prompt building ------------------------------------------------------


def test_build_interpret_prompt_includes_failure_details() -> None:
    bundle = build_interpret_prompt(
        "ffmpeg -i in.mov out.mp4",
        "Unknown encoder 'libx265'",
        request="convert to h265",
        source_context="Verified source metadata",
    )
    assert "Failed command: ffmpeg -i in.mov out.mp4" in bundle.user
    assert "Unknown encoder 'libx265'" in bundle.user
    assert "Original request: convert to h265" in bundle.user
    assert "Verified source metadata" in bundle.user
    assert "DIAGNOSIS:" in bundle.system


# --- response parsing -----------------------------------------------------


def test_parse_interpret_response_with_command() -> None:
    parsed = parse_interpret_response(_INTERPRET_RESPONSE)
    assert "libx265 encoder" in parsed.diagnosis
    assert parsed.command == "ffmpeg -i input.mkv -c:v libx264 output.mp4"


def test_parse_interpret_response_none_command() -> None:
    parsed = parse_interpret_response(_INTERPRET_NONE_RESPONSE)
    assert parsed.command is None
    assert "does not exist" in parsed.diagnosis


@pytest.mark.parametrize(
    "bad",
    [
        "no sections at all",
        "DIAGNOSIS:\n- something\nCOMMAND:\nrm -rf /",
        "DIAGNOSIS:\n- multi\nCOMMAND:\nffmpeg -i a.mov\nout.mp4",
    ],
)
def test_parse_interpret_response_rejects_invalid(bad: str) -> None:
    with pytest.raises(PromptParseError):
        parse_interpret_response(bad)


# --- CLI fix flow ---------------------------------------------------------


class ScriptedProvider:
    """Returns queued responses; records the user prompts it was given."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.user_prompts: list[str] = []

    def complete(self, system: str, user: str) -> str:
        _ = system
        self.user_prompts.append(user)
        return self.responses.pop(0)


def _wire(monkeypatch, provider: ScriptedProvider, run_results: list[ExecutionResult]):
    results = list(run_results)

    def fake_run(argv, *, interactive, source_duration_seconds=None, allow_stdin_cancel=True):
        _ = argv, interactive, source_duration_seconds, allow_stdin_cancel
        return results.pop(0)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("meg.cli.create_provider", lambda *a, **k: provider)
    monkeypatch.setattr("meg.cli._stdin_is_interactive", lambda: True)
    monkeypatch.setattr("meg.cli._run_approved_argv", fake_run)


def test_failed_run_offers_fix_and_applies_correction(monkeypatch) -> None:
    provider = ScriptedProvider([_GENERATE_RESPONSE, _INTERPRET_RESPONSE])
    _wire(
        monkeypatch,
        provider,
        [ExecutionResult(returncode=1, stderr="Unknown encoder 'libx265'")],
    )

    # run -> approve -> decline stderr tail -> fix -> quit at corrected menu
    result = runner.invoke(
        app, ["convert mkv to h265 mp4"], input="r\ny\nn\nf\nq\n"
    )

    assert result.exit_code == 0
    assert "[f]ix with AI" in result.stdout
    assert "libx265 encoder" in result.stdout  # diagnosis shown
    assert "ffmpeg -i input.mkv -c:v libx264 output.mp4" in result.stdout
    # interpret call carried the failure evidence
    assert len(provider.user_prompts) == 2
    assert "Unknown encoder 'libx265'" in provider.user_prompts[1]
    assert "Failed command:" in provider.user_prompts[1]


def test_fix_with_no_possible_command_keeps_menu(monkeypatch) -> None:
    provider = ScriptedProvider([_GENERATE_RESPONSE, _INTERPRET_NONE_RESPONSE])
    _wire(
        monkeypatch,
        provider,
        [ExecutionResult(returncode=1, stderr="No such file or directory")],
    )

    result = runner.invoke(
        app, ["convert mkv to h265 mp4"], input="r\ny\nn\nf\nq\n"
    )

    assert result.exit_code == 0
    assert "No command change can fix this failure" in result.stdout


def test_declined_approval_does_not_offer_fix(monkeypatch) -> None:
    provider = ScriptedProvider([_GENERATE_RESPONSE])
    _wire(monkeypatch, provider, [])

    # run -> decline approval -> quit
    result = runner.invoke(app, ["convert mkv to h265 mp4"], input="r\nn\nq\n")

    assert result.exit_code == 0
    assert "[f]ix with AI" not in result.stdout


def test_cancelled_run_does_not_offer_fix(monkeypatch) -> None:
    provider = ScriptedProvider([_GENERATE_RESPONSE])
    _wire(
        monkeypatch,
        provider,
        [ExecutionResult(returncode=130, stderr="", cancelled=True)],
    )

    result = runner.invoke(app, ["convert mkv to h265 mp4"], input="r\ny\nn\nq\n")

    assert result.exit_code == 0
    assert "[f]ix with AI" not in result.stdout


def test_fix_then_edit_uses_corrected_command_as_previous(monkeypatch) -> None:
    revised = "\n".join(
        [
            "COMMAND:",
            "ffmpeg -i input.mkv -c:v libx264 -crf 18 output.mp4",
            "EXPLANATION:",
            "- Higher quality CRF as requested.",
        ]
    )
    provider = ScriptedProvider([_GENERATE_RESPONSE, _INTERPRET_RESPONSE, revised])
    _wire(
        monkeypatch,
        provider,
        [ExecutionResult(returncode=1, stderr="Unknown encoder 'libx265'")],
    )

    # run -> approve -> no tail -> fix -> edit corrected -> feedback -> quit
    result = runner.invoke(
        app,
        ["convert mkv to h265 mp4"],
        input="r\ny\nn\nf\ne\nuse crf 18\nq\n",
    )

    assert result.exit_code == 0
    assert "crf 18" in result.stdout
    # revise prompt should reference the AI-corrected command, not the failed one
    assert "Previous command: ffmpeg -i input.mkv -c:v libx264 output.mp4" in provider.user_prompts[2]
