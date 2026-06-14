"""Tests for prompt construction and response parsing."""

from __future__ import annotations

import pytest

from meg.prompt import (
    PromptParseError,
    build_explain_prompt,
    build_generate_prompt,
    parse_explain_response,
    parse_generate_response,
)


def test_build_generate_prompt_includes_request() -> None:
    bundle = build_generate_prompt("convert mkv to mp4")
    assert "Request: convert mkv to mp4" in bundle.user
    assert "COMMAND:" in bundle.system
    assert "EXPLANATION:" in bundle.system


def test_build_generate_prompt_verbose_changes_detail_instruction() -> None:
    concise = build_generate_prompt("convert mkv to mp4", verbose=False)
    detailed = build_generate_prompt("convert mkv to mp4", verbose=True)
    assert "concise and operator-focused" in concise.user
    assert "deeper rationale" in detailed.user


def test_build_generate_prompt_includes_source_context() -> None:
    context = "Verified source metadata\nSource: clip.mov\n  video[0]: h264, 1920x1080"
    bundle = build_generate_prompt("scale clip.mov to 720p", source_context=context)
    assert "Verified source metadata" in bundle.user
    assert "1920x1080" in bundle.user
    assert "scale clip.mov to 720p" in bundle.user


def test_system_prompt_preserves_probed_source_defaults() -> None:
    from meg.prompt import SYSTEM_PROMPT_GENERATE

    assert "Never write output to the Source path" in SYSTEM_PROMPT_GENERATE
    assert "clip.mov → clip_out.mov" in SYSTEM_PROMPT_GENERATE
    assert "Preserve all probed specs" in SYSTEM_PROMPT_GENERATE
    assert "Do not add delivery presets" in SYSTEM_PROMPT_GENERATE


def test_parse_generate_response_happy_path() -> None:
    parsed = parse_generate_response(
        "\n".join(
            [
                "COMMAND:",
                "ffmpeg -i input.mkv -c:v libx264 -c:a aac output.mp4",
                "EXPLANATION:",
                "- Encodes video with H.264 for compatibility.",
                "- Encodes audio as AAC for MP4 container support.",
            ]
        )
    )
    assert parsed.command.startswith("ffmpeg -i input.mkv")
    assert "H.264" in parsed.explanation


def test_parse_generate_response_accepts_code_fenced_payload() -> None:
    parsed = parse_generate_response(
        "\n".join(
            [
                "```text",
                "COMMAND:",
                "ffmpeg -i input.mkv -c:v libx264 -c:a aac output.mp4",
                "EXPLANATION:",
                "- Encodes video with H.264 for compatibility.",
                "```",
            ]
        )
    )
    assert parsed.command == "ffmpeg -i input.mkv -c:v libx264 -c:a aac output.mp4"


def test_parse_generate_response_requires_sections() -> None:
    with pytest.raises(PromptParseError, match="Expected COMMAND and EXPLANATION"):
        parse_generate_response("ffmpeg -i input.mkv output.mp4")


def test_parse_generate_response_requires_ffmpeg_prefix() -> None:
    with pytest.raises(PromptParseError, match="must start with 'ffmpeg '"):
        parse_generate_response(
            "\n".join(
                [
                    "COMMAND:",
                    "python script.py",
                    "EXPLANATION:",
                    "- Not an ffmpeg command.",
                ]
            )
        )


def test_build_explain_prompt_includes_command() -> None:
    bundle = build_explain_prompt("ffmpeg -i input.mp4 -c copy output.mp4")
    assert "Command: ffmpeg -i input.mp4 -c copy output.mp4" in bundle.user
    assert "EXPLANATION:" in bundle.system


def test_build_explain_prompt_verbose_changes_detail_instruction() -> None:
    concise = build_explain_prompt("ffmpeg -i input.mp4 -c copy output.mp4", verbose=False)
    detailed = build_explain_prompt("ffmpeg -i input.mp4 -c copy output.mp4", verbose=True)
    assert "concise and operator-focused" in concise.user
    assert "deeper technical rationale" in detailed.user


def test_parse_explain_response_happy_path() -> None:
    parsed = parse_explain_response(
        "\n".join(
            [
                "EXPLANATION:",
                "- `-i input.mp4` sets the input file.",
                "- `-c copy` remuxes streams without re-encoding.",
            ]
        )
    )
    assert "remuxes streams" in parsed.explanation


def test_parse_explain_response_accepts_code_fenced_payload() -> None:
    parsed = parse_explain_response(
        "\n".join(
            [
                "```text",
                "EXPLANATION:",
                "- `-i input.mp4` sets the input file.",
                "```",
            ]
        )
    )
    assert "input file" in parsed.explanation


def test_parse_explain_response_requires_section() -> None:
    with pytest.raises(PromptParseError, match="Expected EXPLANATION"):
        parse_explain_response("This is not structured output.")


def test_parse_generate_response_requires_single_line_command() -> None:
    with pytest.raises(PromptParseError, match="single line"):
        parse_generate_response(
            "\n".join(
                [
                    "COMMAND:",
                    "ffmpeg -i input.mkv \\",
                    "-c:v libx264 output.mp4",
                    "EXPLANATION:",
                    "- Multi-line command should fail parser.",
                ]
            )
        )
