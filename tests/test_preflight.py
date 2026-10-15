"""Tests for pre-flight ffmpeg command validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from meg.ffprobe import MediaSummary, StreamSummary
from meg.preflight import (
    analyze_preflight,
    has_preflight_errors,
    preflight_from_command,
)


def _video_summary(path: str, *, audio_tracks: int = 1) -> MediaSummary:
    streams: list[StreamSummary] = [
        StreamSummary(
            index=0,
            kind="video",
            codec="h264",
            details="h264 (1920x1080, 24 fps)",
        )
    ]
    for idx in range(audio_tracks):
        streams.append(
            StreamSummary(
                index=idx + 1,
                kind="audio",
                codec="aac",
                details="aac (stereo, 48000 Hz)",
            )
        )
    return MediaSummary(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration="00:01:00.000",
        duration_seconds=60.0,
        streams=tuple(streams),
    )


def test_preflight_rejects_missing_input_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.mov"
    findings = analyze_preflight(
        ["ffmpeg", "-i", str(missing), "-c", "copy", "out.mp4"],
        validated_input_paths=[str(missing)],
    )
    assert has_preflight_errors(findings)
    assert any(f.code == "missing_input_file" for f in findings)


def test_preflight_skips_missing_placeholder_inputs() -> None:
    findings = analyze_preflight(
        ["ffmpeg", "-i", "input.mkv", "-c", "copy", "out.mp4"],
    )
    assert not any(f.code == "missing_input_file" for f in findings)


def test_preflight_rejects_missing_i_value() -> None:
    findings = analyze_preflight(["ffmpeg", "-i"])
    assert has_preflight_errors(findings)
    assert any(f.code == "missing_input" for f in findings)


def test_preflight_rejects_copy_with_video_filter() -> None:
    findings = analyze_preflight(
        ["ffmpeg", "-i", "in.mp4", "-vf", "scale=1920:1080", "-c:v", "copy", "out.mp4"],
    )
    assert has_preflight_errors(findings)
    assert any(f.code == "copy_with_video_filter" for f in findings)


def test_preflight_rejects_global_copy_with_video_filter() -> None:
    findings = analyze_preflight(
        ["ffmpeg", "-i", "in.mp4", "-vf", "scale=1920:1080", "-c", "copy", "out.mp4"],
    )
    assert has_preflight_errors(findings)
    assert any(f.code == "copy_with_video_filter" for f in findings)


def test_preflight_rejects_copy_with_audio_filter() -> None:
    findings = analyze_preflight(
        [
            "ffmpeg",
            "-i",
            "in.mp4",
            "-af",
            "loudnorm=I=-23:TP=-1:LRA=7",
            "-c:a",
            "copy",
            "out.mp4",
        ],
    )
    assert has_preflight_errors(findings)
    assert any(f.code == "copy_with_audio_filter" for f in findings)


def test_preflight_rejects_copy_with_filter_complex() -> None:
    findings = analyze_preflight(
        [
            "ffmpeg",
            "-i",
            "in.mp4",
            "-filter_complex",
            "[0:v]yadif[outv]",
            "-map",
            "[outv]",
            "-map",
            "0:a",
            "-c",
            "copy",
            "out.mp4",
        ],
    )
    assert has_preflight_errors(findings)
    assert any(f.code == "copy_with_filter_complex" for f in findings)


def test_preflight_warns_on_remux_request_with_video_encoder() -> None:
    findings = analyze_preflight(
        ["ffmpeg", "-i", "in.mkv", "-c:v", "libx264", "-c:a", "copy", "out.mp4"],
        user_request="Remux only — copy all streams, change container to MP4",
    )
    assert not has_preflight_errors(findings)
    assert any(f.code == "unexpected_video_reencode" for f in findings)


def test_preflight_accepts_clean_remux_command() -> None:
    findings = analyze_preflight(
        ["ffmpeg", "-i", "in.mov", "-c", "copy", "out.mp4"],
        user_request="Remux only — copy all streams, change container to MP4",
    )
    assert findings == ()


def test_preflight_accepts_filter_with_reencode(tmp_path: Path) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    findings = analyze_preflight(
        [
            "ffmpeg",
            "-i",
            str(source),
            "-vf",
            "scale=1920:1080",
            "-c:v",
            "libx264",
            "-c:a",
            "copy",
            str(tmp_path / "out.mp4"),
        ],
    )
    assert not has_preflight_errors(findings)
    assert findings == ()


def test_preflight_rejects_invalid_map_with_probe_data(tmp_path: Path) -> None:
    source = tmp_path / "clip.mov"
    source.write_bytes(b"x")
    summary = _video_summary(str(source), audio_tracks=2)
    findings = analyze_preflight(
        [
            "ffmpeg",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:2",
            "-c",
            "copy",
            str(tmp_path / "out.mov"),
        ],
        probed_sources={str(source): summary},
    )
    assert has_preflight_errors(findings)
    assert any(f.code == "invalid_map" for f in findings)


def test_preflight_accepts_valid_map_with_probe_data(tmp_path: Path) -> None:
    source = tmp_path / "clip.mov"
    source.write_bytes(b"x")
    summary = _video_summary(str(source), audio_tracks=2)
    findings = analyze_preflight(
        [
            "ffmpeg",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:1",
            "-c",
            "copy",
            str(tmp_path / "out.mov"),
        ],
        probed_sources={str(source): summary},
    )
    assert not has_preflight_errors(findings)
    assert findings == ()


def test_preflight_from_command_parses_quoted_paths(tmp_path: Path) -> None:
    missing = tmp_path / "missing clip.mov"
    findings = preflight_from_command(
        f'ffmpeg -i "{missing}" -c copy out.mp4',
        probed_paths=[str(missing)],
    )
    assert has_preflight_errors(findings)
    assert any("missing clip.mov" in f.message for f in findings)


def test_preflight_ignores_non_ffmpeg_commands() -> None:
    assert analyze_preflight(["ffprobe", "-version"]) == ()
    assert preflight_from_command("ffprobe -show_streams in.mp4") == ()


def test_preflight_skips_remote_inputs() -> None:
    findings = analyze_preflight(
        ["ffmpeg", "-i", "https://example.com/in.mp4", "-c", "copy", "out.mp4"],
    )
    assert not any(f.code == "missing_input_file" for f in findings)
