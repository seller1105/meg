"""Synthesize small test media files for the path-based QA suite.

Creates reproducible clips (color bars + sine audio) in ``.qa-media/`` at the
repo root — nothing is committed to git. Requires ffmpeg on PATH. Files are
only rebuilt when missing.

Run directly (``python scripts/make_qa_media.py``) or via run_qa_suite.py.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEDIA_DIR = ROOT / ".qa-media"

_DURATION = "3"


def media_specs() -> list[tuple[Path, list[str]]]:
    """(output path, ffmpeg argv) for each synthetic QA file.

    - ``qa master 1080p25.mov`` — ProRes 422 yuv422p10le + PCM audio, and a
      space in the filename to exercise Windows quoting end to end.
    - ``clip_720p2398.mp4`` — H.264 yuv420p at 24000/1001 fps + AAC.
    - ``two_audio.mkv`` — one video and two distinct audio tracks for
      mapping prompts.
    """
    master = MEDIA_DIR / "qa master 1080p25.mov"
    clip = MEDIA_DIR / "clip_720p2398.mp4"
    two_audio = MEDIA_DIR / "two_audio.mkv"

    return [
        (
            master,
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate=25:duration={_DURATION}",
                "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={_DURATION}",
                "-c:v", "prores_ks", "-profile:v", "2", "-pix_fmt", "yuv422p10le",
                "-c:a", "pcm_s16le", "-ac", "2",
                str(master),
            ],
        ),
        (
            clip,
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=24000/1001:duration={_DURATION}",
                "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={_DURATION}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                str(clip),
            ],
        ),
        (
            two_audio,
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=25:duration={_DURATION}",
                "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={_DURATION}",
                "-f", "lavfi", "-i", f"sine=frequency=880:sample_rate=48000:duration={_DURATION}",
                "-map", "0:v:0", "-map", "1:a:0", "-map", "2:a:0",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "96k",
                str(two_audio),
            ],
        ),
    ]


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def ensure_qa_media(*, quiet: bool = False) -> list[Path] | None:
    """Create any missing QA media files. Returns paths, or None if no ffmpeg."""
    if not ffmpeg_available():
        if not quiet:
            print("ffmpeg not found on PATH; cannot build QA media.", file=sys.stderr)
        return None

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for path, argv in media_specs():
        if not path.exists():
            if not quiet:
                print(f"Building {path.name}...", flush=True)
            proc = subprocess.run(argv, capture_output=True, text=True)
            if proc.returncode != 0 or not path.exists():
                if not quiet:
                    tail = (proc.stderr or "").strip().splitlines()[-3:]
                    print(
                        f"Failed to build {path.name}: {' | '.join(tail)}",
                        file=sys.stderr,
                    )
                return None
        paths.append(path)
    return paths


if __name__ == "__main__":
    raise SystemExit(0 if ensure_qa_media() else 1)
