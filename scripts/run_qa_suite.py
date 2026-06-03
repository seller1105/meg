"""Run roadmap G1–G15 and E1–E8 prompts; write results to docs/qa-run.json.

Requires ANTHROPIC_API_KEY or OPENAI_API_KEY in the environment.
See docs/STATUS.md for how to interpret results.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEG = ROOT / ".venv" / "Scripts" / "meg.exe"
OUT = ROOT / "docs" / "qa-run.json"

GENERATE: list[tuple[str, str]] = [
    ("G1", "Convert this MKV to H.264 MP4 with AAC audio"),
    ("G2", "Extract audio as 24-bit WAV"),
    ("G3", "Scale to 1920x1080, keep aspect ratio, pad with black"),
    ("G4", "Normalize loudness to -23 LUFS (broadcast)"),
    ("G5", "Concatenate three MP4s in order"),
    ("G6", "Remux only — copy all streams, change container to MP4"),
    ("G7", "Burn in SRT subtitles (hardcode)"),
    ("G8", "Extract frame at 00:01:30 as PNG"),
    ("G9", "Deinterlace 1080i to 1080p"),
    ("G10", "Package as ProRes 422 HQ MOV"),
    ("G11", "IMF-style deliverable (high level)"),
    ("G12", "H.264 CRF 18, slow preset, web streaming"),
    ("G13", "Strip all audio, keep video only"),
    ("G14", "Two audio tracks — map English AAC, copy video"),
    ("G15", "Crop center 16:9 from 4:3 source"),
]

EXPLAIN: list[tuple[str, str]] = [
    ("E1", "ffmpeg -i in.mp4 -vf scale=1920:1080 -c:v libx264 out.mp4"),
    ("E2", "ffmpeg -i in.mov -c copy out.mp4"),
    ("E3", "ffmpeg -i in.mp4 -vn -acodec pcm_s24le out.wav"),
    ("E4", "ffmpeg -f concat -safe 0 -i list.txt -c copy out.mp4"),
    (
        "E5",
        'ffmpeg -i in.mp4 -filter_complex "[0:v]yadif[outv]" -map "[outv]" -map 0:a -c:v libx264 -c:a copy out.mp4',
    ),
    ("E6", "ffmpeg -i in.mp4 -map 0:v:0 -map 0:a:1 -c copy out.mp4"),
    ("E7", "ffmpeg -i in.mp4 -af loudnorm=I=-23:TP=-1:LRA=7 out.mp4"),
    ("E8", "ffmpeg -i in.mp4 -hwaccel cuda -c:v h264_nvenc out.mp4"),
]


def _ensure_api_key() -> None:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY"):
        return
    try:
        import winreg  # type: ignore[import-untyped]

        key = winreg.QueryValueEx(
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment"),
            "ANTHROPIC_API_KEY",
        )[0]
        if key:
            os.environ["ANTHROPIC_API_KEY"] = key
    except OSError:
        pass


def _run_generate(case_id: str, prompt: str) -> dict[str, object]:
    start = time.perf_counter()
    proc = subprocess.run(
        [str(MEG), prompt],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed = round(time.perf_counter() - start, 2)
    return {
        "id": case_id,
        "mode": "generate",
        "input": prompt,
        "exit_code": proc.returncode,
        "elapsed_s": elapsed,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def _run_explain(case_id: str, command: str) -> dict[str, object]:
    start = time.perf_counter()
    proc = subprocess.run(
        [str(MEG), "--explain", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed = round(time.perf_counter() - start, 2)
    return {
        "id": case_id,
        "mode": "explain",
        "input": command,
        "exit_code": proc.returncode,
        "elapsed_s": elapsed,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def main() -> int:
    if not MEG.exists():
        print(f"meg not found at {MEG}", file=sys.stderr)
        return 1
    _ensure_api_key()
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        print("No API key in environment.", file=sys.stderr)
        return 1

    results: list[dict[str, object]] = []
    for case_id, prompt in GENERATE:
        print(f"Running {case_id} (generate)...", flush=True)
        results.append(_run_generate(case_id, prompt))
    for case_id, command in EXPLAIN:
        print(f"Running {case_id} (explain)...", flush=True)
        results.append(_run_explain(case_id, command))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Wrote {OUT}")
    failed = [r["id"] for r in results if r["exit_code"] != 0]
    if failed:
        print(f"Failures: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
