"""Pre-flight sanity checks for generated ffmpeg commands."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import os
import re
from pathlib import Path
from typing import Literal

from meg.exec import (
    CommandValidationError,
    _executable_name,
    _flag_consumes_next_arg,
    _is_flag,
    _resolve_output_paths,
    parse_command_line,
)
from meg.ffprobe import MediaSummary, probe_media_summary

Severity = Literal["error", "warning"]

_COPY_INTENT_PATTERN = re.compile(
    r"\b("
    r"remux|"
    r"stream\s+copy|"
    r"copy\s+(?:all\s+)?streams|"
    r"change\s+container|"
    r"container\s+only|"
    r"no\s+re-?encode|"
    r"don'?t\s+re-?encode|"
    r"without\s+re-?encod"
    r")\b",
    re.IGNORECASE,
)

_MAP_SPEC_PATTERN = re.compile(r"^(\d+):([vas])(?::(\d+))?$", re.IGNORECASE)

_REMOTE_INPUT_PREFIXES = ("http://", "https://", "rtmp://", "rtmps://", "udp://", "tcp://")


@dataclass(frozen=True)
class PreflightFinding:
    """One pre-flight issue detected before running ffmpeg."""

    severity: Severity
    code: str
    message: str


@dataclass(frozen=True)
class FfmpegStructure:
    """Relevant flags extracted from an ffmpeg argv sequence."""

    input_paths: tuple[str, ...]
    output_paths: tuple[str, ...]
    global_copy: bool
    video_copy: bool
    audio_copy: bool
    video_codec: str | None
    audio_codec: str | None
    has_video_filter: bool
    has_audio_filter: bool
    has_filter_complex: bool
    has_vn: bool
    has_an: bool
    map_specs: tuple[str, ...]
    missing_i_value: bool


def _is_local_input_path(path: str) -> bool:
    """Return True when a path should exist on disk before running."""
    lowered = path.lower()
    if path in {"-", "pipe:", "pipe:0", "pipe:1"}:
        return False
    if path.startswith("pipe:"):
        return False
    if lowered.startswith(_REMOTE_INPUT_PREFIXES):
        return False
    return True


def _normalize_path_key(path: str) -> str:
    try:
        resolved = str(Path(path).resolve(strict=False))
    except OSError:
        resolved = path
    return os.path.normcase(os.path.normpath(resolved))


def _should_verify_input_exists(
    path: str,
    validated_input_paths: Sequence[str],
) -> bool:
    """Return True when a missing -i path should be treated as an error."""
    if not validated_input_paths:
        return False
    normalized = _normalize_path_key(path)
    return any(_normalize_path_key(candidate) == normalized for candidate in validated_input_paths)


def _lookup_probe_summary(
    path: str,
    probed_sources: dict[str, MediaSummary],
) -> MediaSummary | None:
    if path in probed_sources:
        return probed_sources[path]
    try:
        resolved = str(Path(path).resolve(strict=False))
    except OSError:
        resolved = path
    normalized = Path(resolved)
    for key, summary in probed_sources.items():
        try:
            key_resolved = str(Path(key).resolve(strict=False))
        except OSError:
            key_resolved = key
        if Path(key_resolved) == normalized:
            return summary
    return None


def _inspect_ffmpeg_argv(argv: Sequence[str]) -> FfmpegStructure | None:
    """Walk argv and collect flags relevant to pre-flight checks."""
    if not argv or _executable_name(argv[0]) != "ffmpeg":
        return None

    inputs: list[str] = []
    positionals: list[str] = []
    map_specs: list[str] = []
    global_copy = False
    video_copy = False
    audio_copy = False
    video_codec: str | None = None
    audio_codec: str | None = None
    has_video_filter = False
    has_audio_filter = False
    has_filter_complex = False
    has_vn = False
    has_an = False
    missing_i_value = False
    index = 1

    while index < len(argv):
        token = argv[index]
        if token == "-i":
            if index + 1 >= len(argv):
                missing_i_value = True
                break
            inputs.append(argv[index + 1])
            index += 2
            continue
        if token in {"-vn", "-an"}:
            if token == "-vn":
                has_vn = True
            else:
                has_an = True
            index += 1
            continue
        if token in {"-vf", "-filter:v"}:
            has_video_filter = True
        if token in {"-af", "-filter:a"}:
            has_audio_filter = True
        if token.startswith("-filter_complex"):
            has_filter_complex = True
        if token == "-map":
            if index + 1 < len(argv):
                map_specs.append(argv[index + 1])
        if token in {"-c", "-codec"}:
            if index + 1 < len(argv):
                value = argv[index + 1]
                if value == "copy":
                    global_copy = True
                    video_copy = True
                    audio_copy = True
                    video_codec = "copy"
                    audio_codec = "copy"
                else:
                    video_codec = value
                    audio_codec = value
        if token in {"-c:v", "-vcodec"}:
            if index + 1 < len(argv):
                value = argv[index + 1]
                video_codec = value
                video_copy = value == "copy"
        if token in {"-c:a", "-acodec"}:
            if index + 1 < len(argv):
                value = argv[index + 1]
                audio_codec = value
                audio_copy = value == "copy"
        if _is_flag(token):
            next_token = argv[index + 1] if index + 1 < len(argv) else None
            if _flag_consumes_next_arg(token, next_token):
                index += 2
            else:
                index += 1
            continue
        positionals.append(token)
        index += 1

    output_paths, _multi_output = _resolve_output_paths(positionals)

    return FfmpegStructure(
        input_paths=tuple(inputs),
        output_paths=output_paths,
        global_copy=global_copy,
        video_copy=video_copy or global_copy,
        audio_copy=audio_copy or global_copy,
        video_codec=video_codec,
        audio_codec=audio_codec,
        has_video_filter=has_video_filter,
        has_audio_filter=has_audio_filter,
        has_filter_complex=has_filter_complex,
        has_vn=has_vn,
        has_an=has_an,
        map_specs=tuple(map_specs),
        missing_i_value=missing_i_value,
    )


def _user_wants_copy_only(user_request: str | None) -> bool:
    if not user_request:
        return False
    return _COPY_INTENT_PATTERN.search(user_request) is not None


def _video_reencodes(structure: FfmpegStructure) -> bool:
    if structure.has_vn:
        return False
    if structure.video_codec is not None:
        return structure.video_codec != "copy"
    if structure.video_copy:
        return False
    return structure.has_video_filter or structure.has_filter_complex


def _audio_reencodes(structure: FfmpegStructure) -> bool:
    if structure.has_an:
        return False
    if structure.audio_codec is not None:
        return structure.audio_codec != "copy"
    if structure.audio_copy:
        return False
    return structure.has_audio_filter or structure.has_filter_complex


def build_probed_sources(paths: Sequence[str]) -> dict[str, MediaSummary]:
    """Probe local media paths and return a lookup table for pre-flight checks."""
    sources: dict[str, MediaSummary] = {}
    for path in paths:
        summary = probe_media_summary(path)
        if summary is not None:
            sources[path] = summary
    return sources


def analyze_preflight(
    argv: Sequence[str],
    *,
    user_request: str | None = None,
    probed_sources: dict[str, MediaSummary] | None = None,
    validated_input_paths: Sequence[str] | None = None,
) -> tuple[PreflightFinding, ...]:
    """Return structural and intent findings for an ffmpeg argv sequence."""
    structure = _inspect_ffmpeg_argv(argv)
    if structure is None:
        return ()

    findings: list[PreflightFinding] = []
    sources = probed_sources or {}
    verified_inputs = validated_input_paths or ()

    if structure.missing_i_value:
        findings.append(
            PreflightFinding(
                severity="error",
                code="missing_input",
                message="-i is missing its input path.",
            )
        )

    for path in structure.input_paths:
        if not _is_local_input_path(path):
            continue
        if not _should_verify_input_exists(path, verified_inputs):
            continue
        if not Path(path).is_file():
            findings.append(
                PreflightFinding(
                    severity="error",
                    code="missing_input_file",
                    message=f"Input file not found: {path!r}.",
                )
            )

    if structure.has_video_filter and structure.video_copy:
        findings.append(
            PreflightFinding(
                severity="error",
                code="copy_with_video_filter",
                message=(
                    "Video stream copy (-c:v copy or -c copy) cannot be used with "
                    "video filters (-vf / -filter:v)."
                ),
            )
        )

    if structure.has_audio_filter and structure.audio_copy:
        findings.append(
            PreflightFinding(
                severity="error",
                code="copy_with_audio_filter",
                message=(
                    "Audio stream copy (-c:a copy or -c copy) cannot be used with "
                    "audio filters (-af / -filter:a)."
                ),
            )
        )

    if structure.has_filter_complex and (structure.global_copy or structure.video_copy):
        findings.append(
            PreflightFinding(
                severity="error",
                code="copy_with_filter_complex",
                message=(
                    "Stream copy cannot be combined with -filter_complex; filtered "
                    "streams must be re-encoded."
                ),
            )
        )

    if _user_wants_copy_only(user_request):
        if _video_reencodes(structure):
            findings.append(
                PreflightFinding(
                    severity="warning",
                    code="unexpected_video_reencode",
                    message=(
                        "The request looks like remux/stream copy, but the command "
                        "re-encodes video. Use -c copy or -c:v copy if that is intended."
                    ),
                )
            )
        if _audio_reencodes(structure):
            findings.append(
                PreflightFinding(
                    severity="warning",
                    code="unexpected_audio_reencode",
                    message=(
                        "The request looks like remux/stream copy, but the command "
                        "re-encodes audio. Use -c copy or -c:a copy if that is intended."
                    ),
                )
            )

    for map_spec in structure.map_specs:
        if map_spec.startswith("["):
            continue
        match = _MAP_SPEC_PATTERN.match(map_spec)
        if match is None:
            continue
        input_index = int(match.group(1))
        stream_kind = match.group(2).lower()
        stream_index = int(match.group(3) or "0")
        if input_index >= len(structure.input_paths):
            continue
        input_path = structure.input_paths[input_index]
        summary = _lookup_probe_summary(input_path, sources)
        if summary is None:
            continue
        if stream_kind == "v":
            available = sum(1 for stream in summary.streams if stream.kind == "video")
            kind_label = "video"
        elif stream_kind == "a":
            available = sum(1 for stream in summary.streams if stream.kind == "audio")
            kind_label = "audio"
        else:
            continue
        if stream_index >= available:
            findings.append(
                PreflightFinding(
                    severity="error",
                    code="invalid_map",
                    message=(
                        f"-map {map_spec} is out of range for {input_path!r} "
                        f"(only {available} {kind_label} stream(s) detected)."
                    ),
                )
            )

    return tuple(findings)


def preflight_from_command(
    command: str,
    *,
    user_request: str | None = None,
    probed_paths: Sequence[str] | None = None,
) -> tuple[PreflightFinding, ...]:
    """Parse a command string and run pre-flight analysis."""
    try:
        parsed = parse_command_line(command)
    except CommandValidationError:
        return ()
    sources = build_probed_sources(probed_paths or ())
    validated = tuple(probed_paths or ())
    return analyze_preflight(
        parsed.argv,
        user_request=user_request,
        probed_sources=sources,
        validated_input_paths=validated,
    )


def has_preflight_errors(findings: Sequence[PreflightFinding]) -> bool:
    """Return True when any finding blocks execution."""
    return any(finding.severity == "error" for finding in findings)
