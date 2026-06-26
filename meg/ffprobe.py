"""Run ffprobe on local media files and build compact prompt context."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path


class FfprobeError(RuntimeError):
    """Raised when ffprobe cannot analyze a media file."""


# Do not probe files larger than this (50 GiB).
MAX_PROBE_FILE_BYTES = 50 * 1024**3

# Abort ffprobe if it does not finish within this many seconds.
FFPROBE_TIMEOUT_SECONDS = 30.0


# Extensions Meg treats as probe-worthy local media paths.
_MEDIA_EXTENSIONS = (
    "aac",
    "avi",
    "flac",
    "m2ts",
    "m4a",
    "m4v",
    "mkv",
    "mov",
    "mp3",
    "mp4",
    "mpeg",
    "mpg",
    "mts",
    "mxf",
    "ogv",
    "ts",
    "vob",
    "wav",
    "webm",
    "wmv",
)

_EXT_GROUP = "|".join(re.escape(ext) for ext in _MEDIA_EXTENSIONS)

# Quoted paths, Windows drive paths, or bare filenames ending in a media extension.
_MEDIA_PATH_PATTERN = re.compile(
    r"(?:"
    r'["\'](?P<quoted>[^"\']+\.(?:' + _EXT_GROUP + r'))["\']'
    r"|(?P<windows>[A-Za-z]:\\(?:[^\\/\\s\"\']+\\)*[^\\/\\s\"\']+\.(?:"
    + _EXT_GROUP
    + r"))"
    r"|(?P<plain>(?<![\w.\-/\\])(?:[\w.\-/\\]+/)*[\w.\-/\\]+\.(?:"
    + _EXT_GROUP
    + r")(?![\w.\-/\\]))"
    r")",
    re.IGNORECASE,
)

# FFmpeg -i inputs in explain mode (optional quotes, stops at next flag).
_FFMPEG_INPUT_PATTERN = re.compile(
    r'-i\s+(?:"([^"]+)"|\'([^\']+)\'|(\S+))',
    re.IGNORECASE,
)

_SKIP_COLOR_VALUES = ("", "unknown", "unspecified")
_SKIP_FIELD_ORDER = ("", "unknown", "progressive")


@dataclass(frozen=True)
class StreamSummary:
    """One audio or video stream extracted from ffprobe JSON."""

    index: int
    kind: str  # "video" or "audio"
    codec: str
    details: str


@dataclass(frozen=True)
class MediaSummary:
    """Compact, human-readable facts about one media file."""

    path: str
    container: str
    duration: str
    duration_seconds: float | None
    streams: tuple[StreamSummary, ...]


@dataclass(frozen=True)
class _ProbeCacheKey:
    """Cache key from a resolved path plus file identity."""

    resolved_path: str
    mtime_ns: int
    size: int


_PROBE_SUMMARY_CACHE: dict[_ProbeCacheKey, MediaSummary] = {}


def extract_media_paths(text: str) -> list[str]:
    """Find plausible local media file paths mentioned in plain text."""
    seen: set[str] = set()
    paths: list[str] = []
    for match in _MEDIA_PATH_PATTERN.finditer(text):
        candidate = match.group("quoted") or match.group("windows") or match.group("plain")
        if candidate is None:
            continue
        normalized = candidate.strip().strip("'\"")
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        paths.append(normalized)
    return paths


def default_output_path(input_path: str, *, suffix: str = "_out") -> str:
    """Derive a non-destructive output path beside the source file."""
    path = Path(input_path)
    return str(path.with_name(f"{path.stem}{suffix}{path.suffix}"))


def _is_unc_path(path: Path) -> bool:
    """Return True for Windows UNC paths (\\\\server\\share\\...)."""
    raw = str(path)
    if raw.startswith("\\\\"):
        return True
    return path.as_posix().startswith("//")


def can_probe(
    path: str | Path,
    *,
    max_bytes: int = MAX_PROBE_FILE_BYTES,
) -> bool:
    """Return True when a path is safe to ffprobe (local, readable, within size budget)."""
    candidate = Path(path)
    if _is_unc_path(candidate):
        return False

    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        return False

    if _is_unc_path(resolved):
        return False
    if not resolved.is_file():
        return False
    if not os.access(resolved, os.R_OK):
        return False

    try:
        if resolved.stat().st_size > max_bytes:
            return False
    except OSError:
        return False

    return True


def clear_probe_cache() -> None:
    """Clear the in-process ffprobe result cache (mainly for tests)."""
    _PROBE_SUMMARY_CACHE.clear()


def _probe_cache_key(path: str | Path) -> _ProbeCacheKey | None:
    """Build a cache key from a resolved file path, mtime, and size."""
    candidate = Path(path)
    try:
        resolved = candidate.resolve(strict=False)
        stat = resolved.stat()
    except OSError:
        return None
    return _ProbeCacheKey(
        resolved_path=os.path.normcase(str(resolved)),
        mtime_ns=stat.st_mtime_ns,
        size=stat.st_size,
    )


def probe_media_summary(
    raw_path: str | Path,
    *,
    ffprobe_bin: str | None = None,
) -> MediaSummary | None:
    """Return a cached or freshly probed MediaSummary for one local file."""
    if not can_probe(raw_path):
        return None

    path = Path(raw_path)
    cache_key = _probe_cache_key(path)
    if cache_key is None:
        return None

    cached = _PROBE_SUMMARY_CACHE.get(cache_key)
    if cached is not None:
        return cached

    binary = ffprobe_bin or shutil.which("ffprobe")
    if binary is None:
        return None

    try:
        payload = run_ffprobe(str(path), ffprobe_bin=binary)
        summary = parse_ffprobe_json(str(path), payload)
    except FfprobeError:
        return None

    _PROBE_SUMMARY_CACHE[cache_key] = summary
    return summary


def extract_ffmpeg_input_paths(command: str) -> list[str]:
    """Find -i input paths from an FFmpeg command string."""
    seen: set[str] = set()
    paths: list[str] = []
    for match in _FFMPEG_INPUT_PATTERN.finditer(command):
        candidate = match.group(1) or match.group(2) or match.group(3)
        if candidate is None:
            continue
        normalized = candidate.strip()
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        paths.append(normalized)
    return paths


def _format_fps(rate: str | None) -> str | None:
    """Turn ffprobe frame-rate strings like 30000/1001 into a short label."""
    if not rate or rate in ("0/0", "N/A"):
        return None
    if "/" in rate:
        try:
            value = float(Fraction(rate))
        except (ValueError, ZeroDivisionError):
            return rate
    else:
        try:
            value = float(rate)
        except ValueError:
            return rate
    if abs(value - round(value)) < 0.01:
        return f"{int(round(value))} fps"
    return f"{value:.3f} fps"


def _format_duration(seconds: str | None) -> str:
    """Format duration seconds into H:MM:SS.mmm for prompt context."""
    if not seconds:
        return "unknown"
    try:
        total = float(seconds)
    except ValueError:
        return seconds
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    secs = total % 60
    if hours:
        return f"{hours}:{minutes:02d}:{secs:06.3f}"
    return f"{minutes}:{secs:06.3f}"


def _color_label(stream: dict[str, str | int | float | None]) -> str | None:
    """Combine color primaries / transfer / matrix into one short token."""
    parts: list[str] = []
    for key in ("color_primaries", "color_transfer", "color_space"):
        value = stream.get(key)
        if isinstance(value, str) and value not in _SKIP_COLOR_VALUES:
            parts.append(value)
    if not parts:
        return None
    unique: list[str] = []
    for part in parts:
        if part not in unique:
            unique.append(part)
    return "/".join(unique)


def _summarize_video_stream(stream: dict[str, object]) -> StreamSummary:
    """Build a one-line video stream summary from ffprobe stream JSON."""
    index = int(stream.get("index", 0))
    codec = str(stream.get("codec_name") or "unknown")
    width = stream.get("width")
    height = stream.get("height")
    resolution = (
        f"{width}x{height}"
        if isinstance(width, int) and isinstance(height, int)
        else "unknown"
    )
    fps = _format_fps(str(stream.get("r_frame_rate") or stream.get("avg_frame_rate") or ""))
    pix_fmt = stream.get("pix_fmt")
    pixel_format = str(pix_fmt) if isinstance(pix_fmt, str) and pix_fmt else None
    color = _color_label(stream)  # type: ignore[arg-type]

    parts = [codec, resolution]
    if fps:
        parts.append(fps)
    if pixel_format:
        parts.append(pixel_format)
    if color:
        parts.append(color)

    field_order = stream.get("field_order")
    if isinstance(field_order, str) and field_order not in _SKIP_FIELD_ORDER:
        parts.append(f"field_order={field_order}")

    return StreamSummary(index=index, kind="video", codec=codec, details=", ".join(parts))


def _summarize_audio_stream(stream: dict[str, object]) -> StreamSummary:
    """Build a one-line audio stream summary from ffprobe stream JSON."""
    index = int(stream.get("index", 0))
    codec = str(stream.get("codec_name") or "unknown")
    channels = stream.get("channels")
    channel_count = str(channels) if isinstance(channels, int) else "?"
    layout = stream.get("channel_layout")
    layout_label = str(layout) if isinstance(layout, str) and layout else f"{channel_count}ch"
    sample_rate = stream.get("sample_rate")
    rate_label = f"{sample_rate} Hz" if sample_rate else "unknown rate"

    details = f"{codec} ({layout_label}, {rate_label})"
    return StreamSummary(index=index, kind="audio", codec=codec, details=details)


def parse_ffprobe_json(path: str, payload: dict[str, object]) -> MediaSummary:
    """Parse ffprobe JSON into a compact MediaSummary."""
    format_info = payload.get("format")
    format_dict = format_info if isinstance(format_info, dict) else {}
    container = str(format_dict.get("format_name") or "unknown")
    raw_duration = (
        str(format_dict.get("duration")) if format_dict.get("duration") is not None else None
    )
    duration_seconds: float | None = None
    if raw_duration is not None:
        try:
            duration_seconds = float(raw_duration)
        except ValueError:
            duration_seconds = None
    duration = _format_duration(raw_duration)

    streams_raw = payload.get("streams")
    stream_items = streams_raw if isinstance(streams_raw, list) else []
    summaries: list[StreamSummary] = []
    for item in stream_items:
        if not isinstance(item, dict):
            continue
        codec_type = item.get("codec_type")
        if codec_type == "video":
            summaries.append(_summarize_video_stream(item))
        elif codec_type == "audio":
            summaries.append(_summarize_audio_stream(item))

    return MediaSummary(
        path=path,
        container=container,
        duration=duration,
        duration_seconds=duration_seconds,
        streams=tuple(summaries),
    )


def run_ffprobe(
    path: str,
    *,
    ffprobe_bin: str = "ffprobe",
    timeout: float = FFPROBE_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Run ffprobe and return parsed JSON for one local file."""
    try:
        completed = subprocess.run(
            [
                ffprobe_bin,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                path,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise FfprobeError(
            "ffprobe was not found. Install FFmpeg and ensure ffprobe is on your PATH."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise FfprobeError(f"ffprobe timed out after {timeout:g}s for {path}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise FfprobeError(detail or f"ffprobe failed for {path}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise FfprobeError(f"Invalid ffprobe JSON for {path}") from exc
    if not isinstance(payload, dict):
        raise FfprobeError(f"Unexpected ffprobe payload for {path}")
    return payload


def format_media_summary(summary: MediaSummary) -> str:
    """Render one MediaSummary as a few compact lines for the model."""
    default_out = default_output_path(summary.path)
    lines = [
        f"Source: {summary.path}",
        f"  default output: {default_out}",
        f"  container: {summary.container}; duration: {summary.duration}",
    ]
    for stream in summary.streams:
        lines.append(f"  {stream.kind}[{stream.index}]: {stream.details}")
    if not summary.streams:
        lines.append("  streams: none detected")
    return "\n".join(lines)


def build_source_context(paths: list[str], *, ffprobe_bin: str | None = None) -> str | None:
    """Probe existing local files and return combined context for the model."""
    if not paths:
        return None

    binary = ffprobe_bin or shutil.which("ffprobe")
    if binary is None:
        return None

    blocks: list[str] = []
    for raw_path in paths:
        summary = probe_media_summary(raw_path, ffprobe_bin=binary)
        if summary is None:
            continue
        blocks.append(format_media_summary(summary))

    if not blocks:
        return None

    header = (
        "Verified source metadata (from ffprobe on the user's local files):\n"
        "Treat the probed file as the authoritative source. Never use the Source path as "
        "the output path — do not overwrite the input.\n"
        "When the user does not name an output destination, use the listed default output "
        "path exactly (same directory, stem + _out + extension). Only use a different path "
        "when the user specifies one. Change the extension only when the user explicitly "
        "requests a different container/format.\n"
        "Preserve every probed spec the user did not explicitly ask to change (codec, pixel "
        "format, color space, container, audio layout, etc.). Apply only the requested "
        "transforms.\n"
        "In EXPLANATION, state what was kept from the source and what was changed per the request."
    )
    return header + "\n\n" + "\n\n".join(blocks)
