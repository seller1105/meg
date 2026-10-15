"""Tests for ffprobe path extraction, parsing, and prompt context building."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from meg.ffprobe import (
    FfprobeError,
    FFPROBE_TIMEOUT_SECONDS,
    build_source_context,
    can_probe,
    clear_probe_cache,
    default_output_path,
    extract_ffmpeg_input_paths,
    extract_media_paths,
    format_media_summary,
    parse_ffprobe_json,
    probe_media_summary,
    run_ffprobe,
)


@pytest.fixture(autouse=True)
def _reset_probe_cache() -> None:
    clear_probe_cache()
    yield
    clear_probe_cache()


SAMPLE_PROBE_JSON = {
    "format": {
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "duration": "125.500",
    },
    "streams": [
        {
            "index": 0,
            "codec_type": "video",
            "codec_name": "prores",
            "width": 3840,
            "height": 2160,
            "pix_fmt": "yuv422p10le",
            "r_frame_rate": "24000/1001",
            "avg_frame_rate": "24000/1001",
            "color_primaries": "bt709",
            "color_transfer": "bt709",
            "color_space": "bt709",
            "field_order": "progressive",
        },
        {
            "index": 1,
            "codec_type": "audio",
            "codec_name": "pcm_s24le",
            "channels": 6,
            "channel_layout": "5.1",
            "sample_rate": "48000",
        },
    ],
}


def test_extract_media_paths_finds_quoted_and_plain_paths() -> None:
    text = 'scale "D:\\renders\\master.mov" and backup.mkv to 1080p'
    assert extract_media_paths(text) == [r"D:\renders\master.mov", "backup.mkv"]


def test_extract_media_paths_deduplicates_case_insensitive() -> None:
    text = "convert Clip.MOV and clip.mov to mp4"
    assert extract_media_paths(text) == ["Clip.MOV"]


def test_extract_ffmpeg_input_paths_handles_quotes() -> None:
    command = 'ffmpeg -i "in file.mov" -i second.mkv -c copy out.mp4'
    assert extract_ffmpeg_input_paths(command) == ["in file.mov", "second.mkv"]


def test_parse_ffprobe_json_summarizes_video_and_audio() -> None:
    summary = parse_ffprobe_json("master.mov", SAMPLE_PROBE_JSON)
    assert summary.container.startswith("mov")
    assert summary.duration == "2:05.500"
    assert summary.duration_seconds == 125.5
    assert len(summary.streams) == 2
    assert summary.streams[0].kind == "video"
    assert "3840x2160" in summary.streams[0].details
    assert "23.976 fps" in summary.streams[0].details
    assert "yuv422p10le" in summary.streams[0].details
    assert "bt709" in summary.streams[0].details
    assert summary.streams[1].kind == "audio"
    assert "5.1" in summary.streams[1].details
    assert "48000 Hz" in summary.streams[1].details


def test_format_media_summary_is_compact() -> None:
    summary = parse_ffprobe_json("master.mov", SAMPLE_PROBE_JSON)
    rendered = format_media_summary(summary)
    assert rendered.startswith("Source: master.mov")
    assert "default output: master_out.mov" in rendered
    assert "container:" in rendered
    assert "video[0]:" in rendered
    assert "audio[1]:" in rendered
    assert "{" not in rendered


def test_default_output_path_inserts_suffix_before_extension() -> None:
    assert default_output_path(r"D:\renders\clip.mov") == r"D:\renders\clip_out.mov"
    assert Path(default_output_path("/media/shot.mxf")) == Path("/media/shot_out.mxf")


def test_build_source_context_skips_missing_files(tmp_path: Path) -> None:
    missing = tmp_path / "ghost.mkv"
    assert build_source_context([str(missing)], ffprobe_bin="ffprobe") is None


def test_can_probe_accepts_local_readable_file(tmp_path: Path) -> None:
    media = tmp_path / "clip.mov"
    media.write_bytes(b"fake")
    assert can_probe(media) is True


def test_can_probe_rejects_unc_paths() -> None:
    assert can_probe(r"\\server\share\clip.mov") is False


def test_can_probe_rejects_oversized_file(tmp_path: Path) -> None:
    media = tmp_path / "huge.mov"
    media.write_bytes(b"x" * 64)
    assert can_probe(media, max_bytes=32) is False


@pytest.mark.skipif(os.name == "nt", reason="Unix-style permission test")
def test_can_probe_rejects_unreadable_file(tmp_path: Path) -> None:
    media = tmp_path / "locked.mov"
    media.write_bytes(b"fake")
    media.chmod(0o000)
    try:
        assert can_probe(media) is False
    finally:
        media.chmod(0o644)


def test_build_source_context_skips_unc_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def fail_run_ffprobe(*args, **kwargs):
        nonlocal called
        called = True
        return SAMPLE_PROBE_JSON

    monkeypatch.setattr("meg.ffprobe.run_ffprobe", fail_run_ffprobe)
    assert build_source_context([r"\\server\share\clip.mov"], ffprobe_bin="ffprobe") is None
    assert called is False


def test_build_source_context_probes_existing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = tmp_path / "clip.mov"
    media.write_bytes(b"not real media")

    def fake_run_ffprobe(path: str, *, ffprobe_bin: str = "ffprobe") -> dict[str, object]:
        assert path == str(media)
        assert ffprobe_bin == "fake-ffprobe"
        return SAMPLE_PROBE_JSON

    monkeypatch.setattr("meg.ffprobe.run_ffprobe", fake_run_ffprobe)

    context = build_source_context([str(media)], ffprobe_bin="fake-ffprobe")
    assert context is not None
    assert "Verified source metadata" in context
    assert "3840x2160" in context
    assert "Preserve every probed spec" in context
    assert "do not overwrite the input" in context
    assert "clip_out.mov" in context


def test_probe_media_summary_caches_results_for_same_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = tmp_path / "clip.mov"
    media.write_bytes(b"not real media")
    calls = 0

    def fake_run_ffprobe(path: str, *, ffprobe_bin: str = "ffprobe") -> dict[str, object]:
        nonlocal calls
        calls += 1
        return SAMPLE_PROBE_JSON

    monkeypatch.setattr("meg.ffprobe.run_ffprobe", fake_run_ffprobe)

    first = probe_media_summary(media, ffprobe_bin="fake-ffprobe")
    second = probe_media_summary(media, ffprobe_bin="fake-ffprobe")

    assert calls == 1
    assert first is not None
    assert second == first


def test_probe_media_summary_reprobes_after_file_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = tmp_path / "clip.mov"
    media.write_bytes(b"v1")
    calls = 0

    def fake_run_ffprobe(path: str, *, ffprobe_bin: str = "ffprobe") -> dict[str, object]:
        nonlocal calls
        calls += 1
        return SAMPLE_PROBE_JSON

    monkeypatch.setattr("meg.ffprobe.run_ffprobe", fake_run_ffprobe)

    probe_media_summary(media, ffprobe_bin="fake-ffprobe")
    media.write_bytes(b"v2-longer")
    probe_media_summary(media, ffprobe_bin="fake-ffprobe")

    assert calls == 2


def test_run_ffprobe_raises_when_binary_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = tmp_path / "clip.mov"
    media.write_bytes(b"x")

    def raise_not_found(*args, **kwargs):
        _ = args, kwargs
        raise FileNotFoundError(2, "No such file or directory", "ffprobe")

    monkeypatch.setattr("meg.ffprobe.subprocess.run", raise_not_found)

    with pytest.raises(FfprobeError, match="ffprobe was not found"):
        run_ffprobe(str(media), ffprobe_bin="ffprobe")


def test_build_source_context_skips_when_ffprobe_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = tmp_path / "clip.mov"
    media.write_bytes(b"fake")

    def raise_not_found(*args, **kwargs):
        _ = args, kwargs
        raise FileNotFoundError(2, "No such file or directory", "ffprobe")

    monkeypatch.setattr("meg.ffprobe.subprocess.run", raise_not_found)

    assert build_source_context([str(media)], ffprobe_bin="ffprobe") is None


def test_run_ffprobe_raises_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media = tmp_path / "bad.mov"
    media.write_bytes(b"x")

    class Completed:
        returncode = 1
        stdout = ""
        stderr = "Invalid data"

    monkeypatch.setattr("meg.ffprobe.subprocess.run", lambda *args, **kwargs: Completed())

    with pytest.raises(FfprobeError, match="Invalid data"):
        run_ffprobe(str(media), ffprobe_bin="ffprobe")


def test_run_ffprobe_raises_on_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media = tmp_path / "slow.mov"
    media.write_bytes(b"x")

    def raise_timeout(*args, **kwargs):
        assert kwargs.get("timeout") == FFPROBE_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr("meg.ffprobe.subprocess.run", raise_timeout)

    with pytest.raises(FfprobeError, match="timed out"):
        run_ffprobe(str(media), ffprobe_bin="ffprobe")


def test_run_ffprobe_parses_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media = tmp_path / "ok.mov"
    media.write_bytes(b"x")

    class Completed:
        returncode = 0
        stdout = json.dumps(SAMPLE_PROBE_JSON)
        stderr = ""

    monkeypatch.setattr("meg.ffprobe.subprocess.run", lambda *args, **kwargs: Completed())

    payload = run_ffprobe(str(media), ffprobe_bin="ffprobe")
    assert payload["format"]["duration"] == "125.500"
